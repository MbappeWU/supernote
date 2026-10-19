import json

import pytest
from sqlalchemy import select

from supernote.server.ai.models.embedding import EmbeddingTaskType
from supernote.server.ai.models.fake.embedding import FakeEmbeddingModel
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.note_processing import NotePageContentDO, SystemTaskDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.file import FileService
from supernote.server.services.processor_modules.embedding import EmbeddingModule


@pytest.fixture
def embedding_module(
    file_service: FileService,
    fake_embedding_model: FakeEmbeddingModel,
) -> EmbeddingModule:
    return EmbeddingModule(
        file_service=file_service,
        embedding_model=fake_embedding_model,
    )


async def test_process_embedding_success(
    embedding_module: EmbeddingModule,
    session_manager: DatabaseSessionManager,
    fake_embedding_model: FakeEmbeddingModel,
) -> None:
    user_id = 100
    file_id = 999
    page_index = 0
    storage_key = "test_note_storage_key"

    async with session_manager.session() as session:
        user_file = UserFileDO(
            id=file_id,
            user_id=user_id,
            storage_key=storage_key,
            file_name="real.note",
            directory_id=0,
        )
        session.add(user_file)

        content = NotePageContentDO(
            file_id=file_id,
            page_index=page_index,
            page_id="p0",
            content_hash="somehash",
            text_content="This is the text to embed.",
        )
        session.add(content)
        await session.commit()

    await embedding_module.run(
        file_id, session_manager, page_index=page_index, page_id="p0"
    )

    assert fake_embedding_model.calls == [
        ("This is the text to embed.", EmbeddingTaskType.RETRIEVAL_DOCUMENT)
    ]

    async with session_manager.session() as session:
        updated_content = (
            (
                await session.execute(
                    select(NotePageContentDO)
                    .where(NotePageContentDO.file_id == file_id)
                    .where(NotePageContentDO.page_index == page_index)
                )
            )
            .scalars()
            .first()
        )

        assert updated_content is not None
        assert updated_content.embedding is not None
        embedding_list = json.loads(updated_content.embedding)
        assert embedding_list == await fake_embedding_model.embed(
            "This is the text to embed.",
            task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT,
        )

        task = (
            (
                await session.execute(
                    select(SystemTaskDO)
                    .where(SystemTaskDO.file_id == file_id)
                    .where(SystemTaskDO.task_type == "EMBEDDING_GENERATION")
                    .where(SystemTaskDO.key == "page_p0")
                )
            )
            .scalars()
            .first()
        )

        assert task is not None
        assert task.status == "COMPLETED"


async def test_embedding_run_if_needed_disabled(
    file_service: FileService,
    session_manager: DatabaseSessionManager,
) -> None:
    module = EmbeddingModule(file_service=file_service, embedding_model=None)

    assert (
        await module.run_if_needed(1, session_manager, page_index=0, page_id="p0")
        is False
    )

    # run() should still return True (skipped success)
    assert await module.run(1, session_manager, page_index=0, page_id="p0") is True
