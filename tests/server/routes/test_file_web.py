import hashlib
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient

from supernote.client.client import Client
from supernote.client.exceptions import BadRequestException
from supernote.client.web import WebClient
from supernote.models.extended import WebSummaryListRequestDTO, WebSummaryListVO
from supernote.models.summary import AddSummaryVO

MOVE_MODAL_PATH = (
    Path(__file__).resolve().parents[3]
    / "supernote"
    / "server"
    / "static"
    / "js"
    / "components"
    / "MoveModal.js"
)


@pytest.fixture
def web_summary_list_url() -> str:
    return "/api/extended/file/summary/list"


async def test_web_summary_list(
    authenticated_client: Client,
    web_summary_list_url: str,
) -> None:
    pass


async def test_web_summary_list_implementation(
    authenticated_client: Client, web_summary_list_url: str
) -> None:
    """Implement the test logic knowing AddSummaryDTO structure."""

    # 1. Add a summary for a specific file
    file_id = 88888
    add_payload = {
        "content": "Extension test",
        "dataSource": "WEB",
        "fileId": file_id,  # AddSummaryDTO has file_id
    }
    # Use AddSummaryVO as response type
    resp = await authenticated_client.post_json(
        "/api/file/add/summary", AddSummaryVO, json=add_payload
    )
    assert resp.id is not None

    # 2. Call the web extension endpoint using post_json and expect WebSummaryListVO
    req_dto = WebSummaryListRequestDTO(file_id=file_id)
    web_resp = await authenticated_client.post_json(
        web_summary_list_url, WebSummaryListVO, json=req_dto.to_dict()
    )

    assert web_resp.success
    assert len(web_resp.summary_do_list) == 1
    assert web_resp.total_records == 1
    assert web_resp.summary_do_list[0].file_id == file_id
    assert web_resp.summary_do_list[0].content == "Extension test"


async def test_web_summary_list_invalid(
    authenticated_client: Client, web_summary_list_url: str
) -> None:
    # Test missing fileId - Should raise BadRequestException
    with pytest.raises(BadRequestException):
        await authenticated_client.post(
            web_summary_list_url,
            json={},
        )

    # Test invalid fileId - Should raise BadRequestException
    with pytest.raises(BadRequestException):
        await authenticated_client.post(
            web_summary_list_url,
            json={"fileId": "invalid"},
        )


async def test_web_upload_apply_returns_part_upload_url_and_standard_inner_name(
    authenticated_client: Client,
) -> None:
    """Verify /api/file/upload/apply returns partUploadUrl and standard innerName."""
    web_client = WebClient(authenticated_client)
    file_name = "2025.01.15.Report (Draft).pdf"
    apply_vo = await web_client.upload_apply(
        directory_id=0,
        file_name=file_name,
        size=1024,
        md5=hashlib.md5(b"test").hexdigest(),
    )
    assert apply_vo.inner_name is not None
    assert apply_vo.inner_name.endswith("-000.pdf")
    assert ".01.15" not in apply_vo.inner_name
    assert apply_vo.full_upload_url is not None
    assert "/api/oss/upload?" in apply_vo.full_upload_url
    assert apply_vo.part_upload_url is not None
    assert "/api/oss/upload/part?" in apply_vo.part_upload_url


async def test_web_client_upload_large_file_multipart(
    authenticated_client: Client,
) -> None:
    """Verify WebClient.upload_file succeeds for multi-chunk files using partUploadUrl."""
    web_client = WebClient(authenticated_client)
    folder = await web_client.create_folder(0, "MultipartUploads")
    folder_id = int(folder.id)

    # > 5MB triggers chunked multipart upload in Client._upload_to_oss
    large_content = b"x" * (5 * 1024 * 1024 + 128)
    expected_md5 = hashlib.md5(large_content).hexdigest()
    await web_client.upload_file(folder_id, "2025.01.15.Large.pdf", large_content)

    listing = await web_client.list_query(folder_id)
    assert len(listing.user_file_vo_list) == 1
    uploaded = listing.user_file_vo_list[0]
    assert uploaded.file_name == "2025.01.15.Large.pdf"
    assert uploaded.size == len(large_content)
    assert uploaded.md5 == expected_md5
    assert uploaded.inner_name is not None
    assert uploaded.inner_name.endswith("-000.pdf")


def test_move_modal_component_structure_and_initial_state() -> None:
    """Verify MoveModal.js defines expected props, emits, data state, and dark mode classes."""
    content = MOVE_MODAL_PATH.read_text(encoding="utf-8")

    assert "name: 'MoveModal'" in content
    assert "props: ['itemIds']" in content
    assert "emits: ['close', 'confirm']" in content

    # Initial browser directory and breadcrumb state
    assert "currentBrowserDirId: '0'" in content
    assert "breadcrumbs: [{ id: '0', name: 'Cloud Root' }]" in content
    assert "targetDirId: '0'" in content

    # Dark mode support classes
    assert "dark:bg-slate-900" in content
    assert "dark:border-slate-800" in content
    assert "dark:text-white" in content


def test_move_modal_subfolder_drilldown_and_breadcrumbs() -> None:
    """Verify MoveModal.js supports drilling into subfolders and navigating breadcrumbs."""
    content = MOVE_MODAL_PATH.read_text(encoding="utf-8")

    # Subfolder loading uses dynamic directory ID rather than hardcoded root only
    assert "async loadFolders(dirId = this.currentBrowserDirId)" in content
    assert "const files = await fetchFiles(targetId);" in content

    # Subfolder drill-down method updates state, breadcrumbs, and loads target folder
    assert "async enterFolder(folder)" in content
    assert "this.currentBrowserDirId = folderId;" in content
    assert "this.breadcrumbs.push({ id: folderId, name: folder.name });" in content
    assert "await this.loadFolders(folderId);" in content

    # Breadcrumb navigation slices trail and reloads the selected ancestor directory
    assert "async navigateToBreadcrumb(index)" in content
    assert "this.breadcrumbs = this.breadcrumbs.slice(0, index + 1);" in content
    assert 'v-for="(crumb, index) in breadcrumbs"' in content
    assert '@click="navigateToBreadcrumb(index)"' in content


def test_move_modal_excludes_selected_item_ids() -> None:
    """Verify MoveModal.js filters out folders whose ID is in props.itemIds as strings."""
    content = MOVE_MODAL_PATH.read_text(encoding="utf-8")

    # String-normalized set of itemIds
    assert "new Set((this.itemIds || []).map(id => String(id)))" in content
    assert "this.excludedIdSet.has(String(id))" in content
    assert (
        "this.folders.filter(folder => !this.excludedIdSet.has(String(folder.id)))"
        in content
    )

    # Guard against selecting, drilling into, or confirming an excluded folder
    assert "if (this.isExcluded(folderId)) return;" in content
    assert "if (this.isExcluded(targetId)) return;" in content
    assert "f => f.isDirectory && !this.isExcluded(f.id)" in content


def test_move_modal_destination_display_and_selection() -> None:
    """Verify MoveModal.js shows the target destination path and supports current folder selection."""
    content = MOVE_MODAL_PATH.read_text(encoding="utf-8")

    assert "destinationPath()" in content
    assert 'data-testid="move-destination"' in content
    assert '@click="selectTarget(currentBrowserDirId)"' in content
    assert '@click="selectTarget(folder.id)"' in content
    assert '@click.stop="enterFolder(folder)"' in content
    assert "this.$emit('confirm', String(this.targetDirId));" in content


async def test_move_modal_served_over_http_and_subfolder_move_flow(
    client: TestClient,
    auth_headers: dict[str, str],
    web_client: WebClient,
) -> None:
    """Verify MoveModal.js is served by the web server and the subfolder move API flow succeeds."""
    resp = await client.get("/static/js/components/MoveModal.js")
    assert resp.status == 200
    js_text = await resp.text()
    assert "navigateToBreadcrumb" in js_text
    assert "enterFolder" in js_text

    # Create nested folder hierarchy: Parent -> Child, plus a Sibling folder to move
    parent_vo = await web_client.create_folder(0, "ParentFolder")
    parent_id = int(parent_vo.id)
    child_vo = await web_client.create_folder(parent_id, "ChildFolder")
    child_id = int(child_vo.id)
    moving_vo = await web_client.create_folder(0, "FolderToMove")
    moving_id = int(moving_vo.id)

    # 1. Query root ("0") as MoveModal does on mount
    root_resp = await client.post(
        "/api/file/list/query",
        json={
            "directoryId": "0",
            "pageNo": 1,
            "pageSize": 50,
            "order": "filename",
            "sequence": "asc",
        },
        headers=auth_headers,
    )
    assert root_resp.status == 200
    root_data = await root_resp.json()
    root_folders = [f for f in root_data["userFileVOList"] if f.get("isFolder") == "Y"]
    root_folder_ids = {str(f["id"]) for f in root_folders}
    assert str(parent_id) in root_folder_ids
    assert str(moving_id) in root_folder_ids

    # Simulate MoveModal filtering out itemIds = [moving_id]
    excluded = {str(moving_id)}
    visible_root_ids = {
        str(f["id"]) for f in root_folders if str(f["id"]) not in excluded
    }
    assert str(moving_id) not in visible_root_ids
    assert str(parent_id) in visible_root_ids

    # 2. Drill into ParentFolder (enterFolder) -> query directoryId = str(parent_id)
    sub_resp = await client.post(
        "/api/file/list/query",
        json={
            "directoryId": str(parent_id),
            "pageNo": 1,
            "pageSize": 50,
            "order": "filename",
            "sequence": "asc",
        },
        headers=auth_headers,
    )
    assert sub_resp.status == 200
    sub_data = await sub_resp.json()
    sub_folders = [f for f in sub_data["userFileVOList"] if f.get("isFolder") == "Y"]
    assert [str(f["id"]) for f in sub_folders] == [str(child_id)]

    # 3. Confirm move of FolderToMove into nested ChildFolder
    move_resp = await client.post(
        "/api/file/move",
        json={
            "idList": [moving_id],
            "directoryId": "0",
            "goDirectoryId": child_id,
        },
        headers=auth_headers,
    )
    assert move_resp.status == 200
    move_data = await move_resp.json()
    assert move_data["success"] is True

    # Verify FolderToMove now resides inside ChildFolder
    child_listing = await web_client.list_query(child_id)
    moved_names = [f.file_name for f in child_listing.user_file_vo_list]
    assert "FolderToMove" in moved_names
