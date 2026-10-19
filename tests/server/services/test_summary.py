"""Unit tests for SummaryService."""

import pytest

from supernote.models.summary import (
    AddSummaryDTO,
    AddSummaryGroupDTO,
    QuerySummaryDTO,
    QuerySummaryGroupDTO,
    UpdateSummaryDTO,
    UpdateSummaryGroupDTO,
)
from supernote.models.user import UserRegisterDTO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.exceptions import SummaryNotFound
from supernote.server.services.summary import SummaryService
from supernote.server.services.user import UserService

USER_EMAIL = "test@example.com"


@pytest.fixture
async def summary_service(
    user_service: UserService,
    session_manager: DatabaseSessionManager,
) -> SummaryService:
    """Create a SummaryService bound to the test session manager with a registered test user."""
    await user_service.register(UserRegisterDTO(email=USER_EMAIL, password="a" * 32))
    return SummaryService(user_service=user_service, session_manager=session_manager)


async def test_tag_crud_and_not_found_errors(
    summary_service: SummaryService,
) -> None:
    """Verify tag creation, update, deletion, and not-found error handling."""
    tag = await summary_service.add_tag(USER_EMAIL, "Important")
    assert tag.id is not None
    assert tag.name == "Important"

    assert await summary_service.update_tag(USER_EMAIL, tag.id, "Urgent")
    tags = await summary_service.list_tags(USER_EMAIL)
    assert [t.name for t in tags] == ["Urgent"]

    assert await summary_service.delete_tag(USER_EMAIL, tag.id)
    assert await summary_service.list_tags(USER_EMAIL) == []

    with pytest.raises(SummaryNotFound):
        await summary_service.update_tag(USER_EMAIL, 99999, "Missing")

    with pytest.raises(SummaryNotFound):
        await summary_service.delete_tag(USER_EMAIL, 99999)


async def test_summary_crud_and_lookup_helpers(
    summary_service: SummaryService,
) -> None:
    """Verify summary creation, updates, UUID/file lookups, and not-found errors."""
    created = await summary_service.add_summary(
        USER_EMAIL,
        AddSummaryDTO(
            file_id=42,
            unique_identifier="sum-uuid-1",
            parent_unique_identifier="grp-uuid-1",
            content="Initial content",
            source_path="/Note/Test.note",
            data_source="OCR",
            tags="t1",
            metadata='{"k": "v"}',
        ),
    )
    assert created.id is not None
    assert created.unique_identifier == "sum-uuid-1"

    by_uuid = await summary_service.get_summary_by_uuid(USER_EMAIL, "sum-uuid-1")
    assert by_uuid == created
    assert await summary_service.get_summary_by_uuid(USER_EMAIL, "missing-uuid") is None
    assert await summary_service.get_summary_by_uuid(USER_EMAIL, "") is None

    for_file = await summary_service.list_summaries_for_file_internal(USER_EMAIL, 42)
    assert for_file == [created]

    assert await summary_service.update_summary(
        USER_EMAIL,
        UpdateSummaryDTO(
            id=created.id,
            parent_unique_identifier="grp-uuid-2",
            content="Updated content",
            source_path="/Note/Moved.note",
            data_source="USER",
            tags="t2",
            metadata='{"k": "v2"}',
            last_modified_time=1700000005000,
            md5_hash="custom-md5",
        ),
    )
    fetched = await summary_service.get_summary(USER_EMAIL, created.id)
    assert fetched.parent_unique_identifier == "grp-uuid-2"
    assert fetched.content == "Updated content"
    assert fetched.source_path == "/Note/Moved.note"
    assert fetched.data_source == "USER"
    assert fetched.tags == "t2"
    assert fetched.metadata == '{"k": "v2"}'
    assert fetched.last_modified_time == 1700000005000
    assert fetched.md5_hash == "custom-md5"

    assert await summary_service.delete_summary(USER_EMAIL, created.id)

    with pytest.raises(SummaryNotFound):
        await summary_service.update_summary(
            USER_EMAIL, UpdateSummaryDTO(id=99999, content="Missing")
        )

    with pytest.raises(SummaryNotFound):
        await summary_service.delete_summary(USER_EMAIL, 99999)

    with pytest.raises(SummaryNotFound):
        await summary_service.get_summary(USER_EMAIL, 99999)


async def test_group_crud_and_fallback_lookup_by_id(
    summary_service: SummaryService,
) -> None:
    """Verify group creation, update by UUID and ID fallback, and not-found errors."""
    group = await summary_service.add_group(
        USER_EMAIL,
        AddSummaryGroupDTO(
            unique_identifier="group-uuid-1",
            name="Group 1",
            md5_hash="h1",
            description="Desc 1",
        ),
    )
    assert group.id is not None

    assert await summary_service.update_group(
        USER_EMAIL,
        UpdateSummaryGroupDTO(
            id=group.id,
            unique_identifier=None,
            name="Group 1 Updated",
            description="Desc 2",
            md5_hash="h2",
            last_modified_time=1700000010000,
        ),
    )
    groups = await summary_service.list_groups(USER_EMAIL, QuerySummaryGroupDTO())
    assert len(groups) == 1
    assert groups[0].name == "Group 1 Updated"
    assert groups[0].description == "Desc 2"
    assert groups[0].md5_hash == "h2"
    assert groups[0].last_modified_time == 1700000010000

    with pytest.raises(SummaryNotFound):
        await summary_service.update_group(
            USER_EMAIL,
            UpdateSummaryGroupDTO(
                id=99999,
                unique_identifier="missing-group-uuid",
                md5_hash="h3",
            ),
        )


async def test_pagination_and_unpaginated_queries(
    summary_service: SummaryService,
) -> None:
    """Verify list_summaries, list_summary_infos, list_summaries_by_id, list_groups, and counts."""
    created_ids: list[int] = []
    for idx in range(25):
        item = await summary_service.add_summary(
            USER_EMAIL,
            AddSummaryDTO(
                unique_identifier=f"svc-sum-{idx}",
                parent_unique_identifier="parent-a" if idx < 15 else "parent-b",
                content=f"Content {idx}",
            ),
        )
        assert item.id is not None
        created_ids.append(item.id)

    for idx in range(3):
        await summary_service.add_group(
            USER_EMAIL,
            AddSummaryGroupDTO(
                unique_identifier=f"svc-grp-{idx}",
                name=f"Grp {idx}",
                md5_hash=f"gh-{idx}",
            ),
        )

    assert await summary_service.count_summaries(USER_EMAIL) == 25
    assert (
        await summary_service.count_summaries(USER_EMAIL, parent_uuid="parent-a") == 15
    )
    assert await summary_service.count_summaries(USER_EMAIL, ids=created_ids[:5]) == 5
    assert await summary_service.count_groups(USER_EMAIL) == 3

    all_summaries = await summary_service.list_summaries(USER_EMAIL, size=None)
    assert [s.id for s in all_summaries] == created_ids

    zero_size_summaries = await summary_service.list_summaries(
        USER_EMAIL, page=0, size=0
    )
    assert [s.id for s in zero_size_summaries] == created_ids

    all_infos = await summary_service.list_summary_infos(
        USER_EMAIL, QuerySummaryDTO(page=0, size=0)
    )
    assert [i.id for i in all_infos] == created_ids

    by_ids = await summary_service.list_summaries_by_id(
        USER_EMAIL, QuerySummaryDTO(ids=created_ids)
    )
    assert [s.id for s in by_ids] == created_ids

    all_groups = await summary_service.list_groups(
        USER_EMAIL, QuerySummaryGroupDTO(page=0, size=0)
    )
    assert len(all_groups) == 3
