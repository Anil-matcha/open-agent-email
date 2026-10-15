"""Periodic, deduplicated wake-ups for due contact follow-ups."""

import asyncio
import datetime
import logging
import uuid

from sqlalchemy import and_, select, update

from app.database import AsyncSessionLocal
from app.models import Contact, ContactEngagement, RecipientSuppression, WebhookDelivery
from app.services.webhook_events import _subscribed_endpoints, build_payload

logger = logging.getLogger("contact_due_webhooks")
SWEEP_INTERVAL_SECONDS = 60
SWEEP_BATCH_SIZE = 200


async def publish_due_contact_events(now: datetime.datetime | None = None, limit: int = SWEEP_BATCH_SIZE) -> int:
    """Atomically claim due schedules and enqueue metadata-only webhook deliveries.

    A schedule is claimed by recording the exact ``next_due_at`` value that was
    notified. Updating the due time resets this marker, allowing the new
    schedule to produce another wake-up. Each sweep is bounded to ``limit``
    schedules; failed transactions leave every marker eligible for retry.
    """
    now = now or datetime.datetime.utcnow()
    async with AsyncSessionLocal() as db:
        due_rows = (await db.execute(
            select(ContactEngagement, Contact)
            .join(Contact, and_(
                Contact.id == ContactEngagement.contact_id,
                Contact.agent_email == ContactEngagement.agent_email,
            ))
            .where(
                ContactEngagement.next_due_at.is_not(None),
                ContactEngagement.next_due_at <= now,
                ContactEngagement.due_notified_at.is_distinct_from(ContactEngagement.next_due_at),
                ContactEngagement.replied_at.is_(None),
                ContactEngagement.stage != "closed",
                ~select(RecipientSuppression.id).where(
                    RecipientSuppression.agent_email == ContactEngagement.agent_email,
                    RecipientSuppression.email == Contact.email,
                ).exists(),
            )
            .order_by(ContactEngagement.next_due_at, ContactEngagement.id)
            .limit(max(1, min(int(limit), 500)))
        )).all()

        claimed = 0
        for engagement, contact in due_rows:
            endpoints = await _subscribed_endpoints(db, engagement.agent_email, "contact.due")
            scheduled_at = engagement.next_due_at
            marker = await db.execute(update(ContactEngagement).where(
                ContactEngagement.id == engagement.id,
                ContactEngagement.next_due_at == scheduled_at,
                ContactEngagement.next_due_at <= now,
                ContactEngagement.replied_at.is_(None),
                ContactEngagement.stage != "closed",
                ContactEngagement.due_notified_at.is_distinct_from(scheduled_at),
                ~select(RecipientSuppression.id).where(
                    RecipientSuppression.agent_email == engagement.agent_email,
                    RecipientSuppression.email == contact.email,
                ).exists(),
            ).values(due_notified_at=scheduled_at))
            if marker.rowcount != 1:
                continue

            # A schedule is consumed even without subscribers; endpoints
            # added later receive future due schedules, not historical alerts.
            timestamp = scheduled_at.isoformat()
            for endpoint in endpoints:
                event_id = f"evt_{uuid.uuid4().hex}"
                db.add(WebhookDelivery(
                    id=event_id,
                    endpoint_id=endpoint.id,
                    agent_email=engagement.agent_email,
                    event_type="contact.due",
                    payload=build_payload("contact.due", {
                        "id": event_id,
                        "agent_email": engagement.agent_email,
                        "engagement_id": engagement.id,
                        "contact_id": contact.id,
                        "stage": engagement.stage[:32],
                        "next_due_at": timestamp,
                        "created_at": now.isoformat(),
                    }),
                    status="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                ))
            claimed += 1

        await db.commit()
        return claimed


async def run_contact_due_sweeper() -> None:
    """Sweep once per minute; a database marker makes overlapping workers safe."""
    while True:
        try:
            count = await publish_due_contact_events()
            if count:
                logger.info("Queued %d contact.due wake-up(s)", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Contact due webhook sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
