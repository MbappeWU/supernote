import hashlib
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from supernote.client.device import DeviceClient
from supernote.models.base import ProcessingStatus, TaskType
from supernote.models.system import IntegrityIssueType
from supernote.server.constants import CACHE_BUCKET, USER_DATA_BUCKET
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.note_processing import NotePageContentDO, SystemTaskDO
from supernote.server.db.models.summary import SummaryDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.blob import BlobStorage, LocalBlobStorage
from supernote.server.services.integrity import _REMEDIATION_HINTS, IntegrityService
from supernote.server.services.user import UserService
from supernote.server.utils.paths import get_page_png_path


@pytest.fixture
def integrity_service(
    session_manager: DatabaseSessionManager, blob_storage: BlobStorage
) -> IntegrityService:
    """Fixture for IntegrityService."""
    return IntegrityService(session_manager, blob_storage)


async def test_integrity_check(
    device_client: DeviceClient,
    session_manager: DatabaseSessionManager,
    blob_storage: BlobStorage,
    user_service: UserService,
    integrity_service: IntegrityService,
) -> None:
    # Setup Data
    user = "test@example.com"
    await device_client.create_folder(path="/Docs", equipment_no="test")

    # Create the correct file
    await device_client.upload_content("Docs/good.txt", b"content", equipment_no="test")

    # Corrupt Data (Simulate missing blob)
    # Query VFS for the storage key.
    user_id = await user_service.get_user_id(user)
    async with session_manager.session() as session:
        result = await session.execute(
            select(UserFileDO).where(
                UserFileDO.user_id == user_id, UserFileDO.file_name == "good.txt"
            )
        )
        good_file = result.scalars().first()
        assert good_file
        storage_key = good_file.storage_key
        assert storage_key

    blob_path = blob_storage.get_blob_path(USER_DATA_BUCKET, storage_key)
    assert blob_path.exists()
    blob_path.unlink()  # Delete physical blob

    # Corrupt Data (Simulate size mismatch)
    await device_client.upload_content(
        "Docs/bad_size.txt", b"content2", equipment_no="test"
    )

    # Manually update VFS size to be wrong
    async with session_manager.session() as session:
        stmt = (
            update(UserFileDO)
            .where(
                UserFileDO.user_id == user_id, UserFileDO.file_name == "bad_size.txt"
            )
            .values(size=99999)
        )
        await session.execute(stmt)
        await session.commit()

    # Run Check
    report = await integrity_service.verify_user_storage(user_id)

    # Expect:
    # "content" md5 deleted.
    # "content2" md5 exists but size mismatch.
    # No orphans in this scenario
    # Scanned: 3 (Docs folder + good.txt + bad_size.txt)
    # Docs folder: OK
    # good.txt: missing blob
    # bad_size.txt: size mismatch

    # Scanned: 8 (default folders) + Docs + good + bad_size = 11
    assert report.scanned == 11
    assert report.ok == 9  # Some default folders are ok
    assert report.issue_counts == {
        IntegrityIssueType.MISSING_BLOB: 1,
        IntegrityIssueType.SIZE_MISMATCH: 1,
    }
    assert {i.file_name for i in report.issues} == {"good.txt", "bad_size.txt"}
    assert len(report.remediations) == 2

    file_report = await integrity_service.verify_user_storage(
        user_id, file_id=good_file.id
    )
    assert file_report.scanned == 1
    assert file_report.issue_counts == {IntegrityIssueType.MISSING_BLOB: 1}
    assert len(file_report.issues) == 1
    assert file_report.issues[0].file_id == good_file.id


async def test_integrity_orphans(
    integrity_service: IntegrityService,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify integrity check detects orphaned files."""
    # Create an orphaned VFS entry for the default test user
    file_do = UserFileDO(
        user_id=test_user_id,
        file_name="orphan.txt",
        is_folder="N",
        directory_id=9999,  # Invalid
        is_active="Y",
    )
    db_session.add(file_do)
    await db_session.commit()

    report = await integrity_service.verify_user_storage(test_user_id)
    assert report.issue_counts == {IntegrityIssueType.ORPHAN: 1}


async def test_integrity_hash_mismatch(
    integrity_service: IntegrityService,
    blob_storage: LocalBlobStorage,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify integrity check detects hash mismatch."""
    content = b"content"

    # Create a new blob
    key = str(uuid.uuid4())
    await blob_storage.put(USER_DATA_BUCKET, key, content)

    # Create VFS entry with WRONG MD5
    bad_md5 = "00000000000000000000000000000000"
    file_do = UserFileDO(
        user_id=test_user_id,
        file_name="bad_hash.txt",
        is_folder="N",
        directory_id=0,
        size=len(content),
        md5=bad_md5,
        storage_key=key,
        is_active="Y",
    )
    db_session.add(file_do)
    await db_session.commit()

    # Verify
    report = await integrity_service.verify_user_storage(test_user_id)
    assert report.issue_counts == {IntegrityIssueType.HASH_MISMATCH: 1}
    # 8 Default folders are OK
    assert report.ok == 8
    # Scanned: 8 (default folders) + bad_hash.txt = 9
    assert report.scanned == 9


async def test_integrity_basic(
    integrity_service: IntegrityService,
    blob_storage: LocalBlobStorage,
    test_user_id: int,
) -> None:
    """Verify basic integrity check with no issues."""
    report = await integrity_service.verify_user_storage(test_user_id)

    assert report.issues == []
    assert report.issue_counts == {}
    assert report.scanned == 8  # Default folders
    assert report.ok == 8


async def test_integrity_pipeline_issues(
    integrity_service: IntegrityService,
    blob_storage: LocalBlobStorage,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify note processing pipeline and cache artifact checks."""
    content = b"note-bytes"
    key = str(uuid.uuid4())
    meta = await blob_storage.put(USER_DATA_BUCKET, key, content)
    note_do = UserFileDO(
        user_id=test_user_id,
        file_name="broken_pipeline.note",
        is_folder="N",
        directory_id=0,
        size=len(content),
        md5=meta.content_md5,
        storage_key=key,
        is_active="Y",
    )
    db_session.add(note_do)
    await db_session.flush()

    db_session.add_all(
        [
            SystemTaskDO(
                file_id=note_do.id,
                task_type=TaskType.SUMMARY_GENERATION.value,
                key="global",
                status=ProcessingStatus.FAILED,
                last_error="Gemini quota exceeded",
            ),
            SystemTaskDO(
                file_id=note_do.id,
                task_type=TaskType.PNG_CONVERSION.value,
                key="page_P1",
                status=ProcessingStatus.COMPLETED,
            ),
            SystemTaskDO(
                file_id=note_do.id,
                task_type=TaskType.OCR_EXTRACTION.value,
                key="page_P1",
                status=ProcessingStatus.COMPLETED,
            ),
            NotePageContentDO(
                file_id=note_do.id,
                page_index=0,
                page_id="P1",
                text_content=None,
            ),
        ]
    )
    await db_session.commit()

    file_report = await integrity_service.verify_user_storage(
        test_user_id, file_id=note_do.id
    )
    assert file_report.scanned == 1
    assert file_report.ok == 0
    assert file_report.issue_counts == {
        IntegrityIssueType.TASK_FAILED: 1,
        IntegrityIssueType.MISSING_CACHE_BLOB: 1,
        IntegrityIssueType.MISSING_PAGE_CONTENT: 1,
    }

    # Now populate the PNG cache blob at the unprefixed page_id path and OCR text
    await blob_storage.put(
        CACHE_BUCKET, get_page_png_path(note_do.id, "P1"), b"png-bytes"
    )
    await db_session.execute(
        update(NotePageContentDO)
        .where(NotePageContentDO.file_id == note_do.id)
        .values(text_content="extracted text")
    )
    await db_session.execute(
        update(SystemTaskDO)
        .where(
            SystemTaskDO.file_id == note_do.id,
            SystemTaskDO.task_type == TaskType.SUMMARY_GENERATION.value,
        )
        .values(status=ProcessingStatus.COMPLETED, last_error=None)
    )
    await db_session.commit()

    healthy_report = await integrity_service.verify_user_storage(
        test_user_id, file_id=note_do.id
    )
    assert healthy_report.ok == 1
    assert healthy_report.issues == []


async def test_integrity_duplicate_root_folder(
    integrity_service: IntegrityService,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify duplicate case-variant root folder detection."""
    legacy_export = UserFileDO(
        user_id=test_user_id,
        file_name="Export",
        is_folder="Y",
        directory_id=0,
        is_active="Y",
    )
    db_session.add(legacy_export)
    await db_session.commit()

    report = await integrity_service.verify_user_storage(test_user_id)
    assert report.issue_counts == {IntegrityIssueType.DUPLICATE_ROOT_FOLDER: 1}
    assert len(report.issues) == 1
    assert report.issues[0].issue_type == IntegrityIssueType.DUPLICATE_ROOT_FOLDER
    assert report.issues[0].file_name == "Export"
    assert len(report.remediations) == 1
    assert "https://github.com/allenporter/supernote/issues" in report.remediations[0]


def test_remediation_hints_cover_all_issue_types() -> None:
    """Ensure every IntegrityIssueType has a remediation hint on the server."""
    assert set(_REMEDIATION_HINTS.keys()) == set(IntegrityIssueType)


async def test_integrity_summary_valid_and_deleted(
    integrity_service: IntegrityService,
    blob_storage: LocalBlobStorage,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify integrity check validates active summaries with blobs, groups, and file_ids and ignores deleted ones."""
    file_content = b"note-content"
    file_key = str(uuid.uuid4())
    await blob_storage.put(USER_DATA_BUCKET, file_key, file_content)
    file_do = UserFileDO(
        user_id=test_user_id,
        file_name="valid.note",
        is_folder="N",
        directory_id=0,
        size=len(file_content),
        md5=hashlib.md5(file_content).hexdigest(),
        storage_key=file_key,
        is_active="Y",
    )
    db_session.add(file_do)
    await db_session.flush()

    group_uuid = str(uuid.uuid4())
    group_do = SummaryDO(
        user_id=test_user_id,
        unique_identifier=group_uuid,
        name="Group 1",
        is_summary_group=True,
    )

    hw_bytes = b"handwriting-strokes"
    hw_key = f"hw-{uuid.uuid4()}"
    hw_md5 = hashlib.md5(hw_bytes).hexdigest()
    await blob_storage.put(USER_DATA_BUCKET, hw_key, hw_bytes)

    summary_with_blob = SummaryDO(
        user_id=test_user_id,
        file_id=file_do.id,
        unique_identifier=str(uuid.uuid4()),
        parent_unique_identifier=group_uuid,
        content="Summary with handwriting",
        is_summary_group=False,
        handwrite_inner_name=hw_key,
        handwrite_md5=hw_md5,
    )

    summary_text_only = SummaryDO(
        user_id=test_user_id,
        unique_identifier=str(uuid.uuid4()),
        parent_unique_identifier=group_uuid,
        content="Text only summary",
        is_summary_group=False,
    )

    deleted_summary = SummaryDO(
        user_id=test_user_id,
        unique_identifier=str(uuid.uuid4()),
        parent_unique_identifier="non-existent-group",
        handwrite_inner_name="missing-blob-key",
        is_summary_group=False,
        is_deleted=True,
    )

    db_session.add_all(
        [group_do, summary_with_blob, summary_text_only, deleted_summary]
    )
    await db_session.commit()

    report = await integrity_service.verify_user_storage(test_user_id)
    assert (
        report.scanned == 12
    )  # 8 default folders + 1 file + 1 group + 2 active summaries
    assert report.ok == 12
    assert report.issues == []

    # Scoped by file_id should scan the file and its linked summary
    file_report = await integrity_service.verify_user_storage(
        test_user_id, file_id=file_do.id
    )
    assert file_report.scanned == 2
    assert file_report.ok == 2
    assert file_report.issues == []


async def test_integrity_summary_missing_blob_and_hash_mismatch(
    integrity_service: IntegrityService,
    blob_storage: LocalBlobStorage,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify integrity check detects missing handwriting blobs and MD5 mismatches for summaries."""
    hw_bytes = b"handwriting-strokes"
    hw_key = f"hw-{uuid.uuid4()}"
    await blob_storage.put(USER_DATA_BUCKET, hw_key, hw_bytes)

    missing_blob_summary = SummaryDO(
        user_id=test_user_id,
        unique_identifier="sum-missing-blob",
        name="Missing Blob Summary",
        content="Summary missing blob on disk",
        is_summary_group=False,
        handwrite_inner_name="non-existent-inner-name",
        handwrite_md5="deadbeef",
    )

    missing_key_summary = SummaryDO(
        user_id=test_user_id,
        unique_identifier="sum-missing-key",
        content="Summary with md5 but no inner name",
        is_summary_group=False,
        handwrite_inner_name=None,
        handwrite_md5="deadbeef",
    )

    bad_hash_summary = SummaryDO(
        user_id=test_user_id,
        unique_identifier="sum-bad-hash",
        source_path="/Note/bad.note",
        content="Summary with wrong md5",
        is_summary_group=False,
        handwrite_inner_name=hw_key,
        handwrite_md5="00000000000000000000000000000000",
    )

    db_session.add_all([missing_blob_summary, missing_key_summary, bad_hash_summary])
    await db_session.commit()

    report = await integrity_service.verify_user_storage(test_user_id)
    assert report.scanned == 11  # 8 default folders + 3 summaries
    assert report.issue_counts == {
        IntegrityIssueType.MISSING_BLOB: 2,
        IntegrityIssueType.HASH_MISMATCH: 1,
    }
    assert report.ok == 8
    assert [(i.file_name, i.issue_type) for i in report.issues] == [
        ("Missing Blob Summary", IntegrityIssueType.MISSING_BLOB),
        ("sum-missing-key", IntegrityIssueType.MISSING_BLOB),
        ("/Note/bad.note", IntegrityIssueType.HASH_MISMATCH),
    ]
    assert len(report.remediations) == 2


async def test_integrity_summary_orphans(
    integrity_service: IntegrityService,
    test_user_id: int,
    db_session: AsyncSession,
) -> None:
    """Verify integrity check detects summaries with orphaned parent groups or invalid file_id."""
    deleted_group_uuid = str(uuid.uuid4())
    deleted_group = SummaryDO(
        user_id=test_user_id,
        unique_identifier=deleted_group_uuid,
        name="Deleted Group",
        is_summary_group=True,
        is_deleted=True,
    )

    orphan_parent_summary = SummaryDO(
        user_id=test_user_id,
        unique_identifier="sum-orphan-parent",
        name="Orphan Parent Summary",
        parent_unique_identifier=deleted_group_uuid,
        content="Orphaned by deleted group",
        is_summary_group=False,
    )

    orphan_file_summary = SummaryDO(
        user_id=test_user_id,
        file_id=999999,
        unique_identifier="sum-orphan-file",
        name="Orphan File Summary",
        content="Orphaned by missing file_id",
        is_summary_group=False,
    )

    db_session.add_all([deleted_group, orphan_parent_summary, orphan_file_summary])
    await db_session.commit()

    report = await integrity_service.verify_user_storage(test_user_id)
    assert report.scanned == 10  # 8 default folders + 2 active summaries
    assert report.issue_counts == {IntegrityIssueType.ORPHAN: 2}
    assert report.ok == 8
    assert [(i.file_id, i.file_name, i.issue_type) for i in report.issues] == [
        (
            orphan_parent_summary.id,
            "Orphan Parent Summary",
            IntegrityIssueType.ORPHAN,
        ),
        (999999, "Orphan File Summary", IntegrityIssueType.ORPHAN),
    ]
