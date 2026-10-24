from pathlib import Path

import pytest

from supernote.server.ai.models.fake.embedding import FakeEmbeddingModel
from supernote.server.ai.models.fake.ocr import FakeOcrModel
from supernote.server.ai.models.fake.summary import FakeSummaryModel
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.events import LocalEventBus
from supernote.server.services.blob import BlobStorage
from supernote.server.services.file import FileService
from supernote.server.services.summary import SummaryService
from supernote.server.services.user import UserService


@pytest.fixture
def file_service(
    storage_root: Path,
    blob_storage: BlobStorage,
    user_service: UserService,
    session_manager: DatabaseSessionManager,
) -> FileService:
    return FileService(
        storage_root=storage_root,
        blob_storage=blob_storage,
        user_service=user_service,
        session_manager=session_manager,
        event_bus=LocalEventBus(),
    )


@pytest.fixture
def summary_service(
    user_service: UserService,
    session_manager: DatabaseSessionManager,
) -> SummaryService:
    return SummaryService(
        user_service=user_service,
        session_manager=session_manager,
    )


@pytest.fixture
def fake_ocr_model() -> FakeOcrModel:
    return FakeOcrModel()


@pytest.fixture
def fake_embedding_model() -> FakeEmbeddingModel:
    return FakeEmbeddingModel()


@pytest.fixture
def fake_summary_model() -> FakeSummaryModel:
    return FakeSummaryModel()
