import datetime

import pytest
from sqlalchemy import update

from app.database import AsyncSessionLocal
from app.models import ApiKey
from tests.conftest import ADMIN, make_agent


async def _mint(client, **body):
    response = await client.post("/v1/account/api-keys", headers=ADMIN,
                                 json={"name": "canonical", "scope": "account", **body})
    return response


async def test_account_scope_key_authenticates_as_admin_and_list_hides_secret(client):
    await make_agent(client, "a@agents.local")
    created = await _mint(client)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scope"] == "account" and body["agent_email"] is None
    assert body["key_prefix"] and body["key"].startswith(body["key_prefix"] + "_")
    admin_key = {"Authorization": f"Bearer {body['key']}"}
    assert (await client.get("/v1/agents", headers=admin_key)).status_code == 200
    assert (await client.get("/v1/account/api-keys", headers=admin_key)).status_code == 200

    listing = (await client.get("/v1/account/api-keys", headers=admin_key)).json()
    item = next(row for row in listing["items"] if row["id"] == body["id"])
    assert item["key_prefix"] == body["key_prefix"]
    assert "key" not in item and body["key"] not in str(listing)
    async with AsyncSessionLocal() as db:
        row = await db.get(ApiKey, body["id"])
        assert row.key_hash != body["key"] and len(row.key_hash) == 64


async def test_agent_scope_key_is_inbox_bound_and_legacy_scope_stays_full_access(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    created = await _mint(client, scope="agent", agent_email="A@AGENTS.LOCAL")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["scope"] == "agent" and body["agent_email"] == "a@agents.local"
    headers = {"Authorization": f"Bearer {body['key']}"}
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 200
    assert (await client.get("/v1/agents/b@agents.local/messages", headers=headers)).status_code == 403
    assert (await client.get("/v1/agents", headers=headers)).status_code == 403
    created_contact = await client.post("/v1/agents/a@agents.local/contacts", headers=headers,
                                        json={"email": "write@example.com", "name": "Agent write"})
    assert created_contact.status_code == 201, created_contact.text

    legacy = await client.post("/v1/api-keys", headers=ADMIN,
                               json={"name": "legacy", "agent_email": "a@agents.local", "scope": "full_access"})
    assert legacy.status_code == 201
    assert legacy.json()["scope"] == "full_access"
    legacy_headers = {"Authorization": f"Bearer {legacy.json()['key']}"}
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=legacy_headers)).status_code == 200
    assert (await client.get("/v1/agents", headers=legacy_headers)).status_code == 403


@pytest.mark.parametrize("body", [
    {"scope": "account", "agent_email": "a@agents.local"},
    {"scope": "agent"},
    {"scope": "full_access", "agent_email": "a@agents.local"},
])
async def test_canonical_scope_requires_consistent_owner(client, body):
    result = await _mint(client, **body)
    assert result.status_code == 422


async def test_account_agent_key_expiry_and_revocation(client):
    await make_agent(client, "a@agents.local")
    created = await _mint(client, scope="agent", agent_email="a@agents.local", expires_in_days=1)
    assert created.status_code == 201
    body = created.json()
    headers = {"Authorization": f"Bearer {body['key']}"}
    async with AsyncSessionLocal() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == body["id"]).values(
            expires_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=1)))
        await db.commit()
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 401

    # A fresh key can be revoked through the canonical route and becomes unusable.
    created = await _mint(client, scope="agent", agent_email="a@agents.local")
    body = created.json()
    headers = {"Authorization": f"Bearer {body['key']}"}
    admin_headers = {"Authorization": f"Bearer {(await _mint(client)) .json()['key']}"}
    revoked = await client.delete(f"/v1/account/api-keys/{body['id']}?confirm=DELETE", headers=admin_headers)
    assert revoked.status_code == 200
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 401


async def test_create_idempotency_replays_same_secret_without_duplicate(client):
    first = await client.post("/v1/account/api-keys", headers={**ADMIN, "Idempotency-Key": "account-key-once"},
                              json={"name": "replayed", "scope": "account"})
    second = await client.post("/v1/account/api-keys", headers={**ADMIN, "Idempotency-Key": "account-key-once"},
                               json={"name": "replayed", "scope": "account"})
    assert first.status_code == second.status_code == 201
    assert first.json()["key"] == second.json()["key"]
    listing = (await client.get("/v1/account/api-keys", headers=ADMIN)).json()
    assert sum(item["id"] == first.json()["id"] for item in listing["items"]) == 1
