"""
Write FastAPI's OpenAPI schema to purification-rescue-frontend/openapi.json.

The frontend's wire types are generated from that file (`npm run gen:api`,
#46), and tests/test_result_schema.py fails when it is stale. Run after any
change to schemas.py or agent_engine/models.py:

    uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "purification-rescue-frontend" / "openapi.json"

sys.path.insert(0, str(ROOT))


def openapi_json() -> str:
    from main import app

    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(openapi_json(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
