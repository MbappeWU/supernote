import hashlib

import pytest

from supernote.client.client import Client
from supernote.client.exceptions import BadRequestException
from supernote.client.web import WebClient
from supernote.models.extended import WebSummaryListRequestDTO, WebSummaryListVO
from supernote.models.summary import AddSummaryVO


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
