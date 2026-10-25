import json
from collections.abc import Generator
from typing import Any, cast

import pytest
from mcp.server.auth.middleware.auth_context import AuthenticatedUser, auth_context_var
from mcp.server.mcpserver import Context

from supernote.models.base import ErrorCode
from supernote.server.ai.models.embedding import EmbeddingTaskType
from supernote.server.ai.models.fake.embedding import FakeEmbeddingModel
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.note_processing import NotePageContentDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.mcp.models import SupernoteAccessToken
from supernote.server.mcp.server import (
    get_notebook_transcript,
    list_notebooks,
    search_notebook_chunks,
    set_services,
)
from supernote.server.services.coordination import CoordinationService
from supernote.server.services.search import SearchService
from supernote.server.services.user import UserService
from tests.server.conftest import TEST_USERNAME


@pytest.fixture
def fake_embedding_model() -> FakeEmbeddingModel:
    return FakeEmbeddingModel()


@pytest.fixture
def mcp_search_service(
    session_manager: DatabaseSessionManager,
    fake_embedding_model: FakeEmbeddingModel,
    user_service: UserService,
    coordination_service: CoordinationService,
) -> SearchService:
    service = SearchService(
        session_manager=session_manager,
        embedding_model=fake_embedding_model,
    )
    set_services(
        search_service=service,
        user_service=user_service,
        coordination_service=coordination_service,
    )
    return service


@pytest.fixture
def mcp_auth_context(create_test_user: None) -> Generator[Context]:
    access_token = SupernoteAccessToken(
        token="test-mcp-token",
        user_id=TEST_USERNAME,
        client_id="test-client",
        scopes=["supernote:all"],
    )
    token_var = auth_context_var.set(AuthenticatedUser(access_token))
    try:
        yield cast(Context, object())
    finally:
        auth_context_var.reset(token_var)


async def test_mcp_roundtrip_with_63bit_file_id(
    mcp_search_service: SearchService,
    session_manager: DatabaseSessionManager,
    fake_embedding_model: FakeEmbeddingModel,
    test_user_id: int,
    mcp_auth_context: Context,
) -> None:
    """Verify 63-bit file_id round-trips through JSON float64 parsing across MCP tools."""
    # 753199434786931100 loses precision in IEEE-754 float64 (rounds to 753199434786931072)
    snowflake_file_id = 753199434786931100
    embedding_vec = await fake_embedding_model.embed(
        "architecture", task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT
    )

    async with session_manager.session() as session:
        session.add(
            UserFileDO(
                id=snowflake_file_id,
                user_id=test_user_id,
                directory_id=0,
                file_name="Architecture.note",
                create_time=1700000000000,
                update_time=1700000005000,
            )
        )
        session.add(
            NotePageContentDO(
                file_id=snowflake_file_id,
                page_index=0,
                page_id="P001",
                text_content="2026-10-25 Architecture notes",
                embedding=json.dumps(embedding_vec),
                embedding_model=fake_embedding_model.model_id,
            )
        )
        await session.commit()

    list_res = await list_notebooks(mcp_auth_context, name_filter="Architecture")
    list_json = json.loads(json.dumps(list_res), parse_int=lambda s: int(float(s)))
    assert list_json == {
        "success": True,
        "errorCode": None,
        "errorMsg": None,
        "notebooks": [
            {
                "fileId": "753199434786931100",
                "fileName": "Architecture.note",
                "createTime": 1700000000000,
                "updateTime": 1700000005000,
            }
        ],
    }

    search_res = await search_notebook_chunks(mcp_auth_context, query="architecture")
    search_json: dict[str, Any] = json.loads(
        json.dumps(search_res), parse_int=lambda s: int(float(s))
    )
    assert len(search_json["results"]) == 1
    assert search_json["results"][0]["fileId"] == "753199434786931100"
    assert search_json["results"][0]["fileName"] == "Architecture.note"

    transcript_res = await get_notebook_transcript(
        mcp_auth_context,
        file_id=search_json["results"][0]["fileId"],
    )
    assert transcript_res == {
        "success": True,
        "errorCode": None,
        "errorMsg": None,
        "transcript": (
            "--- Page 1 ---\n"
            "Notebook Filename: Architecture.note\n"
            "Notebook Created: 2023-11-14 22:13:20\n"
            "Page ID: P001\n"
            "---\n"
            "2026-10-25 Architecture notes"
        ),
    }


async def test_get_notebook_transcript_invalid_file_id(
    mcp_search_service: SearchService,
    mcp_auth_context: Context,
) -> None:
    """Verify get_notebook_transcript returns BAD_REQUEST on malformed file_id strings."""
    res = await get_notebook_transcript(mcp_auth_context, file_id="not-a-number")
    assert res == {
        "success": False,
        "errorCode": ErrorCode.BAD_REQUEST,
        "errorMsg": "Invalid file_id 'not-a-number': must be a numeric ID string.",
    }
