"""Unit tests for LocalEventBus and server domain events."""

import asyncio

import pytest

from supernote.server.events import (
    Event,
    LocalEventBus,
    NoteDeletedEvent,
    NoteProcessingCompletedEvent,
    NoteUpdatedEvent,
    ScheduleUpdatedEvent,
    SyncCompletedEvent,
)


async def test_local_event_bus_publish_and_subscribe() -> None:
    bus = LocalEventBus()
    received_updated: list[NoteUpdatedEvent] = []
    received_deleted: list[NoteDeletedEvent] = []
    received_completed: list[NoteProcessingCompletedEvent] = []
    received_sync: list[SyncCompletedEvent] = []
    received_schedule: list[ScheduleUpdatedEvent] = []

    async def on_updated(event: NoteUpdatedEvent) -> None:
        received_updated.append(event)

    async def on_deleted(event: NoteDeletedEvent) -> None:
        received_deleted.append(event)

    async def on_processing_completed(event: NoteProcessingCompletedEvent) -> None:
        received_completed.append(event)

    async def on_sync_completed(event: SyncCompletedEvent) -> None:
        received_sync.append(event)

    async def on_schedule_updated(event: ScheduleUpdatedEvent) -> None:
        received_schedule.append(event)

    bus.subscribe(NoteUpdatedEvent, on_updated)
    bus.subscribe(NoteDeletedEvent, on_deleted)
    bus.subscribe(NoteProcessingCompletedEvent, on_processing_completed)
    bus.subscribe(SyncCompletedEvent, on_sync_completed)
    bus.subscribe(ScheduleUpdatedEvent, on_schedule_updated)

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
    ev_schedule = ScheduleUpdatedEvent(
        user_id=1, task_list_id="group-1", task_id="task-2", equipment_no="SN123"
    )

    # Publishing an event with no subscribers is a safe no-op
    await bus.publish(Event())

    await bus.publish(ev_updated)
    await bus.publish(ev_deleted)
    await bus.publish(ev_completed)
    await bus.publish(ev_sync)
    await bus.publish(ev_schedule)
    await asyncio.sleep(0)

    assert received_updated == [ev_updated]
    assert received_deleted == [ev_deleted]
    assert received_completed == [ev_completed]
    assert received_sync == [ev_sync]
    assert received_schedule == [ev_schedule]


async def test_local_event_bus_isolates_subscriber_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = LocalEventBus()
    healthy_received: list[SyncCompletedEvent] = []

    async def failing_handler(event: SyncCompletedEvent) -> None:
        raise RuntimeError("subscriber boom")

    async def healthy_handler(event: SyncCompletedEvent) -> None:
        healthy_received.append(event)

    bus.subscribe(SyncCompletedEvent, failing_handler)
    bus.subscribe(SyncCompletedEvent, healthy_handler)

    ev = SyncCompletedEvent(user_id=42, user_email="test@example.com")
    await bus.publish(ev)
    await asyncio.sleep(0)

    assert healthy_received == [ev]
    assert "Error handling event" in caplog.text
