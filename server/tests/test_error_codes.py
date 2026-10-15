"""Structured error envelope: representative statuses, middleware responses, and catalog/doc sync."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.errors import CATALOG, classify
from app.rate_limit import ApiRateLimitMiddleware
from app.request_limits import RequestBodyLimitMiddleware
from tests.conftest import ADMIN, make_agent, make_key

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = {"recipient": "to@example.com", "subject": "hello", "body_text": "body"}


def assert_envelope(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert "detail" in body, "legacy detail must be preserved"
    assert body["error"]["code"] == code, body
    assert body["error"]["message"]
    assert "details" in body["error"]
    return body


async def test_401_unauthorized_keeps_www_authenticate(client):
    r = await client.get("/v1/agents")
    body = assert_envelope(r, 401, "unauthorized")
    assert body["detail"] == "Missing or invalid bearer token"
    assert r.headers["www-authenticate"] == "Bearer"


async def test_403_scope_inbox_and_admin(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    full, _ = await make_key(client, "a@agents.local", "full_access")
    assert_envelope(await client.post("/v1/agents/a@agents.local/messages", headers=ro, json=PAYLOAD), 403, "forbidden_scope")
    assert_envelope(await client.get("/v1/agents/b@agents.local/messages", headers=full), 403, "forbidden_inbox")
    assert_envelope(await client.get("/v1/agents", headers=full), 403, "admin_required")


async def test_404_not_found_including_unknown_route(client):
    assert_envelope(await client.get("/v1/agents/nobody@agents.local", headers=ADMIN), 404, "not_found")
    assert_envelope(await client.get("/v1/definitely-not-a-route", headers=ADMIN), 404, "not_found")


async def test_422_idempotency_reuse_and_validation(client):
    await make_agent(client, "a@agents.local")
    h = {**ADMIN, "Idempotency-Key": "k1"}
    base = "/v1/agents/a@agents.local/messages"
    assert (await client.post(base, headers=h, json=PAYLOAD)).status_code == 201
    assert_envelope(await client.post(base, headers=h, json={**PAYLOAD, "body_text": "different"}), 422, "idempotency_key_reuse")
    assert_envelope(await client.post(base, headers={**ADMIN, "Idempotency-Key": "x" * 129}, json=PAYLOAD), 400, "idempotency_key_invalid")


async def test_422_validation_lists_field_paths_and_keeps_fastapi_detail(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"display_name": 5})
    body = assert_envelope(r, 422, "validation_error")
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"]  # unchanged FastAPI shape
    paths = {d["path"] for d in body["error"]["details"]}
    assert "body.email" in paths
    assert all({"path", "message", "type"} <= set(d) for d in body["error"]["details"])


async def test_422_suppressed_recipient(client):
    await make_agent(client, "a@agents.local")
    await client.post("/v1/agents/a@agents.local/suppressions", headers=ADMIN, json={"email": "to@example.com"})
    assert_envelope(await client.post("/v1/agents/a@agents.local/messages", headers=ADMIN, json=PAYLOAD), 422, "suppressed_recipient")


async def test_503_encryption_key_missing(client, monkeypatch):
    await make_agent(client, "a@agents.local")
    monkeypatch.setattr("app.services.smtp_secrets.settings.SMTP_CONFIG_ENCRYPTION_KEY", "")
    r = await client.post("/v1/webhooks", headers=ADMIN, json={
        "agent_email": "a@agents.local", "url": "https://example.com/hook", "events": ["*"]})
    assert_envelope(r, 503, "encryption_key_missing")


async def test_413_from_body_limit_middleware():
    app = FastAPI()

    @app.post("/echo")
    async def echo(payload: dict):
        return payload

    wrapped = RequestBodyLimitMiddleware(app, max_bytes=64)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="http://t") as c:
        r = await c.post("/echo", content=b"{" + b'"a":"' + b"x" * 200 + b'"}', headers={"content-type": "application/json"})
    body = assert_envelope(r, 413, "payload_too_large")
    assert body["detail"] == "Request body exceeds configured size limit"


async def test_429_has_header_and_retry_after_details():
    app = FastAPI()

    @app.get("/v1/ping")
    async def ping():
        return {"ok": True}

    wrapped = ApiRateLimitMiddleware(app, requests=2, window_seconds=60)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wrapped), base_url="http://t") as c:
        assert (await c.get("/v1/ping")).status_code == 200
        assert (await c.get("/v1/ping")).status_code == 200
        r = await c.get("/v1/ping")
    body = assert_envelope(r, 429, "rate_limited")
    assert int(r.headers["retry-after"]) == body["error"]["details"]["retry_after_seconds"] >= 1


# ---------------------------------------------------------------- catalog and documentation

def test_every_catalog_code_is_documented_and_docs_have_no_unknown_codes():
    doc = (ROOT.parent / "docs" / "errors.md").read_text()
    rows = dict(re.findall(r"^\| `([a-z_]+)` \| (\d{3}) \|", doc, flags=re.M))
    assert set(rows) == set(CATALOG), f"docs out of sync; run python scripts/render_error_docs.py: {set(rows) ^ set(CATALOG)}"
    for code, status in rows.items():
        assert int(status) == CATALOG[code].status, code


def test_docs_are_exactly_what_the_generator_renders():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import render_error_docs
    assert (ROOT.parent / "docs" / "errors.md").read_text() == render_error_docs.render()


def _raised_errors():
    """Every (status, detail-or-None) pair raised through HTTPException in the application source."""
    pattern = re.compile(r"HTTPException\(\s*status_code=(\d+),\s*detail=(f?\"[^\"]*\"|[A-Za-z_.()]+)", re.S)
    found = []
    for path in (ROOT / "app").rglob("*.py"):
        for status, detail in pattern.findall(path.read_text()):
            literal = detail[2:-1] if detail.startswith('f"') else detail[1:-1] if detail.startswith('"') else ""
            found.append((int(status), re.sub(r"\{[^}]*\}", "1", literal)))
    return found


def test_scan_finds_the_raise_sites():
    assert len(_raised_errors()) > 100


def test_every_raised_error_classifies_into_the_catalog():
    for status, message in _raised_errors():
        assert classify(status, message) in CATALOG, (status, message)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 429, 500, 501, 502, 503])
def test_unknown_messages_get_a_status_default_code(status):
    assert classify(status, "some brand new message") in CATALOG


@pytest.mark.parametrize("status,message,code", [
    (409, "Delivery is unknown; retry may send a duplicate. Retry again with confirm_duplicate_risk=true to proceed.", "delivery_state_uncertain"),
    (409, "Review was already resolved by another request", "review_already_resolved"),
    (409, "Scheduled message is already being submitted", "send_in_progress"),
    (409, "Resolve the pending review before trashing this message", "review_pending"),
    (400, "Invalid message cursor", "invalid_cursor"),
    (400, "CSV is malformed or is not UTF-8", "invalid_csv"),
    (400, "Agent with this email already exists", "already_exists"),
    (413, "Attachments exceed configured total size limit", "attachment_limit_exceeded"),
    (422, "Template has invalid variable syntax; use {{name}} or {{path.to.value}}", "invalid_template"),
    (503, "SMTP_CONFIG_ENCRYPTION_KEY is required to encrypt webhook signing secret", "encryption_key_missing"),
])
def test_specific_classifications(status, message, code):
    assert classify(status, message) == code
