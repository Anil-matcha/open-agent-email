import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Agent, Message, OutboundJob, ReviewItem, SmtpConfig
from app.services.email_service import EmailService
ADMIN = {"Authorization": "Bearer test-admin-key"}


async def make_agent(client, email):
    response = await client.post(
        "/v1/agents", headers=ADMIN,
        json={"email": email, "display_name": email},
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_agent_test_send_queues_platform_message_for_external_delivery(client):
    email = "test-inbox@example.com"
    await make_agent(client, email)
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == email))
        agent.hitl_enabled = False
        await db.commit()

    response = await client.post(
        f"/v1/agents/{email}/test",
        headers={**ADMIN, "Idempotency-Key": "agent-test-1"},
    )

    assert response.status_code == 202, response.text
    result = response.json()
    assert result["status"] == "accepted"
    assert result["message_id"].startswith("msg_")
    async with AsyncSessionLocal() as db:
        message = await db.get(Message, result["message_id"])
        assert message is not None
        assert message.sender == "noreply@open-agent-email.dev"
        assert message.recipient == email
        assert message.direction == "outbound"
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == message.id))
        assert job is not None
        assert job.status == "pending"  # accepted even without a relay configured


@pytest.mark.asyncio
async def test_same_configured_from_address_still_forces_external_relay(client, monkeypatch):
    email = "self-sender@example.com"
    await make_agent(client, email)
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == email))
        agent.hitl_enabled = False
        db.add(SmtpConfig(
            id="default", host="smtp.example.test", port="587", username="user",
            password="", enabled=True, from_email=email,
        ))
        await db.commit()

    response = await client.post(f"/v1/agents/{email}/test", headers=ADMIN)
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["status"] == "accepted"
    sent = []

    def fake_smtp(**kwargs):
        sent.append(kwargs)
        return True, "relay accepted"

    monkeypatch.setattr("app.services.email_service._send_smtp_sync", fake_smtp)
    async with AsyncSessionLocal() as db:
        message = await db.get(Message, result["message_id"])
        agent = await db.scalar(select(Agent).where(Agent.email == email))
        status, _ = await EmailService.dispatch_to_upstream(db, message, agent)

    assert status == "accepted_by_relay"
    assert len(sent) == 1
    assert sent[0]["to_addr"] == email
    assert sent[0]["from_addr"] != email


@pytest.mark.asyncio
async def test_agent_test_send_reports_pending_review_and_is_idempotent(client):
    email = "review-test@example.com"
    await make_agent(client, email)
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == email))
        agent.hitl_enabled = True
        await db.commit()

    key = "review-test-key"
    first = await client.post(f"/v1/agents/{email}/test", headers={**ADMIN, "Idempotency-Key": key})
    second = await client.post(f"/v1/agents/{email}/test", headers={**ADMIN, "Idempotency-Key": key})

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert first.json()["status"] == "pending_review"
    async with AsyncSessionLocal() as db:
        review = await db.scalar(select(ReviewItem).where(ReviewItem.message_id == first.json()["message_id"]))
        job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == first.json()["message_id"]))
        assert review is not None
        assert job is None


@pytest.mark.asyncio
async def test_agent_test_send_respects_agent_scope(client):
    email = "scoped-test@example.com"
    await make_agent(client, email)
    key_response = await client.post(
        "/v1/api-keys", headers=ADMIN,
        json={"name": "read-only-test", "agent_email": email, "scope": "read_only"},
    )
    assert key_response.status_code == 201, key_response.text
    read_key = {"Authorization": f"Bearer {key_response.json()['key']}"}

    response = await client.post(f"/v1/agents/{email}/test", headers=read_key)

    assert response.status_code == 403

    reviewer_response = await client.post(
        "/v1/api-keys", headers=ADMIN,
        json={"name": "reviewer-test", "agent_email": email, "scope": "reviewer"},
    )
    assert reviewer_response.status_code == 201, reviewer_response.text
    reviewer_key = {"Authorization": f"Bearer {reviewer_response.json()['key']}"}
    assert (await client.post(f"/v1/agents/{email}/test", headers=reviewer_key)).status_code == 403

    write_response = await client.post(
        "/v1/api-keys", headers=ADMIN,
        json={"name": "full-access-test", "agent_email": email, "scope": "full_access"},
    )
    assert write_response.status_code == 201, write_response.text
    write_key = {"Authorization": f"Bearer {write_response.json()['key']}"}
    queued = await client.post(f"/v1/agents/{email}/test", headers=write_key)
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "pending_review"

    foreign = await client.post(f"/v1/agents/other@example.com/test", headers=write_key)
    assert foreign.status_code in {403, 404}

