"""Durable single-message dispatch worker backed by the application database."""

import asyncio
import datetime
import logging

from sqlalchemy import or_, select, update

from app.database import AsyncSessionLocal
from app.models import Agent, Message, OutboundJob
from app.services.email_service import EmailService
from app.services.ws_manager import ws_manager
from app.services.webhook_events import enqueue_outbound_status_webhook_event

logger = logging.getLogger("outbound_worker")
MAX_ATTEMPTS = 8
LEASE_SECONDS = 300
STALE_DELIVERY_UNKNOWN = (
    "Worker lease expired while dispatching. The SMTP relay may have accepted the message; "
    "automatic retry was stopped to avoid silently sending a duplicate. An administrator "
    "must confirm duplicate risk before retrying."
)


async def _dispatch_one() -> bool:
    now = datetime.datetime.utcnow()
    stale_before = now - datetime.timedelta(seconds=LEASE_SECONDS)
    async with AsyncSessionLocal() as db:
        # A worker may die after SMTP accepted the message but before recording success.
        # Never automatically reclaim that job: SMTP cannot tell us whether acceptance happened.
        stale_result = await db.execute(
            select(OutboundJob)
            .where(
                OutboundJob.status == "processing",
                or_(OutboundJob.locked_at.is_(None), OutboundJob.locked_at < stale_before),
            )
            .order_by(OutboundJob.created_at)
            .limit(1)
        )
        stale_job = stale_result.scalar_one_or_none()
        if stale_job:
            stale_id = stale_job.id
            stale_message_id = stale_job.message_id
            marked = await db.execute(
                update(OutboundJob)
                .where(
                    OutboundJob.id == stale_id,
                    OutboundJob.status == "processing",
                    or_(OutboundJob.locked_at.is_(None), OutboundJob.locked_at < stale_before),
                )
                .values(status="uncertain", locked_at=None, last_error=STALE_DELIVERY_UNKNOWN)
            )
            if marked.rowcount == 1:
                message = await db.get(Message, stale_message_id)
                if message:
                    message.status = "delivery_unknown"
                    message.delivery_error = STALE_DELIVERY_UNKNOWN
                    await enqueue_outbound_status_webhook_event(
                        db,
                        event_type="email.delivery_unknown",
                        message_id=message.id,
                        agent_email=message.agent_email,
                        recipient=message.recipient,
                        status=message.status,
                        created_at=message.created_at,
                        conversation_id=message.conversation_id,
                    )
                await db.commit()
                logger.error("Outbound message %s has unknown delivery; automatic retry stopped", stale_message_id)
                if message:
                    payload = {
                        "id": message.id,
                        "agent_email": message.agent_email,
                        "direction": message.direction,
                        "sender": message.sender,
                        "recipient": message.recipient,
                        "subject": message.subject,
                        "status": "delivery_unknown",
                        "thread_id": message.thread_id,
                        "conversation_id": message.conversation_id,
                        "created_at": message.created_at.isoformat(),
                        "delivery_error": message.delivery_error,
                    }
                    await ws_manager.send_agent_message(message.agent_email, "email.delivery_unknown", payload)
                    await ws_manager.broadcast_event("email.delivery_unknown", payload)
            else:
                await db.rollback()
            return True

        result = await db.execute(
            select(OutboundJob)
            .where(OutboundJob.status == "pending", OutboundJob.available_at <= now)
            .order_by(OutboundJob.available_at, OutboundJob.created_at)
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if not job:
            return False

        old_status = job.status
        claim = update(OutboundJob).where(
            OutboundJob.id == job.id,
            OutboundJob.status == old_status,
            OutboundJob.available_at <= now,
        ).values(status="processing", locked_at=now, attempts=OutboundJob.attempts + 1)
        claimed = await db.execute(claim)
        if claimed.rowcount != 1:
            await db.rollback()
            return True
        await db.commit()
        await db.refresh(job)
        claim_locked_at = job.locked_at

        message = await db.get(Message, job.message_id)
        # Agent's primary key is `id`; messages reference the inbox by its unique email address.
        agent = await db.scalar(select(Agent).where(Agent.email == message.agent_email)) if message else None
        if not message or not agent:
            status, info = "failed", "Outbound message or owning agent no longer exists"
        else:
            try:
                status, info = await EmailService.dispatch_to_upstream(db, message, agent)
            except Exception as exc:
                logger.exception("Outbound dispatch failed for message %s", job.message_id)
                status, info = "failed", f"Dispatch error: {type(exc).__name__}"

        job = await db.get(OutboundJob, job.id)
        message = await db.get(Message, job.message_id)
        if status == "failed" and job.attempts < MAX_ATTEMPTS:
            delay = min(3600, 2 ** min(job.attempts, 10))
            rescheduled = await db.execute(
                update(OutboundJob)
                .where(
                    OutboundJob.id == job.id,
                    OutboundJob.status == "processing",
                    OutboundJob.locked_at == claim_locked_at,
                )
                .values(
                    status="pending",
                    available_at=datetime.datetime.utcnow() + datetime.timedelta(seconds=delay),
                    locked_at=None,
                    last_error=info,
                )
            )
            if rescheduled.rowcount != 1:
                await db.rollback()
                logger.warning("Discarding stale dispatch result for outbound message %s", job.message_id)
                return True
            if message:
                message.status = "sending"
                message.delivery_error = info
            await db.commit()
            logger.warning("Outbound message %s failed; retry %s scheduled", job.message_id, job.attempts)
            return True

        completed_status = (
            "uncertain" if status == "delivery_unknown"
            else "blocked" if status == "blocked"
            else "failed" if status == "failed"
            else "complete"
        )
        completed = await db.execute(
            update(OutboundJob)
            .where(
                OutboundJob.id == job.id,
                OutboundJob.status == "processing",
                OutboundJob.locked_at == claim_locked_at,
            )
            .values(
                status=completed_status,
                locked_at=None,
                last_error=info if status in {"failed", "delivery_unknown", "blocked"} else None,
            )
        )
        if completed.rowcount != 1:
            await db.rollback()
            logger.warning("Discarding stale dispatch result for outbound message %s", job.message_id)
            return True
        if message:
            message.status = status
            message.delivery_error = info if status in {"failed", "delivery_unknown", "blocked"} else None
            # `email.sent` means the message left this system: accepted by the upstream relay,
            # or delivered to a local inbox. It is not a delivery confirmation.
            event_types = {
                "accepted_by_relay": ["email.accepted_by_relay", "email.sent"],
                "delivered": ["email.sent"],
                "failed": ["email.failed"],
                "delivery_unknown": ["email.delivery_unknown"],
                "blocked": ["email.blocked"],
            }.get(status, [])
            # This runs only after the conditional job transition succeeds, so
            # stale/duplicate dispatch results cannot create duplicate events.
            for event_type in event_types:
                await enqueue_outbound_status_webhook_event(
                    db,
                    event_type=event_type,
                    message_id=message.id,
                    agent_email=message.agent_email,
                    recipient=message.recipient,
                    status=message.status,
                    created_at=message.created_at,
                    conversation_id=message.conversation_id,
                )
        await db.commit()

        if message:
            payload = {
                "id": message.id,
                "agent_email": message.agent_email,
                "direction": message.direction,
                "sender": message.sender,
                "recipient": message.recipient,
                "subject": message.subject,
                "status": status,
                "thread_id": message.thread_id,
                "conversation_id": message.conversation_id,
                "created_at": message.created_at.isoformat(),
                "delivery_error": message.delivery_error,
            }
            event_name = {
                "failed": "email.failed",
                "blocked": "email.blocked",
                "accepted_by_relay": "email.accepted",
                "delivered": "email.sent",
                "delivery_unknown": "email.delivery_unknown",
            }[status]
            await ws_manager.send_agent_message(message.agent_email, event_name, payload)
            await ws_manager.broadcast_event(event_name, payload)
        return True


async def run_outbound_worker() -> None:
    while True:
        try:
            found = await _dispatch_one()
            if not found:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Outbound worker iteration failed")
            await asyncio.sleep(1)

