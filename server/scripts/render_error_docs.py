"""Regenerate docs/errors.md from the error catalog:  python scripts/render_error_docs.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.errors import CATALOG  # noqa: E402

DOC = ROOT.parent / "docs" / "errors.md"

HEADER = """# Error responses

Every error keeps its HTTP status and the historical `detail` field, and adds a structured `error` object:

```json
{
  "detail": "Token is not authorized for this agent inbox",
  "error": {"code": "forbidden_inbox", "message": "Token is not authorized for this agent inbox", "details": null}
}
```

- `error.code` is stable and safe to branch on; `message` is for humans and may change.
- Validation failures (`422`) set `code` to `validation_error` and `details` to a list of `{path, message, type}`; `detail` keeps FastAPI's original list.
- Rate limits (`429`) send a `Retry-After` header and `error.details.retry_after_seconds`.
- An unrecognized code should be handled by its HTTP status.
- The TypeScript and Python SDKs expose `code`, `details` and `retryAfterSeconds` / `retry_after_seconds` on their error type, with `RateLimitError` and `IdempotencyKeyReuseError` subclasses; `oae` prints the code (`error: ... [rate_limited]: ...`).

| Code | HTTP | Meaning | Retry guidance |
|---|---|---|---|
"""


def render() -> str:
    rows = "".join(
        f"| `{code}` | {entry.status} | {entry.meaning} | {entry.retry} |\n"
        for code, entry in sorted(CATALOG.items(), key=lambda item: (item[1].status, item[0]))
    )
    return HEADER + rows


if __name__ == "__main__":
    DOC.write_text(render())
    print(f"wrote {DOC}")
