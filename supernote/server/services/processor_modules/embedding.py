import json
import logging

from sqlalchemy import or_, select
from sqlalchemy.sql import Select

from supernote.server.ai.models.embedding import EmbeddingModel, EmbeddingTaskType
from supernote.server.db.models.note_processing import NotePageContentDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.services.file import FileService
from supernote.server.services.processor_modules import ProcessorModule
from supernote.server.utils.note_content import get_page_content_by_id

logger = logging.getLogger(__name__)


class EmbeddingModule(ProcessorModule):
    """Module responsible for generating embeddings for note pages using an `EmbeddingModel`."""

    def __init__(
        self,
        file_service: FileService,
        embedding_model: EmbeddingModel | None,
    ) -> None:
        self.file_service = file_service
        self.embedding_model = embedding_model

    @property
    def name(self) -> str:
        return "EmbeddingModule"

    @property
    def task_type(self) -> str:
        return "EMBEDDING_GENERATION"

    def missing_tasks_file_ids_stmt(self) -> Select[tuple[int]] | None:
        if self.embedding_model is None:
            return None
        return (
            select(NotePageContentDO.file_id)
            .where(NotePageContentDO.text_content.isnot(None))
            .where(NotePageContentDO.text_content != "")
            .where(
                or_(
                    NotePageContentDO.embedding.is_(None),
                    NotePageContentDO.embedding_model.is_(None),
                    NotePageContentDO.embedding_model != self.embedding_model.model_id,
                )
            )
        )

    async def run_if_needed(
        self,
        file_id: int,
        session_manager: DatabaseSessionManager,
        page_index: int | None = None,
        page_id: str | None = None,
    ) -> bool:
        if not page_id:
            return False

        if self.embedding_model is None:
            return False

        async with session_manager.session() as session:
            # Check Prerequisites (Text Content must exist)
            content = await get_page_content_by_id(session, file_id, page_id)

            if not content or not content.text_content:
                # Dependency not met yet (OCR not done or empty page)
                return False

            if (
                content.embedding is None
                or content.embedding_model != self.embedding_model.model_id
            ):
                return True

        return await super().run_if_needed(
            file_id, session_manager, page_index, page_id
        )

    async def process(
        self,
        file_id: int,
        session_manager: DatabaseSessionManager,
        page_index: int | None = None,
        page_id: str | None = None,
        **kwargs: object,
    ) -> None:
        if not page_id:
            return

        if self.embedding_model is None:
            raise ValueError("Embedding model not configured")

        # Get Text Content
        text_content = ""
        async with session_manager.session() as session:
            content = await get_page_content_by_id(session, file_id, page_id)

            if not content or not content.text_content:
                logger.warning(
                    f"No text content found for embedding: file {file_id} page {page_id} (idx {page_index})"
                )
                return
            text_content = content.text_content

        embedding_values = await self.embedding_model.embed(
            text_content, task_type=EmbeddingTaskType.RETRIEVAL_DOCUMENT
        )
        embedding_json = json.dumps(embedding_values)

        # Save Result
        async with session_manager.session() as session:
            content = await get_page_content_by_id(session, file_id, page_id)

            if content:
                content.embedding = embedding_json
                content.embedding_model = self.embedding_model.model_id
            await session.commit()

        logger.info(f"Completed Embedding for file {file_id} page {page_index}")
