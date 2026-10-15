"""Resolve pending reviews that outlive their inbox's configured expiry window."""

import asyncio
import datetime
import logging

from fastapi import HTTPException
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Agent, ReviewItem
from app.services.hitl_service import HitlService

logger = logging.getLogger("review_expiry")
SWEEP_INTERVAL_SECONDS = 60
SWEEP_BATCH_SIZE = 100
EXPIRY_REVIEWER = "system:expiry"


async def expire_stale_reviews(now: datetime.datetime | None = None, limit: int = SWEEP_BATCH_SIZE) -> int:
    """Resolve up to ``limit`` expired reviews; the default action is reject, never approve.

    Resolution uses the same compare-and-set as a human decision, so overlapping sweeps
    or a concurrent human decision resolve each review exactly once.
    """
    now = now or datetime.datetime.utcnow()
    resolved = 0
    cap = max(1, min(int(limit), 500))
    async with AsyncSessionLocal() as db:
        agents = (await db.execute(
            select(Agent.email, Agent.review_expiry_hours, Agent.review_expiry_action)
            .where(Agent.review_expiry_hours.is_not(None))
        )).all()
        due = []
        for email, hours, action in agents:
            if len(due) >= cap:
                break
            ids = (await db.execute(
                select(ReviewItem.id)
                .where(
                    ReviewItem.agent_email == email,
                    ReviewItem.status == "pending",
                    ReviewItem.created_at <= now - datetime.timedelta(hours=hours),
                )
                .order_by(ReviewItem.created_at, ReviewItem.id)
                .limit(cap - len(due))
            )).scalars().all()
            due.extend((rid, hours, action) for rid in ids)
        for review_id, hours, action in due:
            decision = "approve" if action == "approve" else "reject"
            try:
                await HitlService.resolve_review(
                    db, review_id, decision,
                    notes=f"Review expired after {hours} hour(s); {decision}ed automatically",
                    reviewer_id=EXPIRY_REVIEWER,
                )
                resolved += 1
            except HTTPException:
                await db.rollback()  # already resolved by someone else
    return resolved


async def run_review_expiry_sweeper() -> None:
    while True:
        try:
            count = await expire_stale_reviews()
            if count:
                logger.info("Auto-resolved %d expired review(s)", count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Review expiry sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
