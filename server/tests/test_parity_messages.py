import datetime

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import OutboundJob
from tests.conftest import ADMIN, make_agent, make_key

BASE = "/v1/agents/a@agents.local"
PAYLOAD = {"recipient": "to@example.com", "subject": "hello", "body_text": "body"}


async def _agent(client, hitl=False):
    r = await client.post("/v1/agents", headers=ADMIN, json={
        "email": "a@agents.local", "display_name": "A", "hitl_enabled": hitl})
    assert r.status_code == 201


def _later(days=2):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat()


async def test_labels_roundtrip_and_filter(client):
    await _agent(client)
    mid = (await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)).json()["id"]
    await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    r = await client.patch(f"{BASE}/messages/{mid}", headers=ADMIN, json={"labels": ["vip", " vip ", "needs-human"]})
    assert r.status_code == 200 and r.json()["labels"] == ["vip", "needs-human"]
    listed = (await client.get(f"{BASE}/messages?label=vip", headers=ADMIN)).json()
    assert [m["id"] for m in listed] == [mid]
    assert (await client.patch(f"{BASE}/messages/{mid}", headers=ADMIN, json={"labels": [""]})).status_code == 422


async def test_labels_require_write_scope(client):
    await _agent(client)
    mid = (await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)).json()["id"]
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    assert (await client.patch(f"{BASE}/messages/{mid}", headers=ro, json={"labels": ["x"]})).status_code == 403
    assert (await client.delete(f"{BASE}/messages/{mid}", headers=ro)).status_code == 403


async def test_message_page_cursor_uses_standard_error_code(client):
    await _agent(client)
    response = await client.get(f"{BASE}/messages/page?cursor=bad", headers=ADMIN)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_trash_and_restore(client):
    await _agent(client)
    mid = (await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)).json()["id"]
    assert (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).json()["deleted_at"]
    assert (await client.get(f"{BASE}/messages", headers=ADMIN)).json() == []
    assert (await client.get(f"{BASE}/messages/page", headers=ADMIN)).json()["items"] == []
    assert [m["id"] for m in (await client.get(f"{BASE}/messages?trashed=true", headers=ADMIN)).json()] == [mid]
    assert (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()["deleted_at"] is None
    assert len((await client.get(f"{BASE}/messages", headers=ADMIN)).json()) == 1


async def test_scheduled_send_defers_job_and_can_be_canceled(client):
    await _agent(client)
    r = await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, "send_at": _later()})
    assert r.status_code == 201 and r.json()["status"] == "scheduled" and r.json()["scheduled_for"]
    mid = r.json()["id"]
    async with AsyncSessionLocal() as db:
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == mid))
        assert job.status == "pending" and job.available_at > datetime.datetime.utcnow() + datetime.timedelta(days=1)
    canceled = (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).json()
    assert canceled["status"] == "canceled"
    async with AsyncSessionLocal() as db:
        assert (await db.scalar(select(OutboundJob.status).where(OutboundJob.message_id == mid))) == "cancelled"
    # restoring before the scheduled time re-arms the send (see tests/test_trash_lifecycle.py for the late case)
    assert (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()["status"] == "scheduled"


async def test_send_at_validation(client):
    await _agent(client)
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)).isoformat()
    for bad in (past, _later(91)):
        assert (await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, "send_at": bad})).status_code == 422


async def test_scheduled_at_alias_and_offset_requirement(client):
    await _agent(client)
    when = _later()
    canonical = await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, "scheduled_at": when})
    assert canonical.status_code == 201 and canonical.json()["status"] == "scheduled"
    assert canonical.json()["scheduled_at"] == canonical.json()["scheduled_for"]
    assert (await client.post(f"{BASE}/messages", headers=ADMIN,
                             json={**PAYLOAD, "scheduled_at": when, "send_at": _later(3)})).status_code == 422
    assert (await client.post(f"{BASE}/messages", headers=ADMIN,
                             json={**PAYLOAD, "scheduled_at": when.removesuffix("+00:00").removesuffix("Z")})).status_code == 422


async def test_scheduled_review_approval_keeps_deferral(client):
    await _agent(client, hitl=True)
    mid = (await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, "send_at": _later()})).json()["id"]
    rid = (await client.get("/v1/reviews", headers=ADMIN)).json()[0]["id"]
    assert (await client.post(f"/v1/reviews/{rid}/approve", headers=ADMIN)).status_code == 200
    async with AsyncSessionLocal() as db:
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == mid))
        assert job.available_at > datetime.datetime.utcnow() + datetime.timedelta(days=1)
    assert (await client.get(f"{BASE}/messages/{mid}", headers=ADMIN)).json()["status"] == "scheduled"


async def test_cannot_trash_message_pending_review(client):
    await _agent(client, hitl=True)
    mid = (await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)).json()["id"]
    assert (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).status_code == 409


async def test_lifecycle_and_conversations(client):
    await _agent(client)
    first = (await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)).json()
    await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, "thread_id": first["thread_id"]})
    await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    life = (await client.get(f"{BASE}/messages/{first['id']}/lifecycle", headers=ADMIN)).json()
    assert [e["type"] for e in life["events"]][:2] == ["created", "queued"]
    convs = (await client.get(f"{BASE}/conversations", headers=ADMIN)).json()["items"]
    assert sorted(c["message_count"] for c in convs) == [1, 2]
    detail = (await client.get(f"{BASE}/conversations/{first['thread_id']}", headers=ADMIN)).json()
    assert len(detail["messages"]) == 2 and detail["participants"]
    assert (await client.get(f"{BASE}/conversations/nope", headers=ADMIN)).status_code == 404


async def test_conversation_pages_are_stable_and_reject_invalid_cursor(client):
    await _agent(client)
    await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    first = await client.get(f"{BASE}/conversations/page?limit=1", headers=ADMIN)
    assert first.status_code == 200 and len(first.json()["items"]) == 1 and first.json()["next_cursor"]
    second = await client.get(f"{BASE}/conversations/page?limit=1&cursor=" + first.json()["next_cursor"], headers=ADMIN)
    assert len(second.json()["items"]) == 1 and second.json()["next_cursor"] is None
    assert (await client.get(f"{BASE}/conversations/page?cursor=bad", headers=ADMIN)).json()["detail"]["code"] == "invalid_cursor"


async def test_caller_conversation_id_is_separate_filterable_and_inherited_on_reply(client):
    await _agent(client)
    created = await client.post(f"{BASE}/messages", headers=ADMIN, json={
        **PAYLOAD, "conversation_id": "ticket-42",
    })
    assert created.status_code == 201
    first = created.json()
    assert first["conversation_id"] == "ticket-42"
    assert first["thread_id"] != "ticket-42"

    reply = await client.post(f"{BASE}/messages/{first['id']}/reply", headers=ADMIN, json={"body_text": "reply"})
    assert reply.status_code == 201
    assert reply.json()["conversation_id"] == "ticket-42"
    assert reply.json()["thread_id"] == first["thread_id"]

    override = await client.post(f"{BASE}/messages/{first['id']}/reply", headers=ADMIN, json={
        "body_text": "another reply", "conversation_id": "external-99",
    })
    assert override.status_code == 201 and override.json()["conversation_id"] == "external-99"
    assert [m["conversation_id"] for m in (await client.get(
        f"{BASE}/messages?conversation_id=ticket-42", headers=ADMIN,
    )).json()] == ["ticket-42", "ticket-42"]
    assert (await client.post(f"{BASE}/messages", headers=ADMIN, json={
        **PAYLOAD, "conversation_id": "",
    })).status_code == 422


async def test_conversations_are_inbox_scoped(client):
    await _agent(client)
    await make_agent(client, "b@agents.local")
    key, _ = await make_key(client, "b@agents.local", "full_access")
    assert (await client.get(f"{BASE}/conversations", headers=key)).status_code == 403


async def test_meta_endpoints(client):
    await _agent(client)
    assert (await client.get("/v1/info")).json()["api_version"] == "v1"
    assert (await client.get("/v1/whoami", headers=ADMIN)).json()["kind"] == "admin"
    key, _ = await make_key(client, "a@agents.local", "full_access")
    who = (await client.get("/v1/whoami", headers=key)).json()
    assert who["agent_email"] == "a@agents.local" and who["scope"] == "full_access"
    await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    m = (await client.get(f"{BASE}/metrics", headers=key)).json()
    assert m["messages"]["outbound"] == 1
    prot = await client.put(f"{BASE}/protection", headers=key, json={"threat_sensitivity": "off", "hitl_enabled": False})
    assert prot.status_code == 403  # an agent key cannot weaken its own gate
    ok = await client.put(f"{BASE}/protection", headers=ADMIN, json={"threat_sensitivity": "high", "hitl_enabled": True})
    assert ok.json() == {"threat_sensitivity": "high", "hitl_enabled": True}
    assert (await client.get(f"{BASE}/protection", headers=key)).json()["hitl_enabled"] is True
