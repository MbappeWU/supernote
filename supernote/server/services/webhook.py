"""Outbound webhook dispatch for server events.

Listens on the internal :class:`~supernote.server.events.LocalEventBus` and,
for every configured endpoint subscribed to a given event name, sends a
signed HTTP POST notification. Delivery is best-effort: a slow or failing
endpoint is logged and skipped, it never raises back into the caller and
never blocks or delays the operation that triggered the event (e.g. a
device sync request).
"""

import asyncio
import hashlib
import hmac
import logging
import time

import aiohttp

from supernote.models.webhook import (
    EVENT_NOTE_PROCESSING_COMPLETED,
    EVENT_NOTE_SYNC_COMPLETED,
    NoteProcessingCompletedPayload,
    NoteSyncCompletedPayload,
    WebhookFileVO,
    WebhookPayload,
)

from ..config import WebhookConfig, WebhookEndpointConfig
from ..events import (
    Event,
    LocalEventBus,
    NoteProcessingCompletedEvent,
    NoteUpdatedEvent,
)

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Supernote-Signature"
EVENT_HEADER = "X-Supernote-Event"

REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0


def sign_payload(secret: str, body: bytes) -> str:
    """Compute the `sha256=<hex>` HMAC signature for a webhook payload body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookService:
    """Dispatches signed outbound webhook notifications for subscribed events."""

    def __init__(self, config: WebhookConfig, event_bus: LocalEventBus) -> None:
        self.config = config
        self.event_bus = event_bus

    def start(self) -> None:
        """Subscribe to the events that can trigger webhook dispatch."""
        if not self.config.enabled or not self.config.endpoints:
            logger.debug("Webhook dispatch disabled or no endpoints configured.")
            return
        self.event_bus.subscribe(NoteUpdatedEvent, self._handle_note_updated)
        self.event_bus.subscribe(
            NoteProcessingCompletedEvent, self._handle_note_processing_completed
        )
        logger.info(
            f"WebhookService started with {len(self.config.endpoints)} endpoint(s)."
        )

    async def _handle_note_updated(self, event: Event) -> None:
        """Translate a NoteUpdatedEvent into a note.sync_completed webhook."""
        if not isinstance(event, NoteUpdatedEvent):
            return
        # Note: intentionally does not include event.user_id. It's an
        # internal database identifier, not something we want to hand to
        # third-party endpoints. See PR #217 for discussion on how
        # per-user webhook delivery might work in a future iteration.
        payload = NoteSyncCompletedPayload(
            event=EVENT_NOTE_SYNC_COMPLETED,
            timestamp=int(time.time()),
            file=WebhookFileVO(id=event.file_id, path=event.file_path),
        )
        await self.dispatch(payload)

    async def _handle_note_processing_completed(self, event: Event) -> None:
        """Translate a NoteProcessingCompletedEvent into a note.processing_completed webhook."""
        if not isinstance(event, NoteProcessingCompletedEvent):
            return
        payload = NoteProcessingCompletedPayload(
            event=EVENT_NOTE_PROCESSING_COMPLETED,
            timestamp=int(time.time()),
            file=WebhookFileVO(id=event.file_id, path=event.file_path),
        )
        await self.dispatch(payload)

    async def dispatch(self, payload: WebhookPayload) -> None:
        """Send `payload` to every configured endpoint subscribed to `payload.event`.

        An endpoint with no `events` configured is subscribed to everything
        (see `WebhookEndpointConfig.events`). Never raises: endpoints are
        notified concurrently and independently, and one endpoint failing has
        no effect on delivery to the others.
        """
        event_name = payload.event
        endpoints = [
            ep
            for ep in self.config.endpoints
            if not ep.events or event_name in ep.events
        ]
        if not endpoints:
            return
        body = str(payload.to_json()).encode("utf-8")
        results = await asyncio.gather(
            *(self._send(endpoint, event_name, body) for endpoint in endpoints),
            return_exceptions=True,
        )
        for endpoint, result in zip(endpoints, results, strict=True):
            if isinstance(result, BaseException):
                logger.error(
                    f"Webhook {event_name} to {endpoint.url} raised "
                    f"{type(result).__name__}: {result}"
                )

    async def _send(
        self,
        endpoint: WebhookEndpointConfig,
        event_name: str,
        body: bytes,
    ) -> None:
        """POST `body` to a single endpoint, retrying transient failures.

        Client errors (4xx) are not retried. Network errors, timeouts, and
        server errors (5xx) are retried up to MAX_ATTEMPTS with a linear
        backoff. All failures are logged (without the secret or signature)
        and swallowed.
        """
        headers = {"Content-Type": "application/json", EVENT_HEADER: event_name}
        if endpoint.secret:
            headers[SIGNATURE_HEADER] = sign_payload(endpoint.secret, body)

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    async with session.post(
                        endpoint.url, data=body, headers=headers
                    ) as response:
                        if response.status < 400:
                            return
                        if response.status < 500:
                            logger.warning(
                                f"Webhook {event_name} to {endpoint.url} rejected "
                                f"with status {response.status}; not retrying."
                            )
                            return
                        logger.warning(
                            f"Webhook {event_name} to {endpoint.url} failed with "
                            f"status {response.status} (attempt {attempt}/{MAX_ATTEMPTS})."
                        )
                except (aiohttp.ClientError, TimeoutError) as e:
                    logger.warning(
                        f"Webhook {event_name} to {endpoint.url} failed: "
                        f"{type(e).__name__} (attempt {attempt}/{MAX_ATTEMPTS})."
                    )

                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

        logger.error(
            f"Webhook {event_name} to {endpoint.url} failed after "
            f"{MAX_ATTEMPTS} attempts; giving up."
        )
