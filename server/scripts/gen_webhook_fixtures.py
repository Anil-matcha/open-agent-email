"""Regenerate fixtures/webhook_signatures.json from the server's signing code.

Only run this after a deliberate change to the webhook signing scheme: the fixture is the
contract that the server, TypeScript SDK and Python SDK tests all assert against.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

OUT = ROOT.parent / "fixtures" / "webhook_signatures.json"

from app.services.webhook_delivery import signature_headers  # noqa: E402
from app.services.webhook_events import build_payload  # noqa: E402

EVENT_ID = "evt_0123456789abcdef"
TIMESTAMP = "1800000000"


def main() -> None:
    body = json.dumps(build_payload("email.received", {
        "id": EVENT_ID, "message_id": "msg_abc", "agent_email": "bot@agents.local",
        "direction": "inbound", "sender": "a@example.com", "recipient": "bot@agents.local",
        "status": "delivered", "thread_id": "th_1", "created_at": "2027-01-15T08:00:00",
    }), separators=(",", ":"), ensure_ascii=False)
    cases = []
    for name, secrets in (
        ("single secret", ["whsec_golden_current"]),
        ("rotation overlap", ["whsec_golden_current", "whsec_golden_previous"]),
    ):
        cases.append({
            "name": name, "timestamp": TIMESTAMP, "event_id": EVENT_ID, "event_type": "email.received",
            "secrets": secrets, "body": body,
            "expected_headers": signature_headers(TIMESTAMP, EVENT_ID, "email.received", body.encode(), secrets),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "description": "Golden webhook signing vectors shared by the server, TypeScript and Python tests. "
                       "Webhook-Signature signs `<t>.<body>`; X-Webhook-Signature signs `<t>.<event id>.<body>`.",
        "cases": cases,
    }, indent=2) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
