import json

from supernote.server.mcp.models import (
    ListNotebooksResponseVO,
    NotebookVO,
    SearchResponseVO,
    SearchResultVO,
    TranscriptRequestDTO,
)
from supernote.server.utils.unique_id import next_id


def test_notebook_vo_preserves_64bit_file_id_precision() -> None:
    """Verify NotebookVO serializes 63-bit file_id as a string to prevent JS float64 rounding."""
    # 753199434786931100 exceeds 2^53 - 1 and rounds to 753199434786931072 in IEEE-754 float64
    snowflake_id = 753199434786931100
    assert int(float(str(snowflake_id))) != snowflake_id

    vo = ListNotebooksResponseVO(
        notebooks=[
            NotebookVO(
                file_id=str(snowflake_id),
                file_name="Daily.note",
                create_time=1700000000000,
                update_time=1700000005000,
            )
        ]
    )

    # Simulate a JavaScript JSON parser that converts all JSON numbers via IEEE-754 float64
    parsed = json.loads(vo.to_json(), parse_int=lambda s: int(float(s)))
    assert parsed == {
        "success": True,
        "errorCode": None,
        "errorMsg": None,
        "notebooks": [
            {
                "fileId": "753199434786931100",
                "fileName": "Daily.note",
                "createTime": 1700000000000,
                "updateTime": 1700000005000,
            }
        ],
    }

    req = TranscriptRequestDTO.from_dict({"fileId": parsed["notebooks"][0]["fileId"]})
    assert req.to_dict() == {
        "fileId": "753199434786931100",
        "startIndex": None,
        "endIndex": None,
    }


def test_search_result_vo_preserves_next_id_precision() -> None:
    """Verify SearchResultVO serializes generated next_id() values as strings."""
    generated_id = next_id()
    response = SearchResponseVO(
        results=[
            SearchResultVO(
                file_id=str(generated_id),
                file_name="Notes.note",
                page_index=2,
                page_id="P003",
                score=0.95,
                text_preview="Meeting summary",
                date="2026-10-25",
            )
        ]
    )

    parsed = json.loads(response.to_json(), parse_int=lambda s: int(float(s)))
    assert parsed == {
        "success": True,
        "errorCode": None,
        "errorMsg": None,
        "results": [
            {
                "fileId": str(generated_id),
                "fileName": "Notes.note",
                "pageIndex": 2,
                "pageId": "P003",
                "score": 0.95,
                "textPreview": "Meeting summary",
                "date": "2026-10-25",
            }
        ],
    }
    assert int(parsed["results"][0]["fileId"]) == generated_id
