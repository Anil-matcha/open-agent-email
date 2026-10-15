import datetime
import json

from tests.conftest import ADMIN, make_agent, make_key

PAYLOAD = {"recipient": "to@example.com", "subject": "hello", "body_text": "body"}


async def call(client, headers, name, **arguments):
    r = await client.post("/mcp/rpc", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    result = r.json()["result"]
    text = result["content"][0]["text"]
    return result["isError"], (text if result["isError"] else json.loads(text))


async def setup(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": "a@agents.local", "display_name": "A", "hitl_enabled": False})
    assert r.status_code == 201
    return (await client.post("/v1/agents/a@agents.local/messages", headers=ADMIN, json=PAYLOAD)).json()


async def test_tool_catalog_grew_and_send_schema_has_scheduling_fields(client):
    tools = (await client.get("/mcp/tools", headers=ADMIN)).json()["tools"]
    names = {t["name"] for t in tools}
    assert {"whoami", "list_conversations", "update_message_labels", "delete_message", "get_message_lifecycle"} <= names
    send = next(t for t in tools if t["name"] == "send_message")
    assert {"scheduled_at", "send_at"} <= set(send["inputSchema"]["properties"])


async def test_labels_trash_restore_and_lifecycle_via_mcp(client):
    msg = await setup(client)
    ag = "a@agents.local"
    err, out = await call(client, ADMIN, "update_message_labels", agent_email=ag, message_id=msg["id"], labels=["vip"])
    assert not err and out["labels"] == ["vip"]
    err, out = await call(client, ADMIN, "get_message_lifecycle", agent_email=ag, message_id=msg["id"])
    assert not err and out["events"][0]["type"] == "created"
    err, out = await call(client, ADMIN, "delete_message", agent_email=ag, message_id=msg["id"])
    assert not err and out["deleted_at"]
    err, out = await call(client, ADMIN, "restore_message", agent_email=ag, message_id=msg["id"])
    assert not err and out["deleted_at"] is None
    err, out = await call(client, ADMIN, "list_conversations", agent_email=ag)
    assert not err and out["items"][0]["message_count"] == 1


async def test_scheduled_send_via_mcp(client):
    original = await setup(client)
    when = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).isoformat()
    err, out = await call(client, ADMIN, "send_message", agent_email="a@agents.local", send_at=when, **PAYLOAD)
    assert not err and out["status"] == "scheduled" and out["scheduled_for"]
    assert out["scheduled_at"] == out["scheduled_for"]
    err, out = await call(client, ADMIN, "send_message", agent_email="a@agents.local", scheduled_at=when, **PAYLOAD)
    assert not err and out["status"] == "scheduled" and out["scheduled_for"]
    assert out["scheduled_at"] == out["scheduled_for"]
    err, out = await call(client, ADMIN, "reply_to_message", agent_email="a@agents.local",
                          message_id=original["id"], body_text="later", scheduled_at=when)
    assert not err and out["status"] == "scheduled"
    assert out["scheduled_at"] == out["scheduled_for"]
    err, _ = await call(client, ADMIN, "send_message", agent_email="a@agents.local", scheduled_at="2030-01-01T00:00:00", **PAYLOAD)
    assert err
    err, out = await call(client, ADMIN, "send_message", agent_email="a@agents.local", send_at="not-a-date", **PAYLOAD)
    assert err
    err, _ = await call(client, ADMIN, "send_message", agent_email="a@agents.local", send_at="2001-01-01T00:00:00Z", **PAYLOAD)
    assert err


async def test_ext_tools_respect_scope(client):
    msg = await setup(client)
    await make_agent(client, "b@agents.local")
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    full, _ = await make_key(client, "a@agents.local", "full_access")
    rv, _ = await make_key(client, "a@agents.local", "reviewer")
    a = {"agent_email": "a@agents.local"}
    assert not (await call(client, ro, "list_conversations", **a))[0]
    assert (await call(client, ro, "update_message_labels", message_id=msg["id"], labels=["x"], **a))[0]
    assert (await call(client, ro, "delete_message", message_id=msg["id"], **a))[0]
    assert (await call(client, full, "list_conversations", agent_email="b@agents.local"))[0]
    assert (await call(client, full, "update_protection", threat_sensitivity="off", hitl_enabled=False, **a))[0]
    assert not (await call(client, full, "update_message_labels", message_id=msg["id"], labels=["x"], **a))[0]
    assert (await call(client, rv, "list_conversations", **a))[0]
    err, out = await call(client, rv, "whoami")
    assert not err and out["scope"] == "reviewer"
    err, out = await call(client, ADMIN, "update_protection", threat_sensitivity="high", hitl_enabled=True, **a)
    assert not err and out["hitl_enabled"] is True
