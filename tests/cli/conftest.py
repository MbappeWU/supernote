"""Shared fixtures for CLI tests backed by a real in-process Supernote server."""

import hashlib
import os
import socket
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient
from pytest_aiohttp import AiohttpClient

import supernote.server.db.models  # noqa: F401
from supernote.client.auth import FileCacheAuth
from supernote.models.user import UserRegisterDTO
from supernote.server.app import create_app
from supernote.server.config import AiConfig, AuthConfig, ModelConfig, ServerConfig
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.coordination import SqliteCoordinationService
from supernote.server.services.user import UserService

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "secret"


@dataclass
class CliServerEnv:
    """Running test server environment for CLI tests."""

    url: str
    client: TestClient
    session_manager: DatabaseSessionManager
    user_service: UserService
    server_config: ServerConfig
    admin_token: str


@pytest.fixture(autouse=True)
def isolated_cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME to tmp_path so ~/.cache/supernote.pkl is isolated per test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def cli_env(
    aiohttp_client: AiohttpClient,
    tmp_path: Path,
) -> AsyncGenerator[CliServerEnv]:
    """Spin up a real Supernote server and pre-populate ~/.cache/supernote.pkl with admin credentials."""
    db_path = tmp_path / "cli_test.sqlite"
    session_manager = DatabaseSessionManager(
        f"sqlite+aiosqlite:///{db_path}",
        engine_kwargs={"connect_args": {"check_same_thread": False}},
    )
    await session_manager.create_all_tables()
    real_close = session_manager.close
    session_manager.create_all_tables = AsyncMock()  # type: ignore[method-assign]
    session_manager.close = AsyncMock()  # type: ignore[method-assign]

    storage_dir = tmp_path / "storage"
    mcp_port = _pick_port()
    server_config = ServerConfig(
        host="127.0.0.1",
        port=_pick_port(),
        mcp_port=mcp_port,
        _mcp_base_url=f"http://127.0.0.1:{mcp_port}",
        storage_dir=str(storage_dir),
        auth=AuthConfig(
            enable_registration=True,
            expiration_hours=1,
            secret_key="test-secret-key-32-characters-long!!",
        ),
        ai=AiConfig(
            ocr=ModelConfig(provider="fake", model="fake-ocr-v1"),
            summary=ModelConfig(provider="fake", model="fake-summary-v1"),
            embedding=ModelConfig(provider="fake", model="fake-embedding-v1"),
        ),
    )
    coordination_service = SqliteCoordinationService(session_manager)
    user_service = UserService(
        server_config.auth, coordination_service, session_manager
    )

    with (
        patch("supernote.server.app.run_migrations"),
        patch(
            "supernote.server.app.create_db_session_manager",
            return_value=session_manager,
        ),
        patch(
            "supernote.server.app.create_coordination_service",
            return_value=coordination_service,
        ),
        patch(
            "supernote.server.app.create_user_service",
            return_value=user_service,
        ),
    ):
        app = create_app(server_config)
        test_client = await aiohttp_client(app)
        base_url = str(test_client.make_url("")).rstrip("/")

        pw_md5 = hashlib.md5(ADMIN_PASSWORD.encode("utf-8")).hexdigest()
        await user_service.register(
            UserRegisterDTO(
                email=ADMIN_EMAIL,
                password=pw_md5,
                user_name="Admin",
            )
        )
        admin_token = await user_service.create_session(ADMIN_EMAIL)

        cache_path = os.path.expanduser("~/.cache/supernote.pkl")
        FileCacheAuth(cache_path).save_credentials(admin_token, base_url)

        yield CliServerEnv(
            url=base_url,
            client=test_client,
            session_manager=session_manager,
            user_service=user_service,
            server_config=server_config,
            admin_token=admin_token,
        )

    await real_close()
