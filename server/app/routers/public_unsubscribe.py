"""Public unsubscribe page and RFC one-click endpoint."""

import datetime
import hashlib
import html
import re
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RecipientSuppression, UnsubscribeToken
from app.services.webhook_events import enqueue_suppression_added_webhook_event

router = APIRouter(tags=["Public unsubscribe"])
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_BODY_BYTES = 128
SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _page(title: str, content: str, status_code: int = 200) -> HTMLResponse:
    body = (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><main style=\"max-width:36rem;margin:12vh auto;padding:1.5rem;font:16px system-ui\">"
        f"<h1>{html.escape(title)}</h1>{content}</main></html>"
    )
    return HTMLResponse(body, status_code=status_code, headers=SECURITY_HEADERS)


async def _lookup(token: str, db: AsyncSession) -> UnsubscribeToken | None:
    if len(token) != 43 or not TOKEN_RE.fullmatch(token):
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    return await db.scalar(select(UnsubscribeToken).where(UnsubscribeToken.token_hash == digest))


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe_confirmation(token: str, db: AsyncSession = Depends(get_db)):
    capability = await _lookup(token, db)
    if not capability:
        return _page("Unsubscribe link unavailable", "<p>This link is invalid or expired.</p>", 404)
    content = (
        "<p>Confirm that you want to stop future emails to this address.</p>"
        "<form method=\"post\" action=\"\">"
        "<button type=\"submit\" name=\"confirm\" value=\"unsubscribe\">Unsubscribe</button></form>"
    )
    # Deliberately read-only: mail scanners that fetch links cannot opt out.
    return _page("Confirm unsubscribe", content)


@router.post("/unsubscribe/{token}")
async def unsubscribe_submit(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    if not TOKEN_RE.fullmatch(token) or len(token) != 43:
        return _page("Unsubscribe link unavailable", "<p>This link is invalid or expired.</p>", 404)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                return _page("Unsubscribe link unavailable", "<p>Request could not be processed.</p>", 413)
        except ValueError:
            return _page("Unsubscribe link unavailable", "<p>Request could not be processed.</p>", 400)
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BODY_BYTES:
            return _page("Unsubscribe link unavailable", "<p>Request could not be processed.</p>", 413)
    raw = bytes(chunks)
    if len(raw) > MAX_BODY_BYTES:
        return _page("Unsubscribe link unavailable", "<p>Request could not be processed.</p>", 413)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        fields = parse_qs(raw.decode("ascii"), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        fields = {}
    is_one_click = content_type == "application/x-www-form-urlencoded" and fields == {"List-Unsubscribe": ["One-Click"]}
    is_human_confirm = content_type == "application/x-www-form-urlencoded" and fields == {"confirm": ["unsubscribe"]}
    if not (is_one_click or is_human_confirm):
        return _page("Unsubscribe confirmation required", "<p>Use the confirmation button on the unsubscribe page.</p>", 400)

    capability = await _lookup(token, db)
    if not capability:
        return _page("Unsubscribe link unavailable", "<p>This link is invalid or expired.</p>", 404)

    existing = await db.scalar(select(RecipientSuppression).where(
        RecipientSuppression.agent_email == capability.agent_email,
        RecipientSuppression.email == capability.recipient,
    ))
    if not existing:
        db.add(RecipientSuppression(
            id=f"sup_{uuid.uuid4().hex[:20]}",
            agent_email=capability.agent_email,
            email=capability.recipient,
            reason="Recipient unsubscribed",
        ))
        await enqueue_suppression_added_webhook_event(
            db, agent_email=capability.agent_email, recipient=capability.recipient,
            source="unsubscribe", reason="Recipient unsubscribed", created_at=datetime.datetime.utcnow(),
        )
    if capability.used_at is None:
        capability.used_at = datetime.datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Concurrent submissions are idempotent; the unique suppression is the source of truth.
    if is_one_click:
        return Response(status_code=204, headers=SECURITY_HEADERS)
    return _page("Unsubscribed", "<p>This address will not receive future mail from this inbox.</p>")
