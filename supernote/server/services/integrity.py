import logging
from collections import defaultdict

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from supernote.models.base import ProcessingStatus, TaskType
from supernote.models.system import (
    IntegrityIssueType,
    IntegrityIssueVO,
    IntegrityReportVO,
)
from supernote.server.constants import CACHE_BUCKET, USER_DATA_BUCKET
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.note_processing import NotePageContentDO, SystemTaskDO
from supernote.server.db.models.summary import SummaryDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.blob import BlobStorage
from supernote.server.utils.paths import get_page_png_path

logger = logging.getLogger(__name__)

IntegrityIssue = IntegrityIssueVO
IntegrityReport = IntegrityReportVO

_GITHUB_ISSUES_URL = "https://github.com/allenporter/supernote/issues"

_REMEDIATION_HINTS: dict[IntegrityIssueType, str] = {
    IntegrityIssueType.MISSING_CACHE_BLOB: (
        "Run 'supernote admin reprocess --type png' to regenerate missing PNG cache images."
    ),
    IntegrityIssueType.MISSING_PAGE_CONTENT: (
        "Run 'supernote admin reprocess --type ocr' to re-extract missing page text. "
        f"This is an unexpected server state; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
    IntegrityIssueType.MISSING_PAGE_EMBEDDING: (
        "Run 'supernote admin reprocess --type embedding' to regenerate missing page embeddings. "
        f"This is an unexpected server state; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
    IntegrityIssueType.TASK_FAILED: (
        "Run 'supernote admin tasks --status failed' to inspect task errors, then "
        "'supernote admin reprocess --type all --file-id <ID>' to retry."
    ),
    IntegrityIssueType.MISSING_BLOB: (
        "Re-upload or re-sync affected files from a Supernote device or restore missing blobs from backup."
    ),
    IntegrityIssueType.SIZE_MISMATCH: (
        "Re-upload or re-sync affected files from a Supernote device or restore blobs from backup. "
        f"This is an unexpected server state; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
    IntegrityIssueType.HASH_MISMATCH: (
        "Re-upload or re-sync affected files from a Supernote device or restore blobs from backup. "
        f"This is an unexpected server state; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
    IntegrityIssueType.ORPHAN: (
        "Orphaned VFS nodes have an invalid parent folder. "
        f"This is an unexpected server bug; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
    IntegrityIssueType.DUPLICATE_ROOT_FOLDER: (
        "Duplicate case-variant root folders were detected. "
        f"This is an unexpected server bug; please report an issue at {_GITHUB_ISSUES_URL}."
    ),
}


class IntegrityService:
    """Service to verify data consistency across VFS, BlobStorage, and Processing Pipeline."""

    def __init__(
        self, session_manager: DatabaseSessionManager, blob_storage: BlobStorage
    ) -> None:
        """Create an integrity service instance."""
        self.session_manager = session_manager
        self.blob_storage = blob_storage

    async def verify_user_storage(
        self,
        user_id: int,
        file_id: int | None = None,
    ) -> IntegrityReportVO:
        """Check all files (or a single file) for a user."""
        report = IntegrityReportVO()

        async with self.session_manager.session() as session:
            nodes_with_parent_ok = await self._query_nodes_with_parent_status(
                session, user_id, file_id
            )
            duplicate_root_folders = await self._query_duplicate_root_folders(
                session, user_id, file_id
            )
            db_issues_by_file = await self._query_pipeline_db_issues(
                session, user_id, file_id
            )
            png_keys_by_file = await self._query_completed_png_keys(
                session, user_id, file_id
            )
            summaries_with_status = await self._query_summaries_with_status(
                session, user_id, file_id
            )

        for node, has_valid_parent in nodes_with_parent_ok:
            report.scanned += 1

            if not has_valid_parent:
                detail = f"Invalid parent directory_id={node.directory_id}"
                logger.error(
                    f"Integrity Fail: Node {node.id} ({node.file_name}) has invalid parent {node.directory_id}"
                )
                report.orphans += 1
                report.issues.append(
                    IntegrityIssueVO(
                        user_id=user_id,
                        file_id=node.id,
                        file_name=node.file_name,
                        issue_type=IntegrityIssueType.ORPHAN,
                        detail=detail,
                    )
                )
                continue

            if node.is_folder == "Y":
                if node.id in duplicate_root_folders:
                    detail = duplicate_root_folders[node.id]
                    logger.warning(
                        f"Integrity Warning: Folder {node.id} ({node.file_name}) {detail}"
                    )
                    report.folder_anomalies += 1
                    report.issues.append(
                        IntegrityIssueVO(
                            user_id=user_id,
                            file_id=node.id,
                            file_name=node.file_name,
                            issue_type=IntegrityIssueType.DUPLICATE_ROOT_FOLDER,
                            detail=detail,
                        )
                    )
                    continue
                report.ok += 1
                continue

            if not await self._check_blob_integrity(user_id, node, report):
                continue

            if node.file_name.lower().endswith(".note"):
                if not await self._check_note_pipeline(
                    user_id,
                    node,
                    db_issues_by_file.get(node.id, []),
                    png_keys_by_file.get(node.id, []),
                    report,
                ):
                    continue

            report.ok += 1

        for summary, has_valid_parent, has_valid_file in summaries_with_status:
            report.scanned += 1
            summary_name = (
                summary.name or summary.source_path or summary.unique_identifier
            )
            issue_file_id = (
                summary.file_id if summary.file_id is not None else summary.id
            )

            if not has_valid_parent:
                detail = f"Summary {summary.id} has invalid parent group {summary.parent_unique_identifier}"
                logger.error(
                    f"Integrity Fail: Summary {summary.id} ({summary_name}) has invalid parent {summary.parent_unique_identifier}"
                )
                report.orphans += 1
                report.issues.append(
                    IntegrityIssueVO(
                        user_id=user_id,
                        file_id=issue_file_id,
                        file_name=summary_name,
                        issue_type=IntegrityIssueType.ORPHAN,
                        detail=detail,
                    )
                )
                continue

            if not has_valid_file:
                detail = f"Summary {summary.id} has invalid file_id={summary.file_id}"
                logger.error(
                    f"Integrity Fail: Summary {summary.id} ({summary_name}) has invalid file_id {summary.file_id}"
                )
                report.orphans += 1
                report.issues.append(
                    IntegrityIssueVO(
                        user_id=user_id,
                        file_id=issue_file_id,
                        file_name=summary_name,
                        issue_type=IntegrityIssueType.ORPHAN,
                        detail=detail,
                    )
                )
                continue

            if not await self._check_summary_blob_integrity(user_id, summary, report):
                continue

            report.ok += 1

        report.remediations = list(
            dict.fromkeys(
                _REMEDIATION_HINTS[issue.issue_type]
                for issue in report.issues
                if issue.issue_type in _REMEDIATION_HINTS
            )
        )
        return report

    async def _query_nodes_with_parent_status(
        self,
        session: AsyncSession,
        user_id: int,
        file_id: int | None,
    ) -> list[tuple[UserFileDO, bool]]:
        """Query target VFS nodes along with whether their parent directory is valid."""
        parent_folder = aliased(UserFileDO)
        stmt = (
            select(
                UserFileDO,
                # Parent is valid if node is at root (0) or joined active folder exists
                or_(
                    UserFileDO.directory_id == 0,
                    parent_folder.id.is_not(None),
                ),
            )
            .outerjoin(
                parent_folder,
                # Match parent folder owned by the same user that is active and a folder
                and_(
                    parent_folder.id == UserFileDO.directory_id,
                    parent_folder.user_id == user_id,
                    parent_folder.is_active == "Y",
                    parent_folder.is_folder == "Y",
                ),
            )
            .where(
                # Scope to active VFS nodes owned by this user
                UserFileDO.user_id == user_id,
                UserFileDO.is_active == "Y",
            )
        )
        if file_id is not None:
            # Restrict to a single file/folder node when file_id is specified
            stmt = stmt.where(UserFileDO.id == file_id)

        rows = (await session.execute(stmt)).all()
        return [(node, bool(has_valid_parent)) for node, has_valid_parent in rows]

    async def _query_duplicate_root_folders(
        self,
        session: AsyncSession,
        user_id: int,
        file_id: int | None,
    ) -> dict[int, str]:
        """Query non-uppercase root folders that conflict with a sibling root folder."""
        sibling_folder = aliased(UserFileDO)
        stmt = (
            select(
                UserFileDO.id,
                UserFileDO.file_name,
                sibling_folder.file_name,
            )
            .join(
                sibling_folder,
                # Match another active root folder owned by the same user with the same case-insensitive name
                and_(
                    sibling_folder.user_id == UserFileDO.user_id,
                    sibling_folder.directory_id == 0,
                    sibling_folder.is_active == "Y",
                    sibling_folder.is_folder == "Y",
                    sibling_folder.id != UserFileDO.id,
                    func.upper(sibling_folder.file_name)
                    == func.upper(UserFileDO.file_name),
                ),
            )
            .where(
                # Scope to non-uppercase active root folders owned by this user
                UserFileDO.user_id == user_id,
                UserFileDO.directory_id == 0,
                UserFileDO.is_active == "Y",
                UserFileDO.is_folder == "Y",
                UserFileDO.file_name != func.upper(UserFileDO.file_name),
            )
        )
        if file_id is not None:
            # Restrict to a single folder node when file_id is specified
            stmt = stmt.where(UserFileDO.id == file_id)

        anomalies: dict[int, str] = {}
        for folder_id, name, sibling_name in (await session.execute(stmt)).all():
            names = ", ".join(sorted({name, sibling_name}))
            anomalies[folder_id] = f"Duplicate case-variant root folders exist: {names}"
        return anomalies

    async def _check_blob_integrity(
        self,
        user_id: int,
        node: UserFileDO,
        report: IntegrityReportVO,
    ) -> bool:
        """Verify physical blob existence, size, and MD5 checksum for a file node."""
        if not node.storage_key:
            detail = "File missing storage key in VFS"
            logger.error(
                f"Integrity Fail: File {node.id} ({node.file_name}) missing storage key"
            )
            report.missing_blob += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=node.id,
                    file_name=node.file_name,
                    issue_type=IntegrityIssueType.MISSING_BLOB,
                    detail=detail,
                )
            )
            return False

        try:
            metadata = await self.blob_storage.get_metadata(
                USER_DATA_BUCKET, node.storage_key, include_md5=True
            )
        except FileNotFoundError:
            detail = f"Missing blob key {node.storage_key}"
            logger.error(f"Integrity Fail: File {node.id} ({node.file_name}) {detail}")
            report.missing_blob += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=node.id,
                    file_name=node.file_name,
                    issue_type=IntegrityIssueType.MISSING_BLOB,
                    detail=detail,
                )
            )
            return False

        if metadata.size != node.size:
            detail = f"Size mismatch (VFS: {node.size}, Blob: {metadata.size})"
            logger.warning(
                f"Integrity Warning: File {node.id} size mismatch. VFS: {node.size}, Blob: {metadata.size}"
            )
            report.size_mismatch += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=node.id,
                    file_name=node.file_name,
                    issue_type=IntegrityIssueType.SIZE_MISMATCH,
                    detail=detail,
                )
            )
            return False

        if node.md5 and metadata.content_md5 and node.md5 != metadata.content_md5:
            detail = f"MD5 mismatch (VFS: {node.md5}, Blob: {metadata.content_md5})"
            logger.error(
                f"Integrity Fail: File {node.id} hash mismatch. VFS: {node.md5}, Blob: {metadata.content_md5}"
            )
            report.hash_mismatch += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=node.id,
                    file_name=node.file_name,
                    issue_type=IntegrityIssueType.HASH_MISMATCH,
                    detail=detail,
                )
            )
            return False

        return True

    async def _query_pipeline_db_issues(
        self,
        session: AsyncSession,
        user_id: int,
        file_id: int | None,
    ) -> dict[int, list[tuple[IntegrityIssueType, str]]]:
        """Query failed tasks and completed OCR/embedding tasks missing DB page content."""
        # Scope to active, non-folder files owned by this user (and optional file_id)
        note_filters = [
            UserFileDO.user_id == user_id,
            UserFileDO.is_active == "Y",
            UserFileDO.is_folder == "N",
        ]
        if file_id is not None:
            note_filters.append(UserFileDO.id == file_id)

        stmt = (
            select(
                SystemTaskDO.file_id,
                SystemTaskDO.task_type,
                SystemTaskDO.key,
                SystemTaskDO.status,
                SystemTaskDO.last_error,
            )
            .join(UserFileDO, UserFileDO.id == SystemTaskDO.file_id)
            .outerjoin(
                NotePageContentDO,
                and_(
                    NotePageContentDO.file_id == SystemTaskDO.file_id,
                    NotePageContentDO.page_id == SystemTaskDO.key,
                ),
            )
            .where(
                *note_filters,
                or_(
                    # 1. Any processing task that ended in FAILED status
                    SystemTaskDO.status == ProcessingStatus.FAILED,
                    # 2. Completed OCR task whose page row or extracted text is missing
                    and_(
                        SystemTaskDO.status == ProcessingStatus.COMPLETED,
                        SystemTaskDO.task_type == TaskType.OCR_EXTRACTION.value,
                        or_(
                            NotePageContentDO.id.is_(None),
                            NotePageContentDO.text_content.is_(None),
                        ),
                    ),
                    # 3. Completed embedding task whose page row or vector embedding is missing
                    and_(
                        SystemTaskDO.status == ProcessingStatus.COMPLETED,
                        SystemTaskDO.task_type == TaskType.EMBEDDING_GENERATION.value,
                        or_(
                            NotePageContentDO.id.is_(None),
                            NotePageContentDO.embedding.is_(None),
                        ),
                    ),
                ),
            )
        )

        issues_by_file: dict[int, list[tuple[IntegrityIssueType, str]]] = defaultdict(
            list
        )
        for fid, t_type, t_key, t_status, t_err in (await session.execute(stmt)).all():
            if t_status == ProcessingStatus.FAILED:
                issues_by_file[fid].append(
                    (
                        IntegrityIssueType.TASK_FAILED,
                        f"Task {t_type} ({t_key}) failed: {t_err or 'unknown error'}",
                    )
                )
            elif t_type == TaskType.OCR_EXTRACTION.value:
                issues_by_file[fid].append(
                    (
                        IntegrityIssueType.MISSING_PAGE_CONTENT,
                        f"Completed OCR task ({t_key}) missing page text content",
                    )
                )
            elif t_type == TaskType.EMBEDDING_GENERATION.value:
                issues_by_file[fid].append(
                    (
                        IntegrityIssueType.MISSING_PAGE_EMBEDDING,
                        f"Completed EMBEDDING task ({t_key}) missing page embedding",
                    )
                )
        return issues_by_file

    async def _query_completed_png_keys(
        self,
        session: AsyncSession,
        user_id: int,
        file_id: int | None,
    ) -> dict[int, list[str]]:
        """Query completed PNG_CONVERSION task keys per file ID."""
        # Scope to active, non-folder files owned by this user (and optional file_id)
        note_filters = [
            UserFileDO.user_id == user_id,
            UserFileDO.is_active == "Y",
            UserFileDO.is_folder == "N",
        ]
        if file_id is not None:
            note_filters.append(UserFileDO.id == file_id)

        stmt = (
            select(SystemTaskDO.file_id, SystemTaskDO.key)
            .join(UserFileDO, UserFileDO.id == SystemTaskDO.file_id)
            .where(
                *note_filters,
                # Completed PNG_CONVERSION tasks whose rendered PNG should exist in CACHE_BUCKET
                SystemTaskDO.status == ProcessingStatus.COMPLETED,
                SystemTaskDO.task_type == TaskType.PNG_CONVERSION.value,
            )
        )
        keys_by_file: dict[int, list[str]] = defaultdict(list)
        for fid, t_key in (await session.execute(stmt)).all():
            keys_by_file[fid].append(t_key)
        return keys_by_file

    async def _check_note_pipeline(
        self,
        user_id: int,
        node: UserFileDO,
        db_issues: list[tuple[IntegrityIssueType, str]],
        png_keys: list[str],
        report: IntegrityReportVO,
    ) -> bool:
        """Verify processing tasks and cache artifacts for a .note file."""
        passed = True

        for issue_type, detail in db_issues:
            passed = False
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=node.id,
                    file_name=node.file_name,
                    issue_type=issue_type,
                    detail=detail,
                )
            )

        for png_key in png_keys:
            png_path = get_page_png_path(node.id, png_key)
            if not await self.blob_storage.exists(CACHE_BUCKET, png_path):
                passed = False
                report.issues.append(
                    IntegrityIssueVO(
                        user_id=user_id,
                        file_id=node.id,
                        file_name=node.file_name,
                        issue_type=IntegrityIssueType.MISSING_CACHE_BLOB,
                        detail=f"Completed PNG task ({png_key}) missing cache blob {png_path}",
                    )
                )

        if not passed:
            report.pipeline_issues += 1
        return passed

    async def _query_summaries_with_status(
        self,
        session: AsyncSession,
        user_id: int,
        file_id: int | None,
    ) -> list[tuple[SummaryDO, bool, bool]]:
        """Query active summaries along with whether their parent group and source file are valid."""
        parent_group = aliased(SummaryDO)
        source_file = aliased(UserFileDO)
        stmt = (
            select(
                SummaryDO,
                or_(
                    SummaryDO.parent_unique_identifier.is_(None),
                    SummaryDO.parent_unique_identifier == "",
                    parent_group.id.is_not(None),
                ),
                or_(
                    SummaryDO.file_id.is_(None),
                    source_file.id.is_not(None),
                ),
            )
            .outerjoin(
                parent_group,
                and_(
                    parent_group.unique_identifier
                    == SummaryDO.parent_unique_identifier,
                    parent_group.user_id == user_id,
                    parent_group.is_deleted.is_(False),
                    parent_group.is_summary_group.is_(True),
                ),
            )
            .outerjoin(
                source_file,
                and_(
                    source_file.id == SummaryDO.file_id,
                    source_file.user_id == user_id,
                    source_file.is_active == "Y",
                    source_file.is_folder == "N",
                ),
            )
            .where(
                SummaryDO.user_id == user_id,
                SummaryDO.is_deleted.is_(False),
            )
        )
        if file_id is not None:
            stmt = stmt.where(SummaryDO.file_id == file_id)

        rows = (await session.execute(stmt)).all()
        return [
            (summary, bool(has_valid_parent), bool(has_valid_file))
            for summary, has_valid_parent, has_valid_file in rows
        ]

    async def _check_summary_blob_integrity(
        self,
        user_id: int,
        summary: SummaryDO,
        report: IntegrityReportVO,
    ) -> bool:
        """Verify physical handwriting blob existence and MD5 checksum for a summary."""
        summary_name = summary.name or summary.source_path or summary.unique_identifier
        issue_file_id = summary.file_id if summary.file_id is not None else summary.id

        if not summary.handwrite_inner_name:
            if summary.handwrite_md5:
                detail = f"Summary {summary.id} missing handwrite_inner_name"
                logger.error(
                    f"Integrity Fail: Summary {summary.id} ({summary_name}) missing handwrite_inner_name"
                )
                report.missing_blob += 1
                report.issues.append(
                    IntegrityIssueVO(
                        user_id=user_id,
                        file_id=issue_file_id,
                        file_name=summary_name,
                        issue_type=IntegrityIssueType.MISSING_BLOB,
                        detail=detail,
                    )
                )
                return False
            return True

        try:
            metadata = await self.blob_storage.get_metadata(
                USER_DATA_BUCKET, summary.handwrite_inner_name, include_md5=True
            )
        except FileNotFoundError:
            detail = (
                f"Summary {summary.id} missing blob key {summary.handwrite_inner_name}"
            )
            logger.error(
                f"Integrity Fail: Summary {summary.id} ({summary_name}) {detail}"
            )
            report.missing_blob += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=issue_file_id,
                    file_name=summary_name,
                    issue_type=IntegrityIssueType.MISSING_BLOB,
                    detail=detail,
                )
            )
            return False

        if (
            summary.handwrite_md5
            and metadata.content_md5
            and summary.handwrite_md5 != metadata.content_md5
        ):
            detail = (
                f"Summary {summary.id} MD5 mismatch "
                f"(Summary: {summary.handwrite_md5}, Blob: {metadata.content_md5})"
            )
            logger.error(
                f"Integrity Fail: Summary {summary.id} hash mismatch. Summary: {summary.handwrite_md5}, Blob: {metadata.content_md5}"
            )
            report.hash_mismatch += 1
            report.issues.append(
                IntegrityIssueVO(
                    user_id=user_id,
                    file_id=issue_file_id,
                    file_name=summary_name,
                    issue_type=IntegrityIssueType.HASH_MISMATCH,
                    detail=detail,
                )
            )
            return False

        return True
