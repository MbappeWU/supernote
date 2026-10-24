"""Tests for `supernote.server.ai.models.gemini.summary`."""

from __future__ import annotations

import json
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supernote.server.ai.base import AIError
from supernote.server.ai.models.gemini.backend import GeminiBackend
from supernote.server.ai.models.gemini.summary import GeminiSummaryModel
from supernote.server.ai.prompts import (
    SummaryResponse,
    SummarySegment,
    format_summary_prompt,
)

SEGMENTS = {
    "segments": [
        {
            "date_range": "2023-10-27",
            "summary": "AI Summary Output",
            "extracted_dates": ["2023-10-27"],
            "page_refs": [1, 2],
        }
    ]
}
EXPECTED = SummaryResponse(
    segments=[
        SummarySegment(
            date_range="2023-10-27",
            summary="AI Summary Output",
            extracted_dates=["2023-10-27"],
            page_refs=[1, 2],
        )
    ]
)


@pytest.fixture
def mock_gemini_client() -> Generator[MagicMock]:
    with patch("google.genai.Client") as mock_client_cls:
        yield mock_client_cls.return_value


@pytest.fixture
def summary_model(mock_gemini_client: MagicMock) -> GeminiSummaryModel:
    backend = GeminiBackend(api_key="fake-key")
    backend._client = mock_gemini_client
    return GeminiSummaryModel(backend, "gemini-2.0-flash-exp")


async def test_gemini_summary_model(
    summary_model: GeminiSummaryModel,
    mock_gemini_client: MagicMock,
) -> None:
    mock_sum_resp = MagicMock(text=json.dumps(SEGMENTS))
    mock_gemini_client.aio.models.generate_content = AsyncMock(
        return_value=mock_sum_resp
    )

    summary_prompt = format_summary_prompt("Page 1 text", custom_type="real")
    assert await summary_model.summarize(summary_prompt) == EXPECTED
    assert summary_model.model_id == "gemini-2.0-flash-exp"


async def test_gemini_summary_model_strips_code_fence(
    summary_model: GeminiSummaryModel,
    mock_gemini_client: MagicMock,
) -> None:
    mock_fenced_resp = MagicMock(text=f"```json\n{json.dumps(SEGMENTS)}\n```")
    mock_gemini_client.aio.models.generate_content = AsyncMock(
        return_value=mock_fenced_resp
    )
    assert await summary_model.summarize("Page 1 text") == EXPECTED


async def test_gemini_summary_model_empty_response(
    summary_model: GeminiSummaryModel,
    mock_gemini_client: MagicMock,
) -> None:
    mock_empty_resp = MagicMock(text="")
    mock_gemini_client.aio.models.generate_content = AsyncMock(
        return_value=mock_empty_resp
    )
    assert await summary_model.summarize("Page 1 text") == SummaryResponse(segments=[])


@pytest.mark.parametrize(
    "invalid_payload",
    [
        "not valid json",
        "{}",
        json.dumps({"segments": [{"date_range": "2023-10-27"}]}),
        json.dumps({"segments": "not-a-list"}),
    ],
)
async def test_gemini_summary_model_invalid_json(
    summary_model: GeminiSummaryModel,
    mock_gemini_client: MagicMock,
    invalid_payload: str,
) -> None:
    mock_bad_resp = MagicMock(text=invalid_payload)
    mock_gemini_client.aio.models.generate_content = AsyncMock(
        return_value=mock_bad_resp
    )
    with pytest.raises(AIError, match="Failed to parse"):
        await summary_model.summarize("Page 1 text")
