from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Message, OutboundJob
from app.services.outbound_worker import _dispatch_one
from tests.conftest import ADMIN, make_agent

SENDER = "sender@agents.example.com"
PEER = "peer@agents.example.com"


async def test_worker_finds_the_owning_agent_and_delivers_locally(client):
    """Regression: the worker looked the agent up by email via the primary-key getter, found nothing,
    and failed every queued message with "owning agent no longer exists"."""
    await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": False})
    await make_agent(client, PEER)
    sent = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": PEER, "subject": "s", "body_text": "b"})).json()
    assert sent["status"] == "sending"

    assert await _dispatch_one() is True

    async with AsyncSessionLocal() as db:
        message = await db.get(Message, sent["id"])
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == sent["id"]))
    assert message.status == "delivered" and message.delivery_error is None
    assert job.status == "complete" and job.attempts == 1


async def test_worker_with_unconfigured_relay_fails_for_the_right_reason(client):
    await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": False})
    sent = (await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
        "recipient": "someone@example.org", "subject": "s", "body_text": "b"})).json()
    await _dispatch_one()
    async with AsyncSessionLocal() as db:
        message = await db.get(Message, sent["id"])
    assert "relay is not configured" in message.delivery_error  # not "owning agent no longer exists"
