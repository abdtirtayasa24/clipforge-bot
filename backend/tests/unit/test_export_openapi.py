import json
from pathlib import Path

from scripts.export_openapi import DEFAULT_OUTPUT, export_openapi, openapi_document


def test_export_writes_the_api_schema(tmp_path: Path) -> None:
    output = tmp_path / "api" / "openapi.json"

    export_openapi(output)

    document = json.loads(output.read_text(encoding="utf-8"))
    assert "/api/v1/clips" in document["paths"]
    assert "post" in document["paths"]["/api/v1/clips/{clip_id}/review"]


def test_committed_schema_is_up_to_date() -> None:
    committed = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert committed == openapi_document(), "run: python scripts/export_openapi.py"
