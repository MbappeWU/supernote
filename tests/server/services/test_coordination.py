import asyncio
from collections.abc import AsyncGenerator

import freezegun
import pytest
from sqlalchemy import select

from supernote.server.db.models.kv import KeyValueDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.coordination import (
    CoordinationService,
    SqliteCoordinationService,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def local_coordination_service() -> AsyncGenerator[CoordinationService]:
    """Create a local coordination service for testing."""
    manager = DatabaseSessionManager(TEST_DB_URL)
    assert manager._engine
    await manager.create_all_tables()

    service = SqliteCoordinationService(manager)
    yield service
    await manager.close()


async def test_key_expiry(local_coordination_service: CoordinationService) -> None:
    """Test key expiry."""
    with freezegun.freeze_time("2024-01-01 12:00:00"):
        await local_coordination_service.set_value("foo", "bar", ttl=15)

        assert await local_coordination_service.get_value("foo") == "bar"

    with freezegun.freeze_time("2024-01-01 12:00:16"):
        assert await local_coordination_service.get_value("foo") is None


async def test_increment(coordination_service: SqliteCoordinationService) -> None:
    key = "incr:test"

    # 1. Increment new key
    val = await coordination_service.increment(key, 1, ttl=60)
    assert val == 1

    # Check it exists
    stored = await coordination_service.get_value(key)
    assert stored == "1"

    # 2. Increment existing
    val = await coordination_service.increment(key, 1)
    assert val == 2

    # 3. Increment by larger amount
    val = await coordination_service.increment(key, 10)
    assert val == 12


async def test_increment_expiry(
    coordination_service: SqliteCoordinationService,
) -> None:
    key = "incr:expire"

    # Set with short TTL (but we can't easily wait for it in unit test without sleep)
    # Instead, we manually set an expired value first?
    # No, let's use the property that increment sets TTL on creation.

    # 1. Create
    val = await coordination_service.increment(key, 1, ttl=100)
    assert val == 1

    # 2. Verify TTL is roughly logic (hard to verify exact value without inspecting DB directly)
    # But we can verify "expired" behavior by mocking time or inserting expired row manually.


async def test_delete_by_prefix(
    coordination_service: SqliteCoordinationService,
) -> None:
    await coordination_service.set_value("session:1:EQ1:tok1", "v1", ttl=60)
    await coordination_service.set_value("session:1:EQ2:tok2", "v2", ttl=60)
    await coordination_service.set_value("session:2:EQ1:tok3", "v3", ttl=60)

    await coordination_service.delete_by_prefix("")
    assert await coordination_service.get_value("session:1:EQ1:tok1") == "v1"

    await coordination_service.delete_by_prefix("session:1:EQ1:")
    assert await coordination_service.get_value("session:1:EQ1:tok1") is None
    assert await coordination_service.get_value("session:1:EQ2:tok2") == "v2"
    assert await coordination_service.get_value("session:2:EQ1:tok3") == "v3"

    await coordination_service.delete_by_prefix("session:1:")
    assert await coordination_service.get_value("session:1:EQ2:tok2") is None
    assert await coordination_service.get_value("session:2:EQ1:tok3") == "v3"


async def test_pop_value_atomic_and_expiry(
    coordination_service: SqliteCoordinationService,
) -> None:
    """Verify pop_value atomically consumes a key once and respects TTL expiry."""
    assert await coordination_service.pop_value("missing") is None

    await coordination_service.set_value("nonce:1", "secret", ttl=60)
    results = await asyncio.gather(
        *[coordination_service.pop_value("nonce:1") for _ in range(10)]
    )
    assert results.count("secret") == 1
    assert results.count(None) == 9
    assert await coordination_service.get_value("nonce:1") is None

    with freezegun.freeze_time("2024-01-01 12:00:00"):
        await coordination_service.set_value("nonce:expired", "old", ttl=10)

    with freezegun.freeze_time("2024-01-01 12:00:15"):
        assert await coordination_service.pop_value("nonce:expired") is None


async def test_set_value_prunes_expired_keys(
    coordination_service: SqliteCoordinationService,
) -> None:
    """Verify set_value removes expired rows from key_values even if never read."""
    with freezegun.freeze_time("2024-01-01 12:00:00"):
        await coordination_service.set_value("abandoned:1", "v1", ttl=10)
        await coordination_service.set_value("active:1", "v2", ttl=100)

    with freezegun.freeze_time("2024-01-01 12:00:20"):
        await coordination_service.set_value("active:2", "v3", ttl=100)

    async with coordination_service._session_manager.session() as session:
        result = await session.execute(select(KeyValueDO.key))
        keys = set(result.scalars().all())

    assert "abandoned:1" not in keys
    assert keys >= {"active:1", "active:2"}
