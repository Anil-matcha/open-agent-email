"""Public API contract gate: the generated OpenAPI document must match the committed snapshot."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import export_openapi  # noqa: E402

HOW = "If this change is intentional, run `python scripts/export_openapi.py` and commit api/openapi.snapshot.json."
METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(spec):
    return {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops if m in METHODS}


def test_no_endpoint_was_removed_or_renamed():
    committed = json.loads(export_openapi.SNAPSHOT.read_text())
    missing = _operations(committed) - _operations(export_openapi.current_spec())
    assert not missing, f"Endpoints removed or renamed (breaking): {sorted(missing)}. {HOW}"


def test_snapshot_is_current():
    committed = export_openapi.SNAPSHOT.read_text()
    assert committed == export_openapi.render(export_openapi.current_spec()), f"API surface changed. {HOW}"


def test_every_endpoint_has_a_summary_or_docstring_tag():
    spec = export_openapi.current_spec()
    untagged = [(m, p) for p, ops in spec["paths"].items() for m, op in ops.items()
                if m in METHODS and not op.get("tags")]
    allowed = {("get", "/"), ("get", "/health"), ("get", "/ready"), ("get", "/api/health")}
    assert [x for x in untagged if x not in allowed] == [], "Every public endpoint should carry a tag"
