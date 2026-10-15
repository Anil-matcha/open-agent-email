import datetime

import pytest
from sqlalchemy import func, select, update

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    Message, MessageAttachment, MessagePurgeLog, OutboundJob, OutboundRetryEvent, ReviewItem,
)
from app.services.message_purge import purge_expired_trash
from tests.conftest import ADMIN, make_agent, make_key

BASE = "/v1/agents/a@agents.local"
PAYLOAD = {"recipient": "to@example.com", "subject": "secret subject", "body_text": "secret body"}


async def _agent(client, hitl=False):
    r = await client.post("/v1/agents", headers=ADMIN, json={
        "email": "a@agents.local", "display_name": "A", "hitl_enabled": hitl})
    assert r.status_code == 201


def _later(days=2):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat()


async def _send(client, **extra):
    r = await client.post(f"{BASE}/messages", headers=ADMIN, json={**PAYLOAD, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _finish_job(mid, status="complete"):
    async with AsyncSessionLocal() as db:
        await db.execute(update(OutboundJob).where(OutboundJob.message_id == mid).values(status=status))
        await db.commit()


async def _count(model):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def _backdate_trash(mid, days):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Message).where(Message.id == mid).values(
            deleted_at=datetime.datetime.utcnow() - datetime.timedelta(days=days)))
        await db.commit()


# --------------------------------------------------------------------------- permanent delete

async def test_permanent_delete_requires_exact_confirm(client):
    await _agent(client)
    mid = await _send(client)
    await _finish_job(mid)
    for q in ("?permanent=true", "?permanent=true&confirm=delete", "?permanent=true&confirm=yes"):
        assert (await client.delete(f"{BASE}/messages/{mid}{q}", headers=ADMIN)).status_code == 422
    assert (await client.get(f"{BASE}/messages/{mid}", headers=ADMIN)).status_code == 200


async def test_permanent_delete_removes_everything_and_leaves_content_free_audit(client):
    await _agent(client)
    mid = await _send(client, attachments=[{"filename": "a.txt", "content_base64": "aGk="}])
    async with AsyncSessionLocal() as db:
        job_id = await db.scalar(select(OutboundJob.id).where(OutboundJob.message_id == mid))
        db.add(OutboundRetryEvent(id="rt1", job_id=job_id, actor="admin:x", previous_attempts=1))
        await db.commit()
    await _finish_job(mid, "failed")
    r = await client.delete(f"{BASE}/messages/{mid}?permanent=true&confirm=DELETE", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"deleted": True, "permanent": True, "message_id": mid}
    assert (await client.get(f"{BASE}/messages/{mid}", headers=ADMIN)).status_code == 404
    for model in (Message, MessageAttachment, OutboundJob, OutboundRetryEvent):
        assert await _count(model) == 0, model
    async with AsyncSessionLocal() as db:
        log = (await db.execute(select(MessagePurgeLog))).scalars().all()
    assert len(log) == 1 and log[0].message_id == mid and log[0].reason == "manual"
    assert log[0].agent_email == "a@agents.local" and log[0].purged_by.startswith("admin:")
    assert "secret" not in repr({c.name: getattr(log[0], c.name) for c in MessagePurgeLog.__table__.columns})


async def test_permanent_delete_refused_while_review_pending_or_send_active(client):
    await _agent(client, hitl=True)
    held = await _send(client)
    r = await client.delete(f"{BASE}/messages/{held}?permanent=true&confirm=DELETE", headers=ADMIN)
    assert r.status_code == 409 and await _count(ReviewItem) == 1
    await make_agent(client, "b@agents.local")
    r = await client.post("/v1/agents", headers=ADMIN, json={"email": "c@agents.local", "display_name": "C", "hitl_enabled": False})
    assert r.status_code == 201
    live = (await client.post("/v1/agents/c@agents.local/messages", headers=ADMIN, json=PAYLOAD)).json()["id"]
    for state in ("pending", "processing"):
        await _finish_job(live, state)
        r = await client.delete(f"/v1/agents/c@agents.local/messages/{live}?permanent=true&confirm=DELETE", headers=ADMIN)
        assert r.status_code == 409, state
    await _finish_job(live, "complete")
    assert (await client.delete(f"/v1/agents/c@agents.local/messages/{live}?permanent=true&confirm=DELETE", headers=ADMIN)).status_code == 200


async def test_permanent_delete_scopes_and_isolation(client):
    await _agent(client)
    await make_agent(client, "b@agents.local")
    mid = await _send(client)
    await _finish_job(mid)
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    other, _ = await make_key(client, "b@agents.local", "full_access")
    url = f"{BASE}/messages/{mid}?permanent=true&confirm=DELETE"
    assert (await client.delete(url)).status_code == 401
    assert (await client.delete(url, headers=ro)).status_code == 403
    assert (await client.delete(url, headers=other)).status_code == 403
    assert (await client.delete(f"/v1/agents/b@agents.local/messages/{mid}?permanent=true&confirm=DELETE", headers=other)).status_code == 404
    assert await _count(Message) == 1
    own, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await client.delete(url, headers=own)).status_code == 200


# --------------------------------------------------------------------------- restore and re-arm

async def test_restore_of_message_not_in_trash_is_409(client):
    await _agent(client)
    mid = await _send(client)
    r = await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "not_in_trash"


async def test_trashed_message_still_readable_by_direct_get(client):
    await _agent(client)
    mid = await _send(client)
    await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
    got = await client.get(f"{BASE}/messages/{mid}", headers=ADMIN)
    assert got.status_code == 200 and got.json()["deleted_at"]


async def test_early_restore_rearms_scheduled_send(client):
    await _agent(client)
    mid = await _send(client, send_at=_later())
    canceled = (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).json()
    assert canceled["status"] == "canceled"
    restored = (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()
    assert restored["status"] == "scheduled" and restored["deleted_at"] is None
    async with AsyncSessionLocal() as db:
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == mid))
        message = await db.get(Message, mid)
        assert job.status == "pending" and job.available_at == message.scheduled_for
        assert message.canceled_by_trash is None
    # and it can be canceled again
    assert (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).json()["status"] == "canceled"


async def test_late_restore_stays_canceled(client):
    await _agent(client)
    mid = await _send(client, send_at=_later())
    await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
    async with AsyncSessionLocal() as db:
        await db.execute(update(Message).where(Message.id == mid).values(
            scheduled_for=datetime.datetime.utcnow() - datetime.timedelta(minutes=5)))
        await db.commit()
    restored = (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()
    assert restored["status"] == "canceled" and restored["deleted_at"] is None
    async with AsyncSessionLocal() as db:
        assert (await db.scalar(select(OutboundJob.status).where(OutboundJob.message_id == mid))) == "cancelled"


async def test_restore_does_not_rearm_when_job_not_cancelled(client):
    await _agent(client)
    mid = await _send(client, send_at=_later())
    await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
    await _finish_job(mid, "failed")  # job changed underneath: compare-and-set must not touch it
    restored = (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()
    assert restored["status"] == "canceled"
    async with AsyncSessionLocal() as db:
        assert (await db.scalar(select(OutboundJob.status).where(OutboundJob.message_id == mid))) == "failed"


async def test_ordinary_trash_and_restore_unchanged(client):
    await _agent(client)
    mid = await _send(client)
    trashed = (await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)).json()
    assert trashed["deleted_at"] and trashed["status"] == "sending"
    assert (await client.post(f"{BASE}/messages/{mid}/restore", headers=ADMIN)).json()["status"] == "sending"


# --------------------------------------------------------------------------- retention janitor

async def test_retention_boundary(client, monkeypatch):
    monkeypatch.setattr(settings, "TRASH_RETENTION_DAYS", 30)
    await _agent(client)
    old, edge, fresh, untrashed = [await _send(client) for _ in range(4)]
    for mid in (old, edge, fresh, untrashed):
        await _finish_job(mid)
    for mid in (old, edge, fresh):
        await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
    now = datetime.datetime.utcnow()
    async with AsyncSessionLocal() as db:
        for mid, delta in ((old, datetime.timedelta(days=31)), (edge, datetime.timedelta(days=30)),
                           (fresh, datetime.timedelta(days=29, hours=23))):
            await db.execute(update(Message).where(Message.id == mid).values(deleted_at=now - delta))
        await db.commit()
    assert await purge_expired_trash(now=now) == 2  # exactly-at-window is purged, just inside is kept
    async with AsyncSessionLocal() as db:
        remaining = set((await db.execute(select(Message.id))).scalars())
        reasons = {(r.message_id, r.reason, r.purged_by) for r in (await db.execute(select(MessagePurgeLog))).scalars()}
    assert remaining == {fresh, untrashed}
    assert reasons == {(old, "retention", "system:retention"), (edge, "retention", "system:retention")}


async def test_janitor_is_idempotent_and_batch_bounded(client, monkeypatch):
    monkeypatch.setattr(settings, "TRASH_RETENTION_DAYS", 1)
    await _agent(client)
    ids = [await _send(client) for _ in range(5)]
    for mid in ids:
        await _finish_job(mid)
        await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
        await _backdate_trash(mid, 5)
    assert await purge_expired_trash(limit=2) == 2
    assert await purge_expired_trash(limit=2) == 2
    assert await purge_expired_trash(limit=2) == 1
    assert await purge_expired_trash(limit=2) == 0
    assert await _count(MessagePurgeLog) == 5 and await _count(Message) == 0


async def test_janitor_disabled_and_skips_blocked_messages(client, monkeypatch):
    await _agent(client)
    mid = await _send(client)
    await _finish_job(mid)
    await client.delete(f"{BASE}/messages/{mid}", headers=ADMIN)
    await _backdate_trash(mid, 400)
    monkeypatch.setattr(settings, "TRASH_RETENTION_DAYS", 0)
    assert await purge_expired_trash() == 0 and await _count(Message) == 1
    monkeypatch.setattr(settings, "TRASH_RETENTION_DAYS", 30)
    await _finish_job(mid, "processing")  # an active send must never be purged out from under the worker
    assert await purge_expired_trash() == 0 and await _count(Message) == 1
    await _finish_job(mid, "complete")
    assert await purge_expired_trash() == 1


def test_retention_setting_validated():
    from pydantic import ValidationError
    from app.config import Settings
    assert Settings(TRASH_RETENTION_DAYS=0).TRASH_RETENTION_DAYS == 0
    for bad in (-1, 4000):
        with pytest.raises(ValidationError):
            Settings(TRASH_RETENTION_DAYS=bad)


# --------------------------------------------------------------------------- MCP

async def _mcp(client, headers, **arguments):
    r = await client.post("/mcp/rpc", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delete_message", "arguments": arguments}})
    result = r.json()["result"]
    import json
    text = result["content"][0]["text"]
    return result["isError"], text if result["isError"] else json.loads(text)


async def test_mcp_delete_message_permanent(client):
    await _agent(client)
    mid = await _send(client)
    await _finish_job(mid)
    a = {"agent_email": "a@agents.local", "message_id": mid}
    err, out = await _mcp(client, ADMIN, permanent=True, **a)
    assert err and "confirm=DELETE" in out
    err, out = await _mcp(client, ADMIN, permanent="yes", confirm="DELETE", **a)
    assert err
    ro, _ = await make_key(client, "a@agents.local", "read_only")
    assert (await _mcp(client, ro, permanent=True, confirm="DELETE", **a))[0]
    err, out = await _mcp(client, ADMIN, permanent=True, confirm="DELETE", **a)
    assert not err and out["permanent"] is True
    assert await _count(Message) == 0
    err, out = await _mcp(client, ADMIN, **a)  # message is gone
    assert err


async def test_mcp_plain_delete_still_trashes(client):
    await _agent(client)
    mid = await _send(client)
    err, out = await _mcp(client, ADMIN, agent_email="a@agents.local", message_id=mid)
    assert not err and out["deleted_at"]
