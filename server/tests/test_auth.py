import datetime

import pytest
from sqlalchemy import update

from app.database import AsyncSessionLocal
from app.models import ApiKey
from tests.conftest import ADMIN, make_agent, make_key

ADMIN_ROUTES = [("GET", "/v1/agents"), ("GET", "/v1/api-keys")]
INBOX_ROUTES = [("GET", "/v1/agents/a@agents.local/messages"), ("GET", "/v1/agents/a@agents.local/contacts")]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES + INBOX_ROUTES)
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer"}, {"Authorization": "Bearer nope"},
    {"Authorization": "Basic dGVzdDp0ZXN0"}, {"Authorization": "bearer  "},
])
async def test_anonymous_and_malformed_rejected(client, method, path, headers):
    r = await client.request(method, path, headers=headers)
    assert r.status_code == 401


@pytest.mark.parametrize("method,path", [("GET", "/mcp"), ("GET", "/mcp/info"), ("GET", "/mcp/tools"), ("POST", "/mcp/rpc")])
async def test_mcp_requires_auth(client, method, path):
    r = await client.request(method, path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"} if method == "POST" else None)
    assert r.status_code == 401


async def _call(client, headers, name, arguments):
    r = await client.post("/mcp/rpc", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def test_mcp_agent_cannot_list_agents_or_cross_mailboxes(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    headers, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await _call(client, headers, "list_agents", {}))["isError"] is True
    assert (await _call(client, headers, "list_messages", {"agent_email": "b@agents.local"}))["isError"] is True
    assert (await _call(client, headers, "list_messages", {"agent_email": "a@agents.local"}))["isError"] is False
    assert (await _call(client, ADMIN, "list_agents", {}))["isError"] is False


async def test_mcp_read_only_cannot_send_and_reviewer_limited(client):
    await make_agent(client, "a@agents.local")
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    sent = await _call(client, ro, "send_message", {
        "agent_email": "a@agents.local", "recipient": "x@example.com", "subject": "s", "body_text": "b"})
    assert sent["isError"] is True
    rv, _ = await make_key(client, "a@agents.local", "reviewer")
    assert (await _call(client, rv, "list_messages", {"agent_email": "a@agents.local"}))["isError"] is True
    assert (await _call(client, rv, "list_reviews", {}))["isError"] is False


async def test_agent_key_cannot_use_admin_routes(client):
    await make_agent(client, "a@agents.local")
    headers, _ = await make_key(client, "a@agents.local", "full_access")
    for method, path in ADMIN_ROUTES:
        assert (await client.request(method, path, headers=headers)).status_code == 403


async def test_cross_mailbox_access_denied(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    headers, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 200
    assert (await client.get("/v1/agents/b@agents.local/messages", headers=headers)).status_code == 403
    assert (await client.get("/v1/agents/b@agents.local/contacts", headers=headers)).status_code == 403


async def test_read_only_cannot_write(client):
    await make_agent(client, "a@agents.local")
    headers, _ = await make_key(client, "a@agents.local", "read_only")
    assert (await client.get("/v1/agents/a@agents.local/contacts", headers=headers)).status_code == 200
    r = await client.post("/v1/agents/a@agents.local/contacts", headers=headers, json={"email": "x@example.com"})
    assert r.status_code == 403


async def test_reviewer_cannot_read_mailbox(client):
    await make_agent(client, "a@agents.local")
    headers, _ = await make_key(client, "a@agents.local", "reviewer")
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 403


async def test_revoked_key_rejected(client):
    await make_agent(client, "a@agents.local")
    headers, key_id = await make_key(client, "a@agents.local", "full_access")
    assert (await client.delete(f"/v1/api-keys/{key_id}", headers=ADMIN)).status_code == 204
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 401


async def test_expired_key_rejected(client):
    await make_agent(client, "a@agents.local")
    headers, key_id = await make_key(client, "a@agents.local", "full_access")
    async with AsyncSessionLocal() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == key_id)
                         .values(expires_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=1)))
        await db.commit()
    assert (await client.get("/v1/agents/a@agents.local/messages", headers=headers)).status_code == 401


async def test_keys_are_hashed_at_rest(client):
    await make_agent(client, "a@agents.local")
    headers, key_id = await make_key(client, "a@agents.local", "full_access")
    token = headers["Authorization"].split()[1]
    async with AsyncSessionLocal() as db:
        row = await db.get(ApiKey, key_id)
        assert token not in (row.key_hash, row.prefix) and len(row.key_hash) == 64
