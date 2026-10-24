"""Unit tests for FileService recycle bin purge, reference-counted blob cleanup, and event publishing."""

import asyncio
import hashlib
import time
from pathlib import Path

import pytest
from sqlalchemy import update

from supernote.server.constants import USER_DATA_BUCKET
from supernote.server.db.models.file import RecycleFileDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.events import Event, LocalEventBus, NoteDeletedEvent
from supernote.server.services.blob import BlobStorage
from supernote.server.services.file import RECYCLE_RETENTION_MS, FileService
from supernote.server.services.user import UserService


@pytest.fixture
def event_bus() -> LocalEventBus:
    """Real LocalEventBus for verifying published domain events."""
    return LocalEventBus()


@pytest.fixture
def published_events(event_bus: LocalEventBus) -> list[Event]:
    """Capture domain events published on the LocalEventBus."""
    events: list[Event] = []

    async def capture(event: Event) -> None:
        events.append(event)

    event_bus.subscribe(NoteDeletedEvent, capture)
    return events


@pytest.fixture
def file_service(
    storage_root: Path,
    blob_storage: BlobStorage,
    user_service: UserService,
    session_manager: DatabaseSessionManager,
    event_bus: LocalEventBus,
) -> FileService:
    """Fixture providing FileService with explicit DI dependencies."""
    return FileService(
        storage_root=storage_root,
        blob_storage=blob_storage,
        user_service=user_service,
        session_manager=session_manager,
        event_bus=event_bus,
    )


async def test_recycle_bin_lifecycle(
    file_service: FileService,
    blob_storage: BlobStorage,
    published_events: list[Event],
    test_user_id: int,
    test_users: list[str],
) -> None:
    """Verify moving files to recycle bin, restoring, permanent deletion, and NoteDeletedEvent."""
    user = test_users[0]
    content = b"recycle bin test note"
    md5 = hashlib.md5(content).hexdigest()
    await blob_storage.put(USER_DATA_BUCKET, "rec_blob", content)
    file_entity = await file_service.finish_upload(
        user, "rec_item.note", "/", md5, "rec_blob"
    )

    # Soft delete (move to recycle bin)
    await file_service.delete_item(user, file_entity.id)
    root_items = await file_service.list_folder(user, "/")
    assert not any(item.id == file_entity.id for item in root_items)

    recycled_items = await file_service.list_recycle(user)
    assert len(recycled_items) == 1
    assert recycled_items[0].name == "rec_item.note"
    recycle_id = recycled_items[0].id

    # Revert from recycle bin
    await file_service.revert_from_recycle(user, [recycle_id])
    assert len(await file_service.list_recycle(user)) == 0
    restored_items = await file_service.list_folder(user, "/")
    assert any(item.id == file_entity.id for item in restored_items)

    # Delete again and permanently purge
    await file_service.delete_item(user, file_entity.id)
    recycled_items_2 = await file_service.list_recycle(user)
    recycle_id_2 = recycled_items_2[0].id

    published_events.clear()
    await file_service.delete_from_recycle(user, [recycle_id_2])
    await asyncio.sleep(0)
    assert len(await file_service.list_recycle(user)) == 0
    assert await blob_storage.exists(USER_DATA_BUCKET, "rec_blob") is False

    # Permanent purge of .note publishes NoteDeletedEvent
    assert published_events == [
        NoteDeletedEvent(file_id=file_entity.id, user_id=test_user_id)
    ]


async def test_file_shared_blob_reference_counting(
    file_service: FileService,
    blob_storage: BlobStorage,
    create_test_user: None,
    test_users: list[str],
) -> None:
    """Verify blob survives until all copies referencing its storage key are permanently purged."""
    user = test_users[0]
    content = b"shared content bytes"
    md5 = hashlib.md5(content).hexdigest()
    storage_key = "shared_key_1"
    await blob_storage.put(USER_DATA_BUCKET, storage_key, content)

    orig_file = await file_service.finish_upload(
        user, "orig.txt", "/", md5, storage_key
    )
    copied_file = await file_service.copy_item(
        user, orig_file.id, to_path="/copy.txt", autorename=True
    )
    assert copied_file.storage_key == orig_file.storage_key

    # Soft-delete and permanently purge original file
    await file_service.delete_item(user, orig_file.id)
    recycle_orig = (await file_service.list_recycle(user))[0]
    await file_service.delete_from_recycle(user, [recycle_orig.id])

    # Physical blob survives because copy still references it
    assert await blob_storage.exists(USER_DATA_BUCKET, storage_key) is True

    # Soft-delete and permanently purge copy
    await file_service.delete_item(user, copied_file.id)
    recycle_copy = (await file_service.list_recycle(user))[0]
    await file_service.delete_from_recycle(user, [recycle_copy.id])

    # Physical blob is deleted now that no references survive
    assert await blob_storage.exists(USER_DATA_BUCKET, storage_key) is False


async def test_file_purge_multiple_files_shared_blob_single_deletion(
    file_service: FileService,
    blob_storage: BlobStorage,
    create_test_user: None,
    test_users: list[str],
) -> None:
    """Verify permanently purging multiple files sharing the same blob key executes a single blob deletion."""
    user = test_users[0]
    content = b"shared content bytes 2"
    md5 = hashlib.md5(content).hexdigest()
    storage_key = "shared_key_2"
    await blob_storage.put(USER_DATA_BUCKET, storage_key, content)

    f1 = await file_service.finish_upload(user, "f1.txt", "/", md5, storage_key)
    f2 = await file_service.copy_item(user, f1.id, to_path="/f2.txt", autorename=True)
    f3 = await file_service.copy_item(user, f1.id, to_path="/f3.txt", autorename=True)

    await file_service.delete_item(user, f1.id)
    await file_service.delete_item(user, f2.id)
    await file_service.delete_item(user, f3.id)

    recycle_ids = [r.id for r in await file_service.list_recycle(user)]
    assert len(recycle_ids) == 3

    delete_calls: list[tuple[str, str]] = []
    orig_delete = blob_storage.delete

    async def tracking_delete(bucket: str, key: str) -> None:
        delete_calls.append((bucket, key))
        await orig_delete(bucket, key)

    setattr(blob_storage, "delete", tracking_delete)

    await file_service.delete_from_recycle(user, recycle_ids)

    matching_calls = [
        call for call in delete_calls if call == (USER_DATA_BUCKET, storage_key)
    ]
    assert len(matching_calls) == 1
    assert await blob_storage.exists(USER_DATA_BUCKET, storage_key) is False


async def test_recycle_bin_opportunistic_retention_cleanup(
    file_service: FileService,
    blob_storage: BlobStorage,
    session_manager: DatabaseSessionManager,
    published_events: list[Event],
    test_user_id: int,
    test_users: list[str],
) -> None:
    """Verify expired recycle bin items (>30 days) are opportunistically purged while recent items survive."""
    user = test_users[0]
    content = b"expired note content"
    md5 = hashlib.md5(content).hexdigest()
    await blob_storage.put(USER_DATA_BUCKET, "expired_blob", content)
    await blob_storage.put(USER_DATA_BUCKET, "recent_blob", content)

    expired_file = await file_service.finish_upload(
        user, "expired.note", "/", md5, "expired_blob"
    )
    recent_file = await file_service.finish_upload(
        user, "recent.note", "/", md5, "recent_blob"
    )

    await file_service.delete_item(user, expired_file.id)
    await file_service.delete_item(user, recent_file.id)

    # Backdate expired_file's delete_time beyond the 30-day retention window
    expired_time = int(time.time() * 1000) - RECYCLE_RETENTION_MS - 10_000
    async with session_manager.session() as session:
        await session.execute(
            update(RecycleFileDO)
            .where(RecycleFileDO.file_id == expired_file.id)
            .values(delete_time=expired_time)
        )
        await session.commit()

    published_events.clear()

    # Listing recycle bin opportunistically prunes the expired entry
    recycled = await file_service.list_recycle(user)
    await asyncio.sleep(0)
    assert [r.name for r in recycled] == ["recent.note"]
    assert await blob_storage.exists(USER_DATA_BUCKET, "expired_blob") is False
    assert await blob_storage.exists(USER_DATA_BUCKET, "recent_blob") is True

    assert published_events == [
        NoteDeletedEvent(file_id=expired_file.id, user_id=test_user_id)
    ]
