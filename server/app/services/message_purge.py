"""Permanent message deletion and the trash-retention janitor."""

import asyncio
import datetime
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import (
    Message, MessageAttachment, MessagePurgeLog, OutboundJob, OutboundRetryEvent, ReviewItem, ReviewLinkToken,
)

logger = logging.getLogger("message_purge")
SWEEP_INTERVAL_SECONDS = 300
SWEEP_BATCH_SIZE = 100
# A job in either state may still produce an outbound send; deleting it would drop that send silently.
BLOCKING_JOB_STATES = ("pending", "processing")


async def purge_blocker(db: AsyncSession, message: Message) -> str | None:
    """Why this message cannot be purged right now, or None when it can."""
    if message.status == "pending_review":
        return "Resolve the pending review before permanently deleting this message"
    job_status = await db.scalar(select(OutboundJob.status).where(OutboundJob.message_id == message.id))
    if job_status in BLOCKING_JOB_STATES:
        return "An outbound send for this message is queued or in progress; wait for it or cancel it first"
    return None


async def purge_message(db: AsyncSession, message: Message, *, purged_by: str, reason: str) -> None:
    """Delete the message and everything hanging off it, and record an audit row. Caller commits."""
    message_id, agent_email = message.id, message.agent_email
    job_ids = list((await db.execute(select(OutboundJob.id).where(OutboundJob.message_id == message_id))).scalars())
    review_ids = list((await db.execute(select(ReviewItem.id).where(ReviewItem.message_id == message_id))).scalars())
    # Explicit deletes: SQLite does not enforce ON DELETE CASCADE unless foreign keys are switched on.
    if job_ids:
        await db.execute(delete(OutboundRetryEvent).where(OutboundRetryEvent.job_id.in_(job_ids)))
        await db.execute(delete(OutboundJob).where(OutboundJob.id.in_(job_ids)))
    if review_ids:
        await db.execute(delete(ReviewLinkToken).where(ReviewLinkToken.review_id.in_(review_ids)))
        await db.execute(delete(ReviewItem).where(ReviewItem.id.in_(review_ids)))
    await db.execute(delete(MessageAttachment).where(MessageAttachment.message_id == message_id))
    db.add(MessagePurgeLog(
        agent_email=agent_email, message_id=message_id, purged_by=purged_by[:128], reason=reason,
        purged_at=datetime.datetime.utcnow(),
    ))
    await db.execute(delete(Message).where(Message.id == message_id))
    db.expunge(message)


async def purge_expired_trash(now: datetime.datetime | None = None, limit: int = SWEEP_BATCH_SIZE) -> int:
    """Purge messages trashed for longer than TRASH_RETENTION_DAYS. Bounded and safe to repeat."""
    days = settings.TRASH_RETENTION_DAYS
    if days <= 0:
        return 0
    now = now or datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(days=days)
    purged = 0
    async with AsyncSessionLocal() as db:
        candidates = (await db.execute(
            select(Message).where(Message.deleted_at.is_not(None), Message.deleted_at <= cutoff)
            .order_by(Message.deleted_at, Message.id).limit(max(1, min(int(limit), 500)))
        )).scalars().all()
        for message in candidates:
            if await purge_blocker(db, message):
                continue
            await purge_message(db, message, purged_by="system:retention", reason="retention")
            purged += 1
        await db.commit()
    return purged


async def run_trash_janitor() -> None:
    while True:
        try:
            count = await purge_expired_trash()
            if count:
                logger.info("Purged %d trashed message(s) past retention", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Trash retention sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
