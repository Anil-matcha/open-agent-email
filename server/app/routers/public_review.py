"""Public approve/reject pages for emailed review links (read-only GET, explicit POST)."""

import datetime
import hashlib
import html
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, Message, ReviewItem, ReviewLinkToken
from app.routers.public_unsubscribe import _page
from app.services.hitl_service import HitlService

router = APIRouter(tags=["Public review links"])
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_BODY_BYTES = 128
PREVIEW_CHARS = 2000
UNAVAILABLE = ("Review link unavailable", "<p>This link is invalid or has expired.</p>")


def _unavailable(status_code: int = 404) -> HTMLResponse:
    return _page(*UNAVAILABLE, status_code)


def _digest(token: str) -> str | None:
    if len(token) != 43 or not TOKEN_RE.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def _load(token: str, db: AsyncSession):
    digest = _digest(token)
    if digest is None:
        return None
    capability = await db.scalar(select(ReviewLinkToken).where(ReviewLinkToken.token_hash == digest))
    if capability is None or capability.expires_at <= datetime.datetime.utcnow():
        return None
    review = await db.get(ReviewItem, capability.review_id)
    message = await db.get(Message, review.message_id) if review else None
    agent = await db.scalar(select(Agent).where(Agent.email == review.agent_email)) if review else None
    if not review or not message or not agent or agent.deleted_at is not None:
        return None
    return capability, review, message


@router.get("/review-link/{token}", response_class=HTMLResponse)
async def review_confirmation(token: str, db: AsyncSession = Depends(get_db)):
    loaded = await _load(token, db)
    if loaded is None:
        return _unavailable()
    capability, review, message = loaded
    if capability.used_at is not None:
        return _page("Link already used", "<p>This link has already been used.</p>", 410)
    if review.status != "pending":
        return _page("Already decided", f"<p>This message was already {html.escape(review.status)}.</p>", 409)
    verb = "Approve and send" if capability.action == "approve" else "Reject"
    preview = " ".join(message.body_text.split())[:PREVIEW_CHARS]
    content = (
        f"<p><strong>From:</strong> {html.escape(message.sender)}<br>"
        f"<strong>To:</strong> {html.escape(message.recipient)}<br>"
        f"<strong>Subject:</strong> {html.escape(message.subject)}</p>"
        f"<pre style=\"white-space:pre-wrap;background:#f4f4f5;padding:1rem;border-radius:.5rem\">{html.escape(preview)}</pre>"
        "<form method=\"post\" action=\"\">"
        f"<button type=\"submit\" name=\"confirm\" value=\"{html.escape(capability.action)}\">{verb}</button></form>"
    )
    # Deliberately read-only: mail scanners that prefetch links cannot decide a review.
    return _page("Review held message", content)


@router.post("/review-link/{token}", response_class=HTMLResponse)
async def review_submit(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    digest = _digest(token)
    if digest is None:
        return _unavailable()
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            return _page("Review link unavailable", "<p>Request could not be processed.</p>", 413)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        fields = parse_qs(bytes(raw).decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        fields = {}

    loaded = await _load(token, db)
    if loaded is None:
        return _unavailable()
    capability, review, message = loaded
    if content_type != "application/x-www-form-urlencoded" or fields != {"confirm": [capability.action]}:
        return _page("Confirmation required", "<p>Use the confirmation button on the review page.</p>", 400)

    now = datetime.datetime.utcnow()
    # Single use: exactly one request can claim the token; the claim and the decision commit together.
    claimed = await db.execute(
        update(ReviewLinkToken)
        .where(ReviewLinkToken.token_hash == digest, ReviewLinkToken.used_at.is_(None),
               ReviewLinkToken.expires_at > now)
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        return _page("Link already used", "<p>This link has already been used.</p>", 410)
    try:
        await HitlService.resolve_review(
            db, review.id, capability.action,
            notes=f"{'Approved' if capability.action == 'approve' else 'Rejected'} via email link",
            reviewer_id=f"magic_link:{capability.reviewer_email}",
        )
    except HTTPException as exc:
        await db.rollback()
        if exc.status_code == 409:
            return _page("Already decided", "<p>This message was already decided.</p>", 409)
        return _unavailable()
    done = "approved and queued for sending" if capability.action == "approve" else "rejected and will not be sent"
    return _page("Done", f"<p>The message was {done}.</p>")
