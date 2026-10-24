import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from supernote.client.device import DeviceClient
from supernote.models.base import ProcessingStatus, TaskType
from supernote.server.constants import USER_DATA_BUCKET
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.note_processing import NotePageContentDO, SystemTaskDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.blob import BlobStorage, LocalBlobStorage
from supernote.server.services.integrity import IntegrityService
from supernote.server.services.user import UserService


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

    assert report.orphans == 0
    assert report.missing_blob == 1
    assert report.size_mismatch == 1
    # Scanned: 8 (default folders) + Docs + good + bad_size = 11
    assert report.scanned == 11
    assert report.ok == 9  # Some default folders are ok
    assert {i.issue_type for i in report.issues} == {"missing_blob", "size_mismatch"}
    assert {i.file_name for i in report.issues} == {"good.txt", "bad_size.txt"}

    file_report = await integrity_service.verify_user_storage(
        user_id, file_id=good_file.id
    )
    assert file_report.scanned == 1
    assert file_report.missing_blob == 1
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
    assert report.orphans == 1


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
    assert report.hash_mismatch == 1
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

    assert report.orphans == 0
    assert report.missing_blob == 0
    assert report.size_mismatch == 0
    assert report.hash_mismatch == 0
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
                key="page_1",
                status=ProcessingStatus.COMPLETED,
            ),
            SystemTaskDO(
                file_id=note_do.id,
                task_type=TaskType.OCR_EXTRACTION.value,
                key="page_1",
                status=ProcessingStatus.COMPLETED,
            ),
            NotePageContentDO(
                file_id=note_do.id,
                page_index=0,
                page_id="page_1",
                text_content=None,
            ),
        ]
    )
    await db_session.commit()

    file_report = await integrity_service.verify_user_storage(
        test_user_id, file_id=note_do.id
    )
    assert file_report.scanned == 1
    assert file_report.pipeline_issues == 1
    assert file_report.ok == 0

    issue_types = {issue.issue_type for issue in file_report.issues}
    assert issue_types == {
        "task_failed",
        "missing_cache_blob",
        "missing_page_content",
    }


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
    assert report.folder_anomalies == 1
    assert len(report.issues) == 1
    assert report.issues[0].issue_type == "duplicate_root_folder"
    assert report.issues[0].file_name == "Export"
