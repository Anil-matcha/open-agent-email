from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Agent, Message, ReviewItem
from app.services.email_service import EmailService
from tests.conftest import ADMIN, make_agent


async def test_quarantine_policy_creates_pending_review_even_when_hitl_is_off(client):
    await make_agent(client, "dmarc@agents.local")
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == "dmarc@agents.local"))
        agent.hitl_enabled = False
        await db.commit()

        message, _scan, review = await EmailService.process_inbound(
            db=db,
            agent=agent,
            sender="sender@example.org",
            subject="Policy quarantine",
            body_text="This message failed DMARC.",
            dmarc_status="fail",
            force_review=True,
        )
        await db.commit()

        persisted_message = await db.scalar(select(Message).where(Message.id == message.id))
        persisted_review = await db.scalar(select(ReviewItem).where(ReviewItem.message_id == message.id))
        assert persisted_message.status == "pending_review"
        assert review is not None and persisted_review is not None
        assert "DMARC policy requests quarantine" in persisted_review.reason
        assert persisted_message.verified_domain is None


async def test_verified_domain_requires_dmarc_pass(client):
    await make_agent(client, "auth@agents.local")
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.email == "auth@agents.local"))
        failed, *_ = await EmailService.process_inbound(
            db, agent, "sender@example.org", "Fail", "body", dmarc_status="fail"
        )
        passed, *_ = await EmailService.process_inbound(
            db, agent, "sender@example.org", "Pass", "body", dmarc_status="pass"
        )
        assert failed.verified_domain is None
        assert passed.verified_domain == "example.org"
