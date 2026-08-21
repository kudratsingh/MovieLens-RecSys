"""Generate or verify the committed FastAPI OpenAPI contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.serving.app import app

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPOSITORY_ROOT / "docs" / "api" / "openapi.json"


def rendered_schema() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed OpenAPI artifact differs from the application",
    )
    args = parser.parse_args()
    rendered = rendered_schema()
    if args.check:
        committed = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else ""
        if committed != rendered:
            raise SystemExit(
                "docs/api/openapi.json is stale; run python scripts/generate_openapi.py"
            )
        return
    OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    OPENAPI_PATH.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
