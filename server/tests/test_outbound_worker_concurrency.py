"""Regression: concurrent workers must claim each queued send exactly once."""
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Message, OutboundJob
from app.services import outbound_worker
from app.services.email_service import EmailService
from tests.conftest import ADMIN

SENDER = "sender@agents.example.com"


async def test_concurrent_workers_dispatch_each_job_exactly_once(client, monkeypatch):
    await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": False})
    total = 24
    for i in range(total):
        r = await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
            "recipient": f"r{i:02d}@example.org", "subject": "s", "body_text": "b"})
        assert r.status_code in (200, 201, 202), r.text

    dispatched: list[str] = []

    async def fake_dispatch(db, message, agent):
        dispatched.append(message.id)
        await asyncio.sleep(0)  # yield so other workers interleave between claim and completion
        return "accepted_by_relay", "ok"

    monkeypatch.setattr(EmailService, "dispatch_to_upstream", staticmethod(fake_dispatch))

    async def worker():
        while await outbound_worker._dispatch_one():
            pass

    await asyncio.gather(*(worker() for _ in range(6)))

    assert len(dispatched) == total and len(set(dispatched)) == total
    async with AsyncSessionLocal() as db:
        jobs = (await db.execute(select(OutboundJob))).scalars().all()
        messages = (await db.execute(select(Message))).scalars().all()
    assert {j.status for j in jobs} == {"complete"} and all(j.attempts == 1 for j in jobs)
    assert {m.status for m in messages} == {"accepted_by_relay"}
