from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import IdempotencyRecord, Message, MessageAttachment
from tests.conftest import ADMIN, make_agent, make_key

MSGS = "/v1/agents/a@agents.local/messages"
PAYLOAD = {"recipient": "to@example.com", "subject": "hello", "body_text": "body"}


async def _count():
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(Message))


async def test_idempotency_key_prevents_duplicate_send(client):
    await make_agent(client, "a@agents.local")
    h = {**ADMIN, "Idempotency-Key": "k1"}
    first = await client.post(MSGS, headers=h, json=PAYLOAD)
    second = await client.post(MSGS, headers=h, json=PAYLOAD)
    assert first.status_code == 201 and second.status_code in (200, 201)
    assert first.json()["id"] == second.json()["id"]
    assert await _count() == 1
    async with AsyncSessionLocal() as db:
        receipt = await db.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.scope == "outbound:a@agents.local",
            IdempotencyRecord.idempotency_key == "k1",
        ))
        assert receipt.state == "completed"
        assert receipt.response_status == 201
        assert receipt.response_body == {"message_id": first.json()["id"]}


async def test_in_flight_send_key_returns_conflict(client):
    await make_agent(client, "a@agents.local")
    from app.services.idempotency import reserve_idempotency

    await reserve_idempotency("outbound:a@agents.local", "busy-send", {
        "recipient": "to@example.com", "sender": "a@agents.local",
        "subject": "hello", "body_text": "body", "body_html": None,
        "thread_id": None, "conversation_id": None, "in_reply_to": None,
        "references": None, "attachments": [],
    })
    response = await client.post(MSGS, headers={**ADMIN, "Idempotency-Key": "busy-send"}, json=PAYLOAD)
    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]
    assert await _count() == 0


async def test_idempotency_key_reuse_with_different_body_rejected(client):
    await make_agent(client, "a@agents.local")
    h = {**ADMIN, "Idempotency-Key": "k1"}
    assert (await client.post(MSGS, headers=h, json=PAYLOAD)).status_code == 201
    r = await client.post(MSGS, headers=h, json={**PAYLOAD, "body_text": "different"})
    assert r.status_code == 422
    assert await _count() == 1


async def test_idempotency_keys_are_inbox_scoped(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    h = {**ADMIN, "Idempotency-Key": "shared"}
    assert (await client.post(MSGS, headers=h, json=PAYLOAD)).status_code == 201
    assert (await client.post("/v1/agents/b@agents.local/messages", headers=h, json=PAYLOAD)).status_code == 201
    assert await _count() == 2


async def test_forward_replays_idempotently_and_rejects_key_reuse(client):
    await make_agent(client, "a@agents.local")
    original = await client.post(MSGS, headers=ADMIN, json={
        **PAYLOAD,
        "attachments": [{"filename": "original.txt", "content_type": "text/plain", "content_base64": "b3JpZ2luYWw="}],
    })
    assert original.status_code == 201
    url = f"{MSGS}/{original.json()['id']}/forward"
    headers = {**ADMIN, "Idempotency-Key": "forward-1"}
    request = {
        "recipient": "human@example.com", "note": "Please review",
        "body_text": "Custom forward body", "body_html": "<p>Custom HTML</p>",
        "attachments": [{"filename": "extra.txt", "content_type": "text/plain", "content_base64": "ZXh0cmE="}],
    }

    first = await client.post(url, headers=headers, json=request)
    retry = await client.post(url, headers=headers, json=request)
    assert first.status_code == retry.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert first.json()["body_text"] == "Custom forward body"
    assert first.json()["body_html"] == "<p>Custom HTML</p>"
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MessageAttachment).where(MessageAttachment.message_id == first.json()["id"]).order_by(MessageAttachment.filename))).scalars().all()
        assert [(row.filename, row.content) for row in rows] == [("extra.txt", b"extra"), ("original.txt", b"original")]
    changed = await client.post(url, headers=headers, json={**request, "body_text": "Different context"})
    assert changed.status_code == 422
    assert await _count() == 2


async def test_forward_without_custom_body_keeps_human_handoff_format(client):
    await make_agent(client, "a@agents.local")
    original = await client.post(MSGS, headers=ADMIN, json=PAYLOAD)
    url = f"{MSGS}/{original.json()['id']}/forward"
    forwarded = await client.post(url, headers=ADMIN, json={"recipient": "human@example.com", "note": "Review this"})
    assert forwarded.status_code == 201
    assert "handing this email to you for human review" in forwarded.json()["body_text"]
    assert "Context from the agent:\nReview this" in forwarded.json()["body_text"]
    assert "---------- Forwarded message ----------" in forwarded.json()["body_text"]

async def test_suppressed_recipient_blocked_before_message_created(client):
    await make_agent(client, "a@agents.local")
    r = await client.post("/v1/agents/a@agents.local/suppressions", headers=ADMIN, json={"email": "To@Example.com"})
    assert r.status_code == 201
    r = await client.post(MSGS, headers=ADMIN, json=PAYLOAD)
    assert r.status_code >= 400 and r.status_code < 500
    assert await _count() == 0


async def test_suppression_is_per_inbox(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    await client.post("/v1/agents/a@agents.local/suppressions", headers=ADMIN, json={"email": "to@example.com"})
    assert (await client.post("/v1/agents/b@agents.local/messages", headers=ADMIN, json=PAYLOAD)).status_code == 201


async def test_read_only_key_cannot_send(client):
    await make_agent(client, "a@agents.local")
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    assert (await client.post(MSGS, headers=ro, json=PAYLOAD)).status_code == 403
    assert await _count() == 0


def test_ses_configuration_set_header_is_added_only_when_configured(monkeypatch):
    import smtplib
    from app.services.email_service import _send_smtp_sync
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def starttls(self): pass
        def sendmail(self, frm, to, data):
            sent.append(data)
            return {}
        def quit(self): pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    args = ("relay", 587, "", "", False, False, "a@x.com", "b@y.net", "s", "b")
    assert _send_smtp_sync(*args, ses_configuration_set="oae-test")[0]
    assert _send_smtp_sync(*args)[0]
    assert "X-SES-CONFIGURATION-SET: oae-test" in sent[0]
    assert "X-SES-CONFIGURATION-SET" not in sent[1]
