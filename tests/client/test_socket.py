"""Unit tests for SupernoteSocketClient."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import supernote.client.socket as socket_module
from supernote.client.exceptions import SupernoteSocketError
from supernote.client.socket import SupernoteSocketClient
from supernote.models.socket import (
    DigestMessageTemplate,
    ScheduleMessageTemplate,
    SocketDigestMessageData,
    SocketHandshakeParams,
    SocketIoAction,
    SocketIoClientMessage,
    SocketIoDigestDataType,
    SocketIoEvent,
    SocketIoFileType,
    SocketIoMessageTemplate,
    SocketIoMessageType,
    SocketMessageData,
    SocketTaskMessageData,
)


class _FakeAsyncClient:
    """In-memory fake for socketio.AsyncClient to test event handler wiring and client methods."""

    def __init__(self) -> None:
        self.events: dict[str, Callable[..., Any]] = {}
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.emit = AsyncMock()

    def event(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        self.events[getattr(fn, "__name__")] = fn
        return fn

    def on(self, event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.events[event_name] = fn
            return fn

        return _decorator


@pytest.fixture
def fake_sio() -> _FakeAsyncClient:
    return _FakeAsyncClient()


@pytest.fixture
def client(fake_sio: _FakeAsyncClient) -> SupernoteSocketClient:
    with patch.object(socket_module.socketio, "AsyncClient", return_value=fake_sio):
        return SupernoteSocketClient("http://localhost:8080/")


def test_init_raises_when_socketio_missing() -> None:
    with patch.object(socket_module, "socketio", None):
        with pytest.raises(SupernoteSocketError, match="python-socketio is required"):
            SupernoteSocketClient("http://localhost:8080")


async def test_connect_and_disconnect_lifecycle(
    client: SupernoteSocketClient,
    fake_sio: _FakeAsyncClient,
) -> None:
    assert client.host == "http://localhost:8080"
    assert not client.is_connected

    params = SocketHandshakeParams(
        token="jwt-token",
        type="SN100",
        random="98765",
        sign="sig123",
    )
    await client.connect(params)
    fake_sio.connect.assert_awaited_once_with(
        "http://localhost:8080/?token=jwt-token&type=SN100&random=98765&sign=sig123",
        socketio_path="socket.io",
        transports=["polling", "websocket"],
        wait_timeout=10,
    )

    await fake_sio.events["connect"]()
    assert client.is_connected

    await client.disconnect()
    fake_sio.disconnect.assert_awaited_once()
    assert not client.is_connected

    # Calling disconnect when already disconnected is a no-op
    fake_sio.disconnect.reset_mock()
    await client.disconnect()
    fake_sio.disconnect.assert_not_awaited()

    # Server-initiated disconnect callback updates state
    await fake_sio.events["connect"]()
    assert client.is_connected
    await fake_sio.events["disconnect"]()
    assert not client.is_connected


async def test_ping_success_warning_and_timeout(
    client: SupernoteSocketClient,
    fake_sio: _FakeAsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await fake_sio.events[SocketIoEvent.RATTA_PING.value](
        SocketIoClientMessage.RECEIVED.value
    )
    await client.ping(timeout=1.0)
    fake_sio.emit.assert_awaited_once_with(SocketIoEvent.RATTA_PING.value, "ping")

    await fake_sio.events[SocketIoEvent.RATTA_PING.value]("unexpected-pong")
    await client.ping(timeout=1.0)
    assert "Unexpected ping reply payload: unexpected-pong" in caplog.text

    with pytest.raises(TimeoutError):
        await client.ping(timeout=0.01)


async def test_check_status_success_error_and_timeout(
    client: SupernoteSocketClient,
    fake_sio: _FakeAsyncClient,
) -> None:
    await fake_sio.events[SocketIoEvent.SERVER_MESSAGE.value]("true")
    await client.check_status(timeout=1.0)
    fake_sio.emit.assert_awaited_once_with(
        SocketIoEvent.CLIENT_MESSAGE.value,
        SocketIoClientMessage.STATUS.value,
    )

    await fake_sio.events[SocketIoEvent.SERVER_MESSAGE.value]("false")
    with pytest.raises(
        SupernoteSocketError, match="Unexpected status check reply: false"
    ):
        await client.check_status(timeout=1.0)

    with pytest.raises(TimeoutError):
        await client.check_status(timeout=0.01)


async def test_send_client_message(
    client: SupernoteSocketClient,
    fake_sio: _FakeAsyncClient,
) -> None:
    await client.send_client_message(SocketIoClientMessage.RECEIVED.value)
    fake_sio.emit.assert_awaited_once_with(
        SocketIoEvent.CLIENT_MESSAGE.value,
        SocketIoClientMessage.RECEIVED.value,
    )


async def test_messages_routing_and_malformed_payload_handling(
    client: SupernoteSocketClient,
    fake_sio: _FakeAsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    file_msg = SocketMessageData(
        code="200",
        msg="Success",
        timestamp=1700000000000,
        msg_type=SocketIoMessageType.FILE_SYN,
        data=[
            SocketIoMessageTemplate(
                message_type=SocketIoAction.ADDFILE,
                equipment_no="SN100",
                file_type=SocketIoFileType.FILE,
                id=1,
            )
        ],
    )
    task_msg = SocketTaskMessageData(
        code="200",
        msg="Success",
        timestamp=1700000000001,
        data=[
            ScheduleMessageTemplate(
                message_type=SocketIoAction.QUERY,
                equipment_no="SN100",
                task_list_id="list-1",
                task_id="task-1",
            )
        ],
    )
    digest_msg = SocketDigestMessageData(
        code="200",
        msg="Success",
        timestamp=1700000000002,
        data=[
            DigestMessageTemplate(
                message_type=SocketIoAction.ADD_DIGEST,
                data_type=SocketIoDigestDataType.SUMMARY,
                equipment_no="SN100",
                id=10,
            )
        ],
    )

    await fake_sio.events[SocketIoEvent.SERVER_MESSAGE.value](file_msg.to_json())
    await fake_sio.events[SocketIoEvent.TODO.value](task_msg.to_json())
    await fake_sio.events[SocketIoEvent.DIGEST.value](digest_msg.to_json())

    # Malformed JSON on to-do and digest logs warnings without enqueueing or crashing
    await fake_sio.events[SocketIoEvent.TODO.value]("{not-valid-json")
    await fake_sio.events[SocketIoEvent.DIGEST.value]("{not-valid-json")
    assert "Failed to parse to-do message" in caplog.text
    assert "Failed to parse digest message" in caplog.text

    # Drain messages while disconnected (drains queued items then stops)
    received = [msg async for msg in client.messages()]
    assert received == [file_msg, task_msg, digest_msg]
