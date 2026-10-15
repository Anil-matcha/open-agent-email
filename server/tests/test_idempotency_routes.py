import hashlib
import json
import datetime

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import ApiKey, Contact, ContactImportBatch, Domain, IdempotencyRecord, OutboundJob, ReviewItem, WebhookEndpoint
from app.services.idempotency import reserve_idempotency
from tests.conftest import ADMIN, make_agent


ADMIN_ID = f"admin:{hashlib.sha256(b'test-admin-key').hexdigest()[:12]}"
A = "a@agents.local"


def _headers(key):
    return {**ADMIN, "Idempotency-Key": key}


def _raw_json(data):
    return json.dumps(data, separators=(",", ":")).encode()


async def _count(model):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_contact_create_replays_conflicts_and_reports_in_flight(client):
    await make_agent(client, A)
    path = f"/v1/agents/{A}/contacts"
    raw = _raw_json({"email": "person@example.com", "name": "Pat"})
    first = await client.post(path, content=raw, headers={**_headers("contact-1"), "content-type": "application/json"})
    retry = await client.post(path, content=raw, headers={**_headers("contact-1"), "content-type": "application/json"})
    assert first.status_code == retry.status_code == 201
    assert first.json() == retry.json()
    assert await _count(Contact) == 1

    changed = _raw_json({"email": "person@example.com", "name": "Different"})
    conflict = await client.post(path, content=changed, headers={**_headers("contact-1"), "content-type": "application/json"})
    assert conflict.status_code == 422 and "different request" in conflict.json()["detail"]

    pending_body = _raw_json({"email": "pending@example.com", "name": "Pending"})
    await reserve_idempotency(f"{ADMIN_ID}:contacts:create", "contact-pending",
                              {"agent_email": A, "body": pending_body.decode("latin1")})
    pending = await client.post(path, content=pending_body,
                                headers={**_headers("contact-pending"), "content-type": "application/json"})
    assert pending.status_code == 409 and "still in progress" in pending.json()["detail"]
    assert await _count(Contact) == 1


async def test_csv_import_replays_conflicts_and_reports_in_flight(client):
    await make_agent(client, A)
    path = f"/v1/agents/{A}/contacts/imports"
    raw = b"email,name\none@example.com,One\ntwo@example.com,Two\n"
    headers = {**_headers("import-1"), "content-type": "text/csv"}
    first = await client.post(path, content=raw, headers=headers)
    retry = await client.post(path, content=raw, headers=headers)
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert await _count(ContactImportBatch) == 1 and await _count(Contact) == 2

    changed = await client.post(path, content=raw + b"", headers=headers)
    # Identical bytes remain the same logical request and replay.
    assert changed.status_code == 200 and changed.json() == first.json()
    other = b"email,name\none@example.com,Changed\n"
    conflict = await client.post(path, content=other, headers=headers)
    assert conflict.status_code == 422 and "different request" in conflict.json()["detail"]

    pending_body = b"email,name\npending@example.com,Pending\n"
    await reserve_idempotency(f"{ADMIN_ID}:contacts:imports:create", "import-pending",
                              {"agent_email": A, "body": pending_body.decode("latin1")})
    pending = await client.post(path, content=pending_body,
                                headers={**_headers("import-pending"), "content-type": "text/csv"})
    assert pending.status_code == 409 and "still in progress" in pending.json()["detail"]
    assert await _count(ContactImportBatch) == 1


async def _seed_domain(domain):
    async with AsyncSessionLocal() as db:
        db.add(Domain(id=f"dom_{domain[:8]}", domain=domain, status="pending_dns",
                      mx_record="mx", spf_record="spf", dkim_record="dkim",
                      dmarc_record="dmarc", created_at=datetime.datetime.utcnow()))
        await db.commit()


async def test_domain_delete_replays_conflicts_and_reports_in_flight(client):
    await _seed_domain("one.example.com")
    await _seed_domain("two.example.com")
    first = await client.delete("/v1/domains/one.example.com", headers=_headers("domain-1"))
    retry = await client.delete("/v1/domains/one.example.com", headers=_headers("domain-1"))
    assert first.status_code == retry.status_code == 204
    assert await _count(Domain) == 1

    changed = await client.delete("/v1/domains/two.example.com", headers=_headers("domain-1"))
    assert changed.status_code == 422 and "different request" in changed.json()["detail"]
    assert await _count(Domain) == 1

    await reserve_idempotency(f"{ADMIN_ID}:domains:delete", "domain-pending",
                              {"domain": "two.example.com", "body": ""})
    pending = await client.delete("/v1/domains/two.example.com", headers=_headers("domain-pending"))
    assert pending.status_code == 409 and "still in progress" in pending.json()["detail"]
    assert await _count(Domain) == 1


async def _held(client, *, create_agent=True):
    if create_agent:
        await make_agent(client, A)
    sent = await client.post(f"/v1/agents/{A}/messages", headers=ADMIN,
                             json={"recipient": "to@example.com", "subject": "hello", "body_text": "body"})
    assert sent.status_code == 201
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(ReviewItem.id).where(ReviewItem.message_id == sent.json()["id"]))


async def test_review_approval_replays_conflicts_and_reports_in_flight(client):
    review_id = await _held(client)
    path = f"/v1/reviews/{review_id}/approve"
    first = await client.post(path, headers=_headers("approve-1"))
    retry = await client.post(path, headers=_headers("approve-1"))
    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert await _count(OutboundJob) == 1

    changed_body = _raw_json({"action": "approve", "notes": "changed"})
    changed = await client.post(path, content=changed_body,
                                headers={**_headers("approve-1"), "content-type": "application/json"})
    assert changed.status_code == 422 and "different request" in changed.json()["detail"]
    assert await _count(OutboundJob) == 1

    pending_review = await _held(client, create_agent=False)
    pending_body = b""
    await reserve_idempotency(f"{ADMIN_ID}:reviews:approve", "approve-pending",
                              {"review_id": pending_review, "body": pending_body.decode("latin1")})
    pending = await client.post(f"/v1/reviews/{pending_review}/approve",
                                headers=_headers("approve-pending"))
    assert pending.status_code == 409 and "still in progress" in pending.json()["detail"]
    assert await _count(OutboundJob) == 1


async def _assert_encrypted_receipt(key, expected_secret):
    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key))
        assert row is not None and row.response_encrypted is True
        assert expected_secret not in str(row.response_body)


async def test_api_key_create_replays_secret_and_conflicts_on_body_change(client):
    await make_agent(client, A)
    path = "/v1/api-keys"
    payload = {"name": "automation", "agent_email": A, "scope": "full_access"}
    headers = _headers("api-key-secret-1")
    first = await client.post(path, headers=headers, json=payload)
    replay = await client.post(path, headers=headers, json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    secret = first.json()["key"]
    assert secret.startswith("oae_")
    await _assert_encrypted_receipt("api-key-secret-1", secret)
    changed = await client.post(path, headers=headers, json={**payload, "name": "changed"})
    assert changed.status_code == 422 and "different request" in changed.json()["detail"]
    assert await _count(ApiKey) == 1


async def test_webhook_create_replays_secret_and_conflicts_on_body_change(client):
    await make_agent(client, A)
    path = "/v1/webhooks"
    payload = {"agent_email": A, "url": "https://hooks.example.com/events", "events": ["email.sent"]}
    headers = _headers("webhook-secret-1")
    first = await client.post(path, headers=headers, json=payload)
    replay = await client.post(path, headers=headers, json=payload)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    secret = first.json()["signing_secret"]
    assert secret.startswith("whsec_")
    await _assert_encrypted_receipt("webhook-secret-1", secret)
    changed = await client.post(path, headers=headers, json={**payload, "enabled": False})
    assert changed.status_code == 422 and "different request" in changed.json()["detail"]
    assert await _count(WebhookEndpoint) == 1


async def test_webhook_secret_rotation_replays_secret_and_conflicts_on_body_change(client):
    await make_agent(client, A)
    created = await client.post("/v1/webhooks", headers=ADMIN, json={
        "agent_email": A, "url": "https://hooks.example.com/rotate", "events": ["email.sent"],
    })
    assert created.status_code == 201
    endpoint_id = created.json()["id"]
    path = f"/v1/webhooks/{endpoint_id}/rotate-secret"
    headers = _headers("webhook-rotate-1")
    first = await client.post(path, headers=headers)
    replay = await client.post(path, headers=headers)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    secret = first.json()["signing_secret"]
    assert secret.startswith("whsec_") and secret != created.json()["signing_secret"]
    await _assert_encrypted_receipt("webhook-rotate-1", secret)
    changed = await client.post(path, content=b"{}",
                                headers={**headers, "content-type": "application/json"})
    assert changed.status_code == 422 and "different request" in changed.json()["detail"]


async def test_secret_bearing_idempotency_requires_encryption_before_side_effect(client, monkeypatch):
    await make_agent(client, A)
    monkeypatch.setattr("app.services.smtp_secrets.settings.SMTP_CONFIG_ENCRYPTION_KEY", "")
    response = await client.post("/v1/api-keys", headers=_headers("missing-encryption-key"), json={
        "name": "must-not-create", "agent_email": A, "scope": "full_access",
    })
    assert response.status_code == 503
    assert await _count(ApiKey) == 0
    assert await _count(IdempotencyRecord) == 0
