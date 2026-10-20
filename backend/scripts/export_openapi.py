"""Writes the OpenAPI schema for the frontend's typed client: `python scripts/export_openapi.py`."""

import json
import sys
from pathlib import Path
from typing import Any

from clipforge.config import Settings
from clipforge.main import create_app

DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"


def openapi_document() -> dict[str, Any]:
    return create_app(Settings()).openapi()


def export_openapi(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(openapi_document(), indent=2, ensure_ascii=False)
    output.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    export_openapi(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
