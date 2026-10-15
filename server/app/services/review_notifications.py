"""Email a reviewer single-use approve/reject links for a held outbound message."""

import asyncio
import datetime
import hashlib
import logging
import secrets

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Agent, Message, ReviewItem, ReviewLinkToken, SmtpConfig
from app.services.smtp_secrets import decrypt_password
from app.services.unsubscribe import public_base_url

logger = logging.getLogger("review_notifications")
LINK_LIFETIME = datetime.timedelta(hours=72)
PREVIEW_CHARS = 600
_tasks: set[asyncio.Task] = set()


def issue_review_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("ascii")).hexdigest()


def schedule_review_notification(review_id: str) -> None:
    """Fire-and-forget so a slow relay never delays the API response."""
    task = asyncio.create_task(notify_review_requested(review_id), name=f"review-notify-{review_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain_review_notifications() -> None:
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _send(config: SmtpConfig, password: str, from_addr: str, to_addr: str, subject: str, body: str):
    from app.services.email_service import _send_smtp_sync
    port = int(config.port) if str(config.port).isdigit() else 587
    return _send_smtp_sync(
        host=config.host, port=port, username=config.username, password=password,
        use_tls=config.use_tls, use_ssl=config.use_ssl, from_addr=from_addr, to_addr=to_addr,
        subject=subject, body_text=body,
    )


async def notify_review_requested(review_id: str) -> bool:
    """Create two hashed link tokens and email them. Never raises; returns True if the mail was accepted."""
    try:
        async with AsyncSessionLocal() as db:
            review = await db.get(ReviewItem, review_id)
            if not review or review.status != "pending":
                return False
            message = await db.get(Message, review.message_id)
            agent = await db.scalar(select(Agent).where(Agent.email == review.agent_email))
            if not message or not agent or message.direction != "outbound" or not agent.review_notify_email:
                return False
            base_url = public_base_url()
            if not base_url:
                logger.warning("Review links not sent for %s: PUBLIC_BASE_URL is not a public https origin", review_id)
                return False
            config = await db.scalar(select(SmtpConfig).where(SmtpConfig.id == "default"))
            if not config or not config.enabled or not config.host:
                logger.warning("Review links not sent for %s: upstream SMTP relay is not configured", review_id)
                return False
            try:
                password = decrypt_password(config.password)
            except RuntimeError as exc:
                logger.error("Review links not sent for %s: %s", review_id, exc)
                return False

            now = datetime.datetime.utcnow()
            lifetime = LINK_LIFETIME
            if agent.review_expiry_hours:
                lifetime = min(lifetime, datetime.timedelta(hours=agent.review_expiry_hours))
            links = {}
            for action in ("approve", "reject"):
                raw, digest = issue_review_token()
                db.add(ReviewLinkToken(
                    token_hash=digest, review_id=review.id, action=action,
                    reviewer_email=agent.review_notify_email, created_at=now, expires_at=now + lifetime,
                ))
                links[action] = f"{base_url}/review-link/{raw}"
            # Persist before any mail containing these capabilities leaves the server.
            await db.commit()

            preview = " ".join(message.body_text.split())[:PREVIEW_CHARS]
            body = (
                f"{agent.display_name} <{agent.email}> wants to send an email that is waiting for your approval.\n\n"
                f"To: {message.recipient}\nSubject: {message.subject}\n\nPreview:\n{preview}\n\n"
                f"Approve (send it): {links['approve']}\n\nReject (discard it): {links['reject']}\n\n"
                "Each link opens a confirmation page and works once. They expire "
                f"{int(lifetime.total_seconds() // 3600)} hours after this email was sent."
            )
            subject = f"[Approval needed] {message.subject}"[:200].replace("\r", " ").replace("\n", " ")
            from_addr = config.from_email or agent.email
            to_addr = agent.review_notify_email
        success, info = await asyncio.to_thread(_send, config, password, from_addr, to_addr, subject, body)
        if not success:
            logger.warning("Review notification for %s was not accepted: %s", review_id, info)
        return bool(success)
    except Exception:
        logger.exception("Review notification for %s failed", review_id)
        return False
