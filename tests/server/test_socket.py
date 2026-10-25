"""Integration tests for Socket.IO server handler and client interaction."""

import asyncio
from collections.abc import AsyncGenerator, Callable

import aiohttp
import jwt
import pytest
from aiohttp.test_utils import TestServer
from socketio.exceptions import ConnectionError as SocketIOConnectionError

from supernote.client.exceptions import SupernoteSocketError
from supernote.client.socket import SupernoteSocketClient
from supernote.models.socket import (
    DigestMessageTemplate,
    ScheduleMessageTemplate,
    SocketDigestMessageData,
    SocketHandshakeParams,
    SocketIoAction,
    SocketIoDigestDataType,
    SocketIoEvent,
    SocketIoMessageTemplate,
    SocketIoMessageType,
    SocketMessageData,
    SocketTaskMessageData,
)
from supernote.server.app import create_app
from supernote.server.config import ServerConfig
from supernote.server.events import LocalEventBus, SyncCompletedEvent
from supernote.server.services.user import JWT_ALGORITHM, UserService
from supernote.server.socket import SocketIOServerManager
from supernote.server.socket_auth import compute_handshake_signature


def _make_handshake_params(
    token: str, conn_type: str = "file", random_val: str = "12345"
) -> SocketHandshakeParams:
    return SocketHandshakeParams(
        token=token,
        type=conn_type,
        random=random_val,
        sign=compute_handshake_signature(token, conn_type, random_val),
    )


@pytest.fixture
async def socketio_server(
    server_config: ServerConfig,
    create_test_user: None,
) -> AsyncGenerator[TestServer]:
    """Create and start a running TestServer hosting the Supernote app with Socket.IO."""
    app = create_app(server_config)
    server = TestServer(app)
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
def create_socket_client(
    socketio_server: TestServer,
) -> Callable[[], SupernoteSocketClient]:
    """Factory fixture providing SupernoteSocketClient instances pre-wired to the server."""
    host = f"http://{socketio_server.host}:{socketio_server.port}"

    def _factory() -> SupernoteSocketClient:
        return SupernoteSocketClient(host=host)

    return _factory


@pytest.fixture
def socket_client(
    create_socket_client: Callable[[], SupernoteSocketClient],
) -> SupernoteSocketClient:
    """Fixture providing a default pre-configured SupernoteSocketClient instance."""
    return create_socket_client()


@pytest.mark.asyncio
async def test_socketio_ping_pong(
    socketio_server: TestServer,
    socket_client: SupernoteSocketClient,
) -> None:
    user_service: UserService = socketio_server.app["user_service"]
    token_a = await user_service.create_session("test@example.com")
    params_a = _make_handshake_params(token_a)

    await socket_client.connect(params_a)
    assert socket_client.is_connected

    # Send ratta_ping and verify success
    await socket_client.ping(timeout=5.0)

    await socket_client.disconnect()
    assert not socket_client.is_connected


@pytest.mark.asyncio
async def test_socketio_status_heartbeat(
    socketio_server: TestServer,
    socket_client: SupernoteSocketClient,
) -> None:
    user_service: UserService = socketio_server.app["user_service"]
    token_a = await user_service.create_session("test@example.com")
    params_a = _make_handshake_params(token_a)

    await socket_client.connect(params_a)
    assert socket_client.is_connected

    # Check status and verify success
    await socket_client.check_status(timeout=5.0)

    await socket_client.disconnect()


@pytest.fixture
def additional_users() -> list[str]:
    return ["a@example.com"]


@pytest.mark.asyncio
async def test_socketio_multi_user_message_isolation(
    socketio_server: TestServer,
    create_socket_client: Callable[[], SupernoteSocketClient],
    create_test_user: None,
) -> None:
    user_service: UserService = socketio_server.app["user_service"]
    user_a = "test@example.com"
    user_b = "a@example.com"

    token_a = await user_service.create_session(user_a)
    token_b = await user_service.create_session(user_b)

    params_a = _make_handshake_params(token_a)
    params_b = _make_handshake_params(token_b)

    client_a = create_socket_client()
    client_b = create_socket_client()

    await client_a.connect(params_a)
    await client_b.connect(params_b)

    assert client_a.is_connected
    assert client_b.is_connected

    manager: SocketIOServerManager = socketio_server.app["socketio_manager"]
    assert manager.sio is not None

    # Send a message targeted ONLY to User A
    msg_data_a = SocketMessageData(
        code="200",
        msg="update notification",
        timestamp=1700000000000,
        msg_type=SocketIoMessageType.FILE_SYN,
        data=[
            SocketIoMessageTemplate(
                message_type=SocketIoAction.ADDFILE,
                equipment_no="SN123",
                file_type="note",
                id=101,
                original_name="NoteA.note",
            )
        ],
    )

    await manager.send_message(user_a, msg_data_a)

    # User A receives the targeted message
    received_a = await anext(client_a.messages())
    assert isinstance(received_a, SocketMessageData)
    assert received_a.code == "200"
    assert len(received_a.data) == 1
    assert received_a.data[0].original_name == "NoteA.note"

    # User B should not receive any message
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(client_b.messages()), timeout=0.05)

    await client_a.disconnect()
    await client_b.disconnect()


@pytest.mark.asyncio
async def test_socketio_connect_unauthorized(
    socket_client: SupernoteSocketClient,
) -> None:
    invalid_params = SocketHandshakeParams(
        token="some-token",
        type="file",
        random="123",
        sign="invalid_signature",
    )

    with pytest.raises(SocketIOConnectionError):
        await socket_client.connect(invalid_params)

    assert not socket_client.is_connected


@pytest.mark.asyncio
async def test_socketio_connect_invalid_token(
    socket_client: SupernoteSocketClient,
    server_config: ServerConfig,
) -> None:
    # Valid signature but invalid JWT token string
    invalid_token_params = _make_handshake_params("bad.jwt.token")

    with pytest.raises(SocketIOConnectionError):
        await socket_client.connect(invalid_token_params)

    assert not socket_client.is_connected

    # Valid JWT signature, but never persisted in CoordinationService
    secret = server_config.auth.secret_key
    unpersisted_token = jwt.encode(
        {"sub": "test@example.com"}, secret, algorithm=JWT_ALGORITHM
    )
    unpersisted_params = _make_handshake_params(unpersisted_token)

    with pytest.raises(SocketIOConnectionError):
        await socket_client.connect(unpersisted_params)

    assert not socket_client.is_connected


@pytest.mark.asyncio
async def test_socketio_client_received_ack(
    socketio_server: TestServer,
    socket_client: SupernoteSocketClient,
) -> None:
    user_service: UserService = socketio_server.app["user_service"]
    token_a = await user_service.create_session("test@example.com")
    params_a = _make_handshake_params(token_a)

    await socket_client.connect(params_a)
    assert socket_client.is_connected

    # Send client delivery ACK
    await socket_client.send_client_message("Received")

    await socket_client.disconnect()


@pytest.mark.asyncio
async def test_socketio_client_messages_async_iterator(
    socketio_server: TestServer,
    socket_client: SupernoteSocketClient,
) -> None:
    user_service: UserService = socketio_server.app["user_service"]
    user_id = "test@example.com"
    token_a = await user_service.create_session(user_id)
    params_a = _make_handshake_params(token_a)

    await socket_client.connect(params_a)

    manager: SocketIOServerManager = socketio_server.app["socketio_manager"]
    msg_data = SocketMessageData(
        msg="test message",
        timestamp=1700000000000,
        msg_type=SocketIoMessageType.FILE_SYN,
    )
    await manager.send_message(user_id, msg_data)

    async for msg in socket_client.messages():
        assert isinstance(msg, SocketMessageData)
        assert msg.code == "200"
        assert msg.msg == "test message"
        break

    # Also test TASK-SYN ('to-do' channel) and DIGEST-SYN ('digest' channel) routing
    task_msg = SocketTaskMessageData(
        msg="task sync",
        timestamp=1700000000001,
        data=[
            ScheduleMessageTemplate(
                message_type=SocketIoAction.QUERY,
                equipment_no="SN123",
                task_list_id="501",
                task_id="1002",
            )
        ],
    )
    await manager.send_message(user_id, task_msg)
    received_task = await anext(socket_client.messages())
    assert isinstance(received_task, SocketTaskMessageData)
    assert received_task.code == "200"
    assert received_task.msg_type == SocketIoMessageType.TASK_SYN
    assert received_task.msg == "task sync"
    assert len(received_task.data) == 1
    assert received_task.data[0].task_list_id == "501"

    digest_msg = SocketDigestMessageData(
        msg="digest sync",
        timestamp=1700000000002,
        data=[
            DigestMessageTemplate(
                message_type=SocketIoAction.ADD_DIGEST,
                data_type=SocketIoDigestDataType.SUMMARY,
                equipment_no="SN123",
                id=302,
            )
        ],
    )
    await manager.send_message(user_id, digest_msg)
    received_digest = await anext(socket_client.messages())
    assert isinstance(received_digest, SocketDigestMessageData)
    assert received_digest.code == "200"
    assert received_digest.msg_type == SocketIoMessageType.DIGEST_SYN
    assert received_digest.msg == "digest sync"
    assert len(received_digest.data) == 1
    assert received_digest.data[0].id == 302

    # Verify LocalEventBus SyncCompletedEvent triggers STARTSYNC on ServerMessage
    event_bus: LocalEventBus = socketio_server.app["event_bus"]
    await event_bus.publish(
        SyncCompletedEvent(
            user_id=1,
            user_email=user_id,
            equipment_no="SN999",
        )
    )
    received_sync = await anext(socket_client.messages())
    assert isinstance(received_sync, SocketMessageData)
    assert received_sync.msg_type == SocketIoMessageType.FILE_SYN
    assert len(received_sync.data) == 1
    assert received_sync.data[0].message_type == SocketIoAction.STARTSYNC
    assert received_sync.data[0].equipment_no == "SN999"

    await socket_client.disconnect()


@pytest.mark.asyncio
async def test_socketio_revoked_session_disconnected_on_heartbeat_and_send(
    socketio_server: TestServer,
    create_socket_client: Callable[[], SupernoteSocketClient],
) -> None:
    """Verify revoked sessions are disconnected on heartbeat or outbound send_message."""
    user_service: UserService = socketio_server.app["user_service"]
    manager: SocketIOServerManager = socketio_server.app["socketio_manager"]
    user_id = "test@example.com"

    token_ping = await user_service.create_session(user_id)
    token_status = await user_service.create_session(user_id)
    token_send = await user_service.create_session(user_id)

    client_ping = create_socket_client()
    client_status = create_socket_client()
    client_send = create_socket_client()

    await client_ping.connect(_make_handshake_params(token_ping))
    await client_status.connect(_make_handshake_params(token_status))
    await client_send.connect(_make_handshake_params(token_send))

    # Revoke token_ping and verify ratta_ping fails and disconnects
    await user_service.logout(token_ping)
    with pytest.raises(TimeoutError):
        await client_ping.ping(timeout=0.1)

    # Revoke token_status and verify status check fails and disconnects
    await user_service.logout(token_status)
    with pytest.raises(TimeoutError):
        await client_status.check_status(timeout=0.1)

    # Revoke token_send and verify outbound send_message disconnects instead of delivering
    await user_service.logout(token_send)
    msg_data = SocketMessageData(
        code="200",
        msg="secret update",
        timestamp=1700000000000,
        msg_type=SocketIoMessageType.FILE_SYN,
    )
    await manager.send_message(user_id, msg_data)

    received = await anext(client_send.messages())
    assert isinstance(received, SocketMessageData)
    assert received.code == "403"
    assert received.msg == "token verification failed"

    await client_ping.disconnect()
    await client_status.disconnect()
    await client_send.disconnect()


@pytest.mark.asyncio
async def test_socketio_eio3_device_handshake_and_ping_compat(
    socketio_server: TestServer,
) -> None:
    """Verify real Supernote device EIO=3 (Socket.IO v2) WebSocket handshake and client PING/PONG."""
    user_service: UserService = socketio_server.app["user_service"]
    manager: SocketIOServerManager = socketio_server.app["socketio_manager"]
    user_id = "test@example.com"

    token = await user_service.create_session(user_id)
    params = _make_handshake_params(token)

    ws_url = (
        f"http://{socketio_server.host}:{socketio_server.port}/socket.io/"
        f"?EIO=3&transport=websocket"
        f"&token={params.token}&type={params.type}"
        f"&random={params.random}&sign={params.sign}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(ws_url) as ws:
            # 1. Server sends Engine.IO OPEN ('0{...}') followed immediately by
            # Socket.IO default-namespace CONNECT ('40{...}') without waiting
            # for a client '40' packet.
            eio_open = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
            assert eio_open.startswith("0{")

            sio_connect = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
            assert sio_connect.startswith("40")

            # 2. Client-initiated Engine.IO v3 PING ('2') receives PONG ('3')
            await ws.send_str("2")
            eio_pong = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
            assert eio_pong == "3"

            # 3. Server-to-device notification is delivered over the default namespace
            msg_data = SocketMessageData(
                code="0",
                msg="eio3 sync notification",
                timestamp=1700000000000,
                msg_type=SocketIoMessageType.FILE_SYN,
            )
            await manager.send_message(user_id, msg_data)
            event_frame = await asyncio.wait_for(ws.receive_str(), timeout=2.0)
            assert event_frame.startswith('42["ServerMessage",')
            assert "eio3 sync notification" in event_frame


@pytest.mark.asyncio
async def test_socketio_client_channels_and_error_handling_via_server(
    socketio_server: TestServer,
    socket_client: SupernoteSocketClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify SupernoteSocketClient handles TASK-SYN, DIGEST-SYN, malformed payloads, and unexpected server replies."""
    user_service: UserService = socketio_server.app["user_service"]
    manager: SocketIOServerManager = socketio_server.app["socketio_manager"]
    user_id = "test@example.com"

    token = await user_service.create_session(user_id)
    await socket_client.connect(_make_handshake_params(token))

    # Valid TASK-SYN and DIGEST-SYN envelopes delivered through server
    task_msg = SocketTaskMessageData(
        code="200",
        msg="Success",
        timestamp=1700000001000,
        data=[
            ScheduleMessageTemplate(
                message_type=SocketIoAction.QUERY,
                task_list_id="list-1",
                task_id="task-1",
                timestamp=1700000001000,
            )
        ],
    )
    await manager.send_message(user_id, task_msg)
    assert await anext(socket_client.messages()) == task_msg

    digest_msg = SocketDigestMessageData(
        code="200",
        msg="Success",
        timestamp=1700000002000,
        data=[
            DigestMessageTemplate(
                message_type=SocketIoAction.ADD_DIGEST,
                data_type=SocketIoDigestDataType.SUMMARY_TAG,
                id=10,
                name="Tag",
                timestamp=1700000002000,
            )
        ],
    )
    await manager.send_message(user_id, digest_msg)
    assert await anext(socket_client.messages()) == digest_msg

    # Malformed payloads emitted by server on to-do and digest channels log warnings without crashing
    await manager.sio.emit(
        SocketIoEvent.TODO.value, "{not-valid-json", room=f"user_{user_id}"
    )
    await manager.sio.emit(
        SocketIoEvent.DIGEST.value, "{not-valid-json", room=f"user_{user_id}"
    )

    # Unexpected ping reply payload logs a warning
    await manager.sio.emit(
        SocketIoEvent.RATTA_PING.value, "unexpected-pong", room=f"user_{user_id}"
    )
    await socket_client.ping(timeout=1.0)
    assert "Unexpected ping reply payload: unexpected-pong" in caplog.text
    assert "Failed to parse to-do message" in caplog.text
    assert "Failed to parse digest message" in caplog.text

    # Non-'true' plain-string ServerMessage causes check_status to raise SupernoteSocketError
    await manager.sio.emit(
        SocketIoEvent.SERVER_MESSAGE.value, "false", room=f"user_{user_id}"
    )
    with pytest.raises(SupernoteSocketError, match="Unexpected status check reply"):
        await socket_client.check_status(timeout=1.0)

    await socket_client.disconnect()
