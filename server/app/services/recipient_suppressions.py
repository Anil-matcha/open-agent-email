"""Inbox scoped outbound recipient suppression checks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from email.utils import getaddresses
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.models import RecipientSuppression


def normalize_recipient(email: str) -> str:
    return email.strip().lower()


def canonicalize_recipient(value: str) -> str:
    """Parse exactly one mailbox and return its normalized address only."""
    if not isinstance(value, str) or not value.strip() or len(value) > 320:
        raise ValueError("Enter exactly one valid recipient email address")
    try:
        try:
            parsed = getaddresses([value], strict=True)
        except TypeError:  # Python versions before strict parsing was added.
            parsed = getaddresses([value])
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter exactly one valid recipient email address") from exc
    if len(parsed) != 1 or not parsed[0][1].strip():
        raise ValueError("Enter exactly one valid recipient email address")
    mailbox = parsed[0][1].strip()
    try:
        validated = TypeAdapter(EmailStr).validate_python(mailbox)
    except ValidationError as exc:
        raise ValueError("Enter exactly one valid recipient email address") from exc
    normalized = str(validated).lower()
    if len(normalized) > 255:
        raise ValueError("Recipient email address must be at most 255 characters")
    return normalized


async def get_recipient_suppression(
    db: AsyncSession, agent_email: str, recipient: str
) -> RecipientSuppression | None:
    normalized = normalize_recipient(recipient)
    result = await db.execute(
        select(RecipientSuppression).where(
            RecipientSuppression.agent_email == agent_email,
            RecipientSuppression.email == normalized,
        )
    )
    return result.scalar_one_or_none()
