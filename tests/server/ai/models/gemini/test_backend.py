"""Tests for `supernote.server.ai.models.gemini.backend`."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from supernote.server.ai.base import AIError
from supernote.server.ai.models.gemini.backend import (
    GeminiBackend,
    create_gemini_backend,
)
from supernote.server.config import ProviderConfig


def test_create_gemini_backend() -> None:
    assert create_gemini_backend({}) is None
    assert create_gemini_backend({"gemini": ProviderConfig(api_key="  ")}) is None

    backend = create_gemini_backend({"gemini": ProviderConfig(api_key="valid-key")})
    assert isinstance(backend, GeminiBackend)


async def test_gemini_backend_translates_client_errors_to_ai_error() -> None:
    with patch("google.genai.Client") as mock_client_cls:
        backend = GeminiBackend(api_key="fake-key")
        mock_client = mock_client_cls.return_value
        backend._client = mock_client

        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=TimeoutError("network timeout")
        )
        with pytest.raises(AIError, match="network timeout"):
            await backend.generate_content(model="gemini-2.0-flash", contents="hello")

        mock_client.aio.models.embed_content = AsyncMock(
            side_effect=TimeoutError("network timeout")
        )
        with pytest.raises(AIError, match="network timeout"):
            await backend.embed_content(model="text-embedding-004", contents="hello")

        # Existing AIError should not be double-wrapped
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=AIError("already wrapped")
        )
        with pytest.raises(AIError, match=r"^already wrapped$"):
            await backend.generate_content(model="gemini-2.0-flash", contents="hello")

        # Programming bugs (e.g. AttributeError) should not be masked as AIError
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=AttributeError("unexpected bug")
        )
        with pytest.raises(AttributeError, match="unexpected bug"):
            await backend.generate_content(model="gemini-2.0-flash", contents="hello")
