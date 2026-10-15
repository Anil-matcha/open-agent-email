"""Write the generated OpenAPI document to api/openapi.snapshot.json.

Run after an intentional API change:  python scripts/export_openapi.py
`tests/test_openapi_contract.py` fails until the committed snapshot matches the app.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

SNAPSHOT = ROOT.parent / "api" / "openapi.snapshot.json"


def current_spec() -> dict:
    from app.main import app
    return app.openapi()


def render(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(render(current_spec()))
    print(f"wrote {SNAPSHOT}")
