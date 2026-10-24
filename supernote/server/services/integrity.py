import logging

from sqlalchemy import select

from supernote.models.system import IntegrityIssueVO, IntegrityReportVO
from supernote.server.constants import USER_DATA_BUCKET
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.blob import BlobStorage

logger = logging.getLogger(__name__)

IntegrityIssue = IntegrityIssueVO
IntegrityReport = IntegrityReportVO


class IntegrityService:
    """Service to verify data consistency between VFS and BlobStorage."""

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
            # Query all active files AND folders for this user
            # We need folders to verify parent logic
            stmt = select(UserFileDO).where(
                UserFileDO.user_id == user_id,
                UserFileDO.is_active == "Y",
            )
            result = await session.execute(stmt)
            all_nodes = result.scalars().all()

            # Build a set of valid directory IDs (Root 0 is always valid)
            valid_dirs = {0}
            for node in all_nodes:
                if node.is_folder == "Y":
                    valid_dirs.add(node.id)

            for node in all_nodes:
                if file_id is not None and node.id != file_id:
                    continue

                report.scanned += 1

                # Check 1: Orphan Check (Parent Validity)
                if node.directory_id not in valid_dirs:
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
                            issue_type="orphan",
                            detail=detail,
                        )
                    )
                    continue

                if node.is_folder == "Y":
                    report.ok += 1
                    continue

                # Check 2: Blob Integrity (Existence, Size, MD5)
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
                            issue_type="missing_blob",
                            detail=detail,
                        )
                    )
                    continue

                try:
                    metadata = await self.blob_storage.get_metadata(
                        USER_DATA_BUCKET, node.storage_key, include_md5=True
                    )
                except FileNotFoundError:
                    detail = f"Missing blob key {node.storage_key}"
                    logger.error(
                        f"Integrity Fail: File {node.id} ({node.file_name}) {detail}"
                    )
                    report.missing_blob += 1
                    report.issues.append(
                        IntegrityIssueVO(
                            user_id=user_id,
                            file_id=node.id,
                            file_name=node.file_name,
                            issue_type="missing_blob",
                            detail=detail,
                        )
                    )
                    continue

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
                            issue_type="size_mismatch",
                            detail=detail,
                        )
                    )
                    continue

                if (
                    node.md5
                    and metadata.content_md5
                    and node.md5 != metadata.content_md5
                ):
                    detail = (
                        f"MD5 mismatch (VFS: {node.md5}, Blob: {metadata.content_md5})"
                    )
                    logger.error(
                        f"Integrity Fail: File {node.id} hash mismatch. VFS: {node.md5}, Blob: {metadata.content_md5}"
                    )
                    report.hash_mismatch += 1
                    report.issues.append(
                        IntegrityIssueVO(
                            user_id=user_id,
                            file_id=node.id,
                            file_name=node.file_name,
                            issue_type="hash_mismatch",
                            detail=detail,
                        )
                    )
                    continue

                report.ok += 1

        return report
