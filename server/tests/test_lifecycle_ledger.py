from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import LIFECYCLE_CODES, Message, MessageLifecycleEntry
from app.services.outbound_worker import _dispatch_one
from tests.conftest import ADMIN, make_agent

SENDER = "sender@agents.example.com"
PEER = "peer@agents.example.com"


async def _sender(client, *, hitl=False):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": hitl})
    assert r.status_code == 201, r.text


async def _entries(client, message_id):
    r = await client.get(f"/v1/agents/{SENDER}/messages/{message_id}/lifecycle", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


async def _ledger(message_id):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(MessageLifecycleEntry).where(MessageLifecycleEntry.message_id == message_id)
                                 .order_by(MessageLifecycleEntry.id))).scalars().all()
    return [row.code for row in rows]


async def test_local_delivery_is_recorded_in_order(client):
    await _sender(client)
    await make_agent(client, PEER)
    sent = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": PEER, "subject": "s", "body_text": "b"})).json()
    assert await _dispatch_one() is True
    codes = await _ledger(sent["id"])
    assert codes[0] == "created" and codes[-1] == "delivered"
    assert {"sending", "queued"} <= set(codes)
    body = await _entries(client, sent["id"])
    assert [e["code"] for e in body["entries"]] == codes
    assert all(e["at"] and "detail" in e for e in body["entries"])
    assert body["events"] and body["status"] == "delivered"  # the existing derived timeline is unchanged


async def test_terminal_failure_detail_is_kept(client):
    await _sender(client)
    sent = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    async with AsyncSessionLocal() as db:
        row = await db.get(Message, sent["id"])
        row.status = "failed"
        row.delivery_error = "Upstream SMTP relay is not configured or enabled"
        await db.commit()
    entries = (await _entries(client, sent["id"]))["entries"]
    assert entries[-1]["code"] == "failed" and "relay is not configured" in entries[-1]["detail"]


async def test_review_decisions_are_recorded(client):
    await _sender(client, hitl=True)
    held = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    assert held["status"] == "pending_review"
    assert "review_requested" in await _ledger(held["id"])
    review = (await client.get("/v1/reviews?status=pending", headers=ADMIN)).json()[0]
    assert (await client.post(f"/v1/reviews/{review['id']}/approve", headers=ADMIN, json={"action": "approve"})).status_code == 200
    codes = await _ledger(held["id"])
    assert codes.count("review_requested") == 1 and codes.count("review_approved") == 1

    await _sender_other_review(client)


async def _sender_other_review(client):
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": PEER, "display_name": "P", "hitl_enabled": True})
    assert r.status_code == 201
    held = (await client.post(f"/v1/agents/{PEER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    review = [r for r in (await client.get("/v1/reviews?status=pending", headers=ADMIN)).json() if r["message_id"] == held["id"]][0]
    assert (await client.post(f"/v1/reviews/{review['id']}/reject", headers=ADMIN, json={"action": "reject"})).status_code == 200
    assert (await _ledger(held["id"]))[-1] == "review_rejected"


async def test_trash_and_restore_are_recorded(client):
    await _sender(client)
    msg = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    await _dispatch_one()
    assert (await client.delete(f"/v1/agents/{SENDER}/messages/{msg['id']}", headers=ADMIN)).status_code == 200
    assert (await client.post(f"/v1/agents/{SENDER}/messages/{msg['id']}/restore", headers=ADMIN)).status_code == 200
    codes = await _ledger(msg["id"])
    assert codes[-2:] == ["trashed", "restored"]


async def test_provider_outcome_is_recorded_once(client):
    await _sender(client)
    msg = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    async with AsyncSessionLocal() as db:
        row = await db.get(Message, msg["id"])
        row.provider_delivery_status = "bounced"
        row.provider_delivery_error = "mailbox full"
        await db.commit()
        row.subject = "unrelated change"
        await db.commit()
    entries = (await _entries(client, msg["id"]))["entries"]
    bounced = [e for e in entries if e["code"] == "provider_bounced"]
    assert len(bounced) == 1 and bounced[0]["detail"] == "mailbox full"


async def test_messages_without_a_ledger_get_derived_entries(client):
    await _sender(client)
    msg = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    async with AsyncSessionLocal() as db:
        row = await db.get(Message, msg["id"])
        row.status = "failed"
        row.delivery_error = "boom"
        await db.commit()
    async with AsyncSessionLocal() as db:
        await db.execute(MessageLifecycleEntry.__table__.delete().where(MessageLifecycleEntry.message_id == msg["id"]))
        await db.commit()
    entries = (await _entries(client, msg["id"]))["entries"]
    codes = [e["code"] for e in entries]
    assert "created" in codes and "queued" in codes and codes[-1] == "failed"
    assert set(codes) <= set(LIFECYCLE_CODES)


async def test_permanent_delete_removes_the_ledger(client):
    await _sender(client)
    await make_agent(client, PEER)
    msg = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": PEER, "subject": "s", "body_text": "b"})).json()
    await _dispatch_one()
    assert await _ledger(msg["id"])
    r = await client.delete(f"/v1/agents/{SENDER}/messages/{msg['id']}?permanent=true&confirm=DELETE", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert await _ledger(msg["id"]) == []
