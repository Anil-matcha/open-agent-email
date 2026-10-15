import datetime
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from fastapi import HTTPException

from app.models import ReviewItem, Message, OutboundJob
from app.services.ws_manager import ws_manager
from app.services.webhook_events import (
    enqueue_email_received_webhook_event,
    enqueue_email_webhook_event,
)

class HitlService:

    @classmethod
    async def resolve_review(
        cls,
        db: AsyncSession,
        review_id: str,
        action: str,  # approve or reject
        notes: str = None,
        reviewer_id: str = "",
        commit: bool = True,
        publish: bool = True,
    ) -> ReviewItem:
        """
        Human reviewer approves or rejects a held message.
        """
        if action not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="Invalid action. Must be 'approve' or 'reject'")

        result = await db.execute(
            update(ReviewItem)
            .where(ReviewItem.id == review_id, ReviewItem.status == "pending")
            .values(
                status="approved" if action == "approve" else "rejected",
                resolved_at=datetime.datetime.utcnow(),
                reviewer_notes=notes,
                reviewed_by=reviewer_id,
            )
        )
        if result.rowcount != 1:
            exists = await db.scalar(select(ReviewItem.id).where(ReviewItem.id == review_id))
            if not exists:
                raise HTTPException(status_code=404, detail="Review item not found")
            raise HTTPException(status_code=409, detail="Review was already resolved by another request")

        review = await db.get(ReviewItem, review_id)
        message = await db.get(Message, review.message_id)
        if message:
            deferred = (
                action == "approve" and message.direction == "outbound"
                and message.scheduled_for is not None and message.scheduled_for > datetime.datetime.utcnow()
            )
            message.status = (
                ("scheduled" if deferred else "sending") if message.direction == "outbound" else "delivered"
            ) if action == "approve" else "rejected"
            await enqueue_email_webhook_event(
                db,
                agent_email=message.agent_email,
                event_type="email.review_approved" if action == "approve" else "email.review_rejected",
                message_id=message.id,
                direction=message.direction,
                review_id=review.id,
                status=message.status,
                created_at=message.created_at,
                conversation_id=message.conversation_id,
            )
            if action == "approve" and message.direction == "inbound":
                await enqueue_email_received_webhook_event(
                    db,
                    agent_email=message.agent_email,
                    sender=message.sender,
                    recipient=message.recipient,
                    thread_id=message.thread_id,
                    conversation_id=message.conversation_id,
                    status=message.status,
                    message_id=message.id,
                    created_at=message.created_at,
                )
            if action == "approve" and message.direction == "outbound":
                db.add(OutboundJob(
                    id=f"out_{uuid.uuid4().hex[:24]}",
                    message_id=message.id,
                    status="pending",
                    attempts=0,
                    available_at=max(datetime.datetime.utcnow(), message.scheduled_for or datetime.datetime.min),
                    created_at=datetime.datetime.utcnow(),
                ))
        # Callers that store a replay receipt can commit both atomically.
        if commit:
            await db.commit()

        if publish:
            await cls.publish_resolution(review, message, action, notes)

        if commit:
            await db.refresh(review)

        return review

    @classmethod
    async def publish_resolution(cls, review: ReviewItem, message: Message | None,
                                 action: str, notes: str | None) -> None:
        if message and action == "approve":
            msg_payload = {
                "id": message.id,
                "agent_email": message.agent_email,
                "direction": message.direction,
                "sender": message.sender,
                "recipient": message.recipient,
                "subject": message.subject,
                "status": message.status,
                "thread_id": message.thread_id,
                "created_at": message.created_at.isoformat(),
            }
            if message.direction == "outbound":
                await ws_manager.send_agent_message(message.agent_email, "email.queued", msg_payload)
                await ws_manager.broadcast_event("email.queued", msg_payload)
            else:
                await ws_manager.send_agent_message(message.agent_email, "email.received", msg_payload)
                await ws_manager.broadcast_event("email.received", msg_payload)

        # Broadcast review resolved
        await ws_manager.broadcast_event("review.resolved", {
            "review_id": review.id,
            "message_id": review.message_id,
            "status": review.status,
            "notes": notes
        })
