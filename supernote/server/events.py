import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Base class for all events."""


@dataclass
class NoteUpdatedEvent(Event):
    """Triggered when a .note file is created or updated."""

    file_id: int
    user_id: int
    file_path: str
    defer_processing: bool = False


@dataclass
class NoteDeletedEvent(Event):
    """Triggered when a .note file is deleted."""

    file_id: int
    user_id: int


@dataclass
class NoteProcessingCompletedEvent(Event):
    """Triggered when the asynchronous processing pipeline for a .note file completes."""

    file_id: int
    user_id: int
    file_path: str


@dataclass
class SyncCompletedEvent(Event):
    """Triggered when a device file synchronization session completes."""

    user_id: int
    user_email: str = ""
    equipment_no: str | None = None


@dataclass
class ScheduleUpdatedEvent(Event):
    """Triggered when a schedule task group or task is created, updated, or deleted."""

    user_id: int
    task_list_id: str | None = None
    task_id: str | None = None
    equipment_no: str | None = None


# TODO: Define and publish additional domain events on LocalEventBus for Socket.IO notification dispatch:
# - File/folder lifecycle events (create folder, upload finish, rename, move, copy, delete, recycle restore) -> FILE-SYN
# - Summary/digest/tag lifecycle events (add/update/delete summary group, summary item, or tag) -> DIGEST-SYN


EventHandler = Callable[[Any], Awaitable[Any]]


class LocalEventBus:
    """A simple in-process event bus using asyncio."""

    def __init__(self) -> None:
        self._subscribers: dict[type[Event], list[EventHandler]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    def subscribe[E: Event](
        self, event_type: type[E], handler: Callable[[E], Awaitable[Any]]
    ) -> None:
        """Subscribe a handler to an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed {handler} to {event_type.__name__}")

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers."""
        event_type = type(event)
        if event_type in self._subscribers:
            for handler in self._subscribers[event_type]:
                # We fire and forget using create_task to ensure background execution
                # and not block the caller (e.g., the API response).
                task = asyncio.create_task(self._safe_execute(handler, event))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def _safe_execute(self, handler: EventHandler, event: Event) -> None:
        """Execute handler and log exceptions."""
        try:
            await handler(event)
        except Exception:
            logger.exception(f"Error handling event {event} with handler {handler}")
