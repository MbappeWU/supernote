"""Unit tests for LocalEventBus and server domain events."""

import asyncio

import pytest

from supernote.server.events import (
    Event,
    LocalEventBus,
    NoteDeletedEvent,
    NoteProcessingCompletedEvent,
    NoteUpdatedEvent,
    SyncCompletedEvent,
)


async def test_local_event_bus_publish_and_subscribe() -> None:
    bus = LocalEventBus()
    received_updated: list[NoteUpdatedEvent] = []
    received_deleted: list[NoteDeletedEvent] = []
    received_completed: list[NoteProcessingCompletedEvent] = []
    received_sync: list[SyncCompletedEvent] = []

    async def on_updated(event: Event) -> None:
        assert isinstance(event, NoteUpdatedEvent)
        received_updated.append(event)

    async def on_deleted(event: Event) -> None:
        assert isinstance(event, NoteDeletedEvent)
        received_deleted.append(event)

    async def on_processing_completed(event: Event) -> None:
        assert isinstance(event, NoteProcessingCompletedEvent)
        received_completed.append(event)

    async def on_sync_completed(event: Event) -> None:
        assert isinstance(event, SyncCompletedEvent)
        received_sync.append(event)

    bus.subscribe(NoteUpdatedEvent, on_updated)
    bus.subscribe(NoteDeletedEvent, on_deleted)
    bus.subscribe(NoteProcessingCompletedEvent, on_processing_completed)
    bus.subscribe(SyncCompletedEvent, on_sync_completed)

    ev_updated = NoteUpdatedEvent(
        file_id=10, user_id=1, file_path="/Note/a.note", defer_processing=True
    )
    ev_deleted = NoteDeletedEvent(file_id=10, user_id=1)
    ev_completed = NoteProcessingCompletedEvent(
        file_id=10, user_id=1, file_path="/Note/a.note"
    )
    ev_sync = SyncCompletedEvent(
        user_id=1, user_email="user@example.com", equipment_no="SN123"
    )

    # Publishing an event with no subscribers is a safe no-op
    await bus.publish(Event())

    await bus.publish(ev_updated)
    await bus.publish(ev_deleted)
    await bus.publish(ev_completed)
    await bus.publish(ev_sync)
    await asyncio.sleep(0)

    assert received_updated == [ev_updated]
    assert received_deleted == [ev_deleted]
    assert received_completed == [ev_completed]
    assert received_sync == [ev_sync]


async def test_local_event_bus_isolates_subscriber_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = LocalEventBus()
    healthy_received: list[SyncCompletedEvent] = []

    async def failing_handler(event: Event) -> None:
        raise RuntimeError("subscriber boom")

    async def healthy_handler(event: Event) -> None:
        assert isinstance(event, SyncCompletedEvent)
        healthy_received.append(event)

    bus.subscribe(SyncCompletedEvent, failing_handler)
    bus.subscribe(SyncCompletedEvent, healthy_handler)

    ev = SyncCompletedEvent(user_id=42, user_email="test@example.com")
    await bus.publish(ev)
    await asyncio.sleep(0)

    assert healthy_received == [ev]
    assert "Error handling event" in caplog.text
