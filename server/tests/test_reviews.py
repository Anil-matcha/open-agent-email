import asyncio

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import Message, OutboundJob, ReviewItem
from tests.conftest import ADMIN, make_agent, make_key

MSGS = "/v1/agents/a@agents.local/messages"
PAYLOAD = {"recipient": "to@example.com", "subject": "hello", "body_text": "body"}


async def _held(client, email="a@agents.local"):
    await make_agent(client, email)  # hitl_enabled defaults to True
    r = await client.post(f"/v1/agents/{email}/messages", headers=ADMIN, json=PAYLOAD)
    assert r.status_code == 201 and r.json()["status"] == "pending_review", r.text
    async with AsyncSessionLocal() as db:
        return (await db.scalar(select(ReviewItem).where(ReviewItem.message_id == r.json()["id"]))).id


async def _jobs():
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(OutboundJob))


async def test_outbound_held_and_no_job_before_approval(client):
    await _held(client)
    assert await _jobs() == 0


async def test_approve_enqueues_exactly_one_job_and_records_reviewer(client):
    rid = await _held(client)
    r = await client.post(f"/v1/reviews/{rid}/approve", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    again = await client.post(f"/v1/reviews/{rid}/approve", headers=ADMIN)
    assert again.status_code == 409
    assert await _jobs() == 1
    async with AsyncSessionLocal() as db:
        review = await db.get(ReviewItem, rid)
        assert review.reviewed_by.startswith("admin:") and review.resolved_at is not None


async def test_concurrent_approvals_enqueue_one_job(client):
    rid = await _held(client)
    results = await asyncio.gather(*[client.post(f"/v1/reviews/{rid}/approve", headers=ADMIN) for _ in range(5)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(409) == 4
    assert await _jobs() == 1


async def test_reject_creates_no_job_and_cannot_be_approved_after(client):
    rid = await _held(client)
    assert (await client.post(f"/v1/reviews/{rid}/reject", headers=ADMIN)).status_code == 200
    assert (await client.post(f"/v1/reviews/{rid}/approve", headers=ADMIN)).status_code == 409
    assert await _jobs() == 0
    async with AsyncSessionLocal() as db:
        assert (await db.scalar(select(Message.status))) == "rejected"


async def test_reviewer_key_is_inbox_scoped(client):
    rid = await _held(client)
    await make_agent(client, "b@agents.local")
    other, _ = await make_key(client, "b@agents.local", "reviewer")
    assert (await client.get(f"/v1/reviews/{rid}", headers=other)).status_code == 404
    assert (await client.post(f"/v1/reviews/{rid}/approve", headers=other)).status_code == 404
    assert (await client.get("/v1/reviews", headers=other)).json() == []
    own, _ = await make_key(client, "a@agents.local", "reviewer")
    assert (await client.post(f"/v1/reviews/{rid}/approve", headers=own)).status_code == 200


async def test_non_reviewer_scopes_cannot_approve(client):
    rid = await _held(client)
    for scope in ("read_only", "full_access"):
        headers, _ = await make_key(client, "a@agents.local", scope)
        assert (await client.post(f"/v1/reviews/{rid}/approve", headers=headers)).status_code == 403
    assert await _jobs() == 0


async def test_invalid_status_filter_rejected(client):
    assert (await client.get("/v1/reviews?status=bogus", headers=ADMIN)).status_code == 422
