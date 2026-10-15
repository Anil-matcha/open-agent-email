"""Bounded extraction of contextual verification codes from recent inbox mail."""

import asyncio
import re
import time
from datetime import datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message

MAX_MESSAGES = 50
MAX_SCAN_CHARS_PER_MESSAGE = 32 * 1024
POLL_INTERVAL_SECONDS = 0.5

_CONTEXT = r"(?:verification|verify|security|one[ -]?time|otp|auth(?:entication)?|login|confirmation|passcode|access|sign[ -]?in|code)"
_CODE_PATTERNS = (
    re.compile(rf"\b{_CONTEXT}\b[^\d\n]{{0,32}}(\d{{4,8}})\b", re.IGNORECASE),
    re.compile(rf"\b(\d{{4,8}})\b[^\d\n]{{0,32}}\b{_CONTEXT}\b", re.IGNORECASE),
)


def extract_otp(subject: str | None, body_text: str | None) -> str | None:
    """Return a code only when a nearby verification-related phrase supports it."""
    text = f"{(subject or '')[:1000]}\n{(body_text or '')[:MAX_SCAN_CHARS_PER_MESSAGE]}"
    matches = []
    for pattern in _CODE_PATTERNS:
        matches.extend((match.start(), match.group(1)) for match in pattern.finditer(text))
    return min(matches, key=lambda item: item[0])[1] if matches else None


async def find_recent_otp(
    db: AsyncSession,
    agent_email: str,
    *,
    since_seconds: int = 600,
    wait_seconds: float = 0,
) -> dict | None:
    """Search only recent, delivered inbound mail for an inbox, with bounded polling."""
    deadline = time.monotonic() + wait_seconds
    while True:
        cutoff = datetime.utcnow() - timedelta(seconds=since_seconds)
        result = await db.execute(
            select(Message)
            .where(and_(
                Message.agent_email == agent_email,
                Message.direction == "inbound",
                Message.status == "delivered",
                Message.created_at >= cutoff,
            ))
            .order_by(Message.created_at.desc())
            .limit(MAX_MESSAGES)
        )
        for message in result.scalars():
            code = extract_otp(message.subject, message.body_text)
            if code:
                return {
                    "found": True,
                    "code": code,
                    "message_id": message.id,
                    "received_at": message.created_at,
                    "sender": message.sender,
                    "subject": message.subject,
                }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        # End the read transaction so the next poll can observe newly committed mail.
        await db.rollback()
        await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
