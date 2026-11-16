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
    if (not isinstance(value, str) or not value.strip() or len(value) > 320
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
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


MAX_RECIPIENTS = 50  # Amazon SES accepts at most 50 recipients per message.


def canonicalize_recipient_lists(to, cc=None, bcc=None) -> tuple[list[str], list[str], list[str]]:
    """Canonicalize To/Cc/Bcc, dropping case-insensitive duplicates (to wins over cc over bcc)."""
    seen: set[str] = set()
    lists = []
    for field, values in (("to", to), ("cc", cc), ("bcc", bcc)):
        if values is None:
            values = []
        elif isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"{field} must be an email address or a list of email addresses")
        kept = []
        for value in values:
            try:
                address = canonicalize_recipient(value)
            except ValueError as exc:
                raise ValueError(str(exc) if field == "to" else f"{field}: {exc}") from exc
            if address not in seen:
                seen.add(address)
                kept.append(address)
        lists.append(kept)
    if not lists[0]:
        raise ValueError("At least one To recipient is required")
    if len(seen) > MAX_RECIPIENTS:
        raise ValueError(f"A message can have at most {MAX_RECIPIENTS} recipients across to, cc and bcc")
    return lists[0], lists[1], lists[2]


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
