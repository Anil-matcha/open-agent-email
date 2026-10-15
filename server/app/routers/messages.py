import base64
import binascii
import json
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update

from app.database import get_db
from app.models import Agent, EmailTemplate, Message, MessageAttachment, OutboundJob, OutboundRetryEvent, ReviewItem, SmtpConfig
from app.schemas import MessageResponse, MessagePageResponse, MessageAttachmentResponse, MessageLabelsUpdate, MessageSendRequest, MessageReplyRequest, MessageForwardRequest, RecentOtpResponse
from app.services.email_service import EmailService
from app.services.attachment_utils import decode_attachment_inputs
from app.services.attachment_utils import ATTACHMENT_URL_TTL_SECONDS, make_attachment_download_url
from app.services.otp_finder import find_recent_otp
from app.security import Principal, get_principal, require_agent_access, require_write
from app.services.message_purge import purge_blocker, purge_message
from app.services.cursors import decode_cursor, encode_cursor
from app.routers.templates import _preview
from pydantic import BaseModel

def _trash_filter(trashed: bool):
    return Message.deleted_at.is_not(None) if trashed else Message.deleted_at.is_(None)


router = APIRouter(prefix="/v1/agents/{email}/messages", tags=["Messages"], dependencies=[Depends(require_agent_access)])
test_router = APIRouter(tags=["Agents"], dependencies=[Depends(require_agent_access)])


class AgentTestSendResult(BaseModel):
    status: str
    message_id: str


@test_router.post("/v1/agents/{email}/test", response_model=AgentTestSendResult, status_code=202,
                 dependencies=[Depends(require_write)], operation_id="test_agent")
async def test_agent_send(email: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Queue a platform-originated message to the agent's own external inbox."""
    agent = await db.scalar(select(Agent).where(Agent.email == email, Agent.deleted_at.is_(None)))
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    smtp_config = await db.get(SmtpConfig, "default")
    platform_sender = (smtp_config.from_email if smtp_config else "").strip() or "noreply@open-agent-email.dev"
    if platform_sender.casefold() == agent.email.casefold():
        platform_sender = "noreply@open-agent-email.dev"
    if platform_sender.casefold() == agent.email.casefold():
        platform_sender = "inbox-test@open-agent-email.dev"
    message = await EmailService.send_outbound(
        db=db,
        agent=agent,
        recipient=agent.email,
        subject="Your inbox test message",
        body_text="This test message was sent by the email platform to verify delivery to your inbox.",
        idempotency_key=request.headers.get("Idempotency-Key"),
        sender=platform_sender,
    )
    return AgentTestSendResult(
        status="pending_review" if message.status == "pending_review" else "accepted",
        message_id=message.id,
    )


async def _outbound_content(email: str, payload, db: AsyncSession, *, reply_subject: str | None = None):
    """Resolve either direct message content or one inbox-scoped saved template."""
    template_ref = payload.template_id or payload.template_alias
    if template_ref:
        if payload.template_id and payload.template_alias:
            raise HTTPException(status_code=422, detail="Provide either template_id or template_alias, not both")
        supplied_content = {"subject", "body_text", "body_html"} & payload.model_fields_set
        if supplied_content:
            raise HTTPException(status_code=422, detail="Provide either template_id or message content, not both")
        template = await db.scalar(
            select(EmailTemplate).where(
                EmailTemplate.agent_email == email,
                (EmailTemplate.id == payload.template_id) if payload.template_id else (EmailTemplate.alias == payload.template_alias),
            )
        )
        if template is None:
            raise HTTPException(status_code=404, detail="Template not found")
        rendered = _preview(template.subject, template.body_text, template.body_html, payload.template_data)
        subject = rendered["subject"]
        if reply_subject is not None and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        return subject, rendered["body_text"], rendered["body_html"] or None

    if "template_data" in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="template_data requires template_id or template_alias")
    if (reply_subject is None and getattr(payload, "subject", None) is None) or payload.body_text is None:
        raise HTTPException(status_code=422, detail="subject and body_text are required without template_id")
    return reply_subject if reply_subject is not None else payload.subject, payload.body_text, payload.body_html


def _encode_message_cursor(
    created_at: datetime,
    message_id: str,
    *,
    inbox: str,
    direction: str | None,
    query: str,
    read_status: str,
    status: str | None,
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "created_at": created_at.isoformat(),
            "id": message_id,
            "inbox": inbox,
            "direction": direction,
            "query": query,
            "read_status": read_status,
            "status": status,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_message_cursor(
    cursor: str,
    *,
    inbox: str,
    direction: str | None,
    query: str,
    read_status: str,
    status: str | None,
) -> tuple[datetime, str]:
    if len(cursor) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Message cursor is invalid or does not match the active filters"})
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.fromisoformat(payload["created_at"])
        message_id = payload["id"]
        if payload.get("v") != 1 or not isinstance(message_id, str) or not message_id or len(message_id) > 64:
            raise ValueError("invalid cursor fields")
        if (payload.get("inbox") != inbox or payload.get("direction") != direction
                or payload.get("query") != query or payload.get("read_status", "all") != read_status):
            raise ValueError("cursor filters changed")
        # Cursors created before status filtering have no status field. Keep them
        # usable for unfiltered requests, while rejecting them for filtered pages.
        if ("status" not in payload and status is not None) or payload.get("status") != status:
            raise ValueError("cursor status filter changed")
        if created_at.tzinfo is not None:
            raise ValueError("cursor timestamp must be naive")
        return created_at, message_id
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Message cursor is invalid or does not match the active filters"}) from None

@router.get("", response_model=List[MessageResponse])
async def list_messages(
    email: str,
    direction: Optional[str] = Query(None, description="inbound or outbound"),
    status: Optional[str] = Query(None, description="delivered, accepted_by_relay, sending, delivery_unknown, pending_review, rejected, blocked, failed"),
    thread_id: Optional[str] = Query(None),
    conversation_id: Optional[str] = Query(None, max_length=255),
    label: Optional[str] = Query(None, max_length=64, description="Only messages carrying this label"),
    trashed: bool = Query(False, description="List trashed messages instead of active ones"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    query = select(Message).where(Message.agent_email == email, _trash_filter(trashed))
    if direction:
        query = query.where(Message.direction == direction)
    if status:
        query = query.where(Message.status == status)
    if thread_id:
        query = query.where(Message.thread_id == thread_id)
    if conversation_id:
        query = query.where(Message.conversation_id == conversation_id)
    
    query = query.order_by(Message.created_at.desc())
    if label:
        # Labels are a small JSON list; filter in Python to stay portable across SQLite/PostgreSQL.
        rows = (await db.execute(query.limit(5000))).scalars().all()
        return [m for m in rows if label in (m.labels or [])][:limit]
    result = await db.execute(query.limit(limit))
    return result.scalars().all()


@router.get("/otp", response_model=RecentOtpResponse, summary="Find a recent inbox verification code")
async def get_recent_otp(
    email: str,
    since_seconds: int = Query(600, ge=1, le=86400, description="Search delivered inbound mail from this many seconds ago"),
    wait_seconds: float = Query(0, ge=0, le=10, description="Wait up to 10 seconds for a matching message"),
    db: AsyncSession = Depends(get_db),
):
    match = await find_recent_otp(
        db,
        email,
        since_seconds=since_seconds,
        wait_seconds=wait_seconds,
    )
    return match or {"found": False}


def _merge_page_token(cursor: Optional[str], page_token: Optional[str]) -> Optional[str]:
    if not isinstance(page_token, str):  # MCP calls the route function directly, leaving the Query default
        return cursor
    if cursor and page_token and cursor != page_token:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "cursor and page_token must match when both are provided"})
    return cursor or page_token


@router.get("/page", response_model=MessagePageResponse)
async def page_messages(
    email: str,
    limit: int = Query(50, ge=1, le=100),
    direction: Optional[str] = Query(None, pattern="^(inbound|outbound)$"),
    read_status: str = Query("all", pattern="^(unread|read|all)$"),
    status: Optional[str] = Query(
        None,
        pattern="^(delivered|accepted_by_relay|sending|delivery_unknown|pending_review|rejected|blocked|failed)$",
        description="Filter by delivery status",
    ),
    q: Optional[str] = Query(None, max_length=200, description="Search sender and subject"),
    cursor: Optional[str] = Query(None, max_length=512),
    page_token: Optional[str] = Query(None, max_length=512, description="Alias for cursor"),
    db: AsyncSession = Depends(get_db),
):
    cursor = _merge_page_token(cursor, page_token)
    query = select(Message).where(Message.agent_email == email, Message.deleted_at.is_(None))
    normalized_query = (q or "").strip()
    if direction:
        query = query.where(Message.direction == direction)
    if status:
        query = query.where(Message.status == status)
    if read_status == "unread":
        query = query.where(Message.is_read.is_(False))
    elif read_status == "read":
        query = query.where(Message.is_read.is_(True))
    if normalized_query:
        search = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{search}%"
        query = query.where(
            or_(Message.sender.ilike(pattern, escape="\\"), Message.subject.ilike(pattern, escape="\\"))
        )
    if cursor:
        cursor_created_at, cursor_id = _decode_message_cursor(
            cursor,
            inbox=email,
            direction=direction,
            query=normalized_query,
            read_status=read_status,
            status=status,
        )
        query = query.where(
            or_(
                Message.created_at < cursor_created_at,
                and_(Message.created_at == cursor_created_at, Message.id < cursor_id),
            )
        )
    query = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1)
    result = await db.execute(query)
    messages = result.scalars().all()
    has_more = len(messages) > limit
    items = messages[:limit]
    next_cursor = _encode_message_cursor(
        items[-1].created_at,
        items[-1].id,
        inbox=email,
        direction=direction,
        query=normalized_query,
        read_status=read_status,
        status=status,
    ) if has_more and items else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("/{message_id}/read", response_model=MessageResponse, dependencies=[Depends(require_write)])
async def mark_message_read(email: str, message_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.agent_email == email,
            Message.direction == "inbound",
        )
    )
    message = result.scalar_one_or_none()
    if not message:
        raise HTTPException(status_code=404, detail="Inbound message not found")
    message.is_read = True
    await db.commit()
    await db.refresh(message)
    return message

@router.get("/{message_id}", response_model=MessageResponse)
async def get_message(email: str, message_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message).where(and_(Message.id == message_id, Message.agent_email == email)))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg

async def _owned_message(db: AsyncSession, email: str, message_id: str) -> Message:
    message = await db.scalar(select(Message).where(Message.id == message_id, Message.agent_email == email))
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@router.patch("/{message_id}", response_model=MessageResponse, dependencies=[Depends(require_write)])
async def update_message_labels(email: str, message_id: str, req: MessageLabelsUpdate, db: AsyncSession = Depends(get_db)):
    """Replace the message's labels with `labels`, or adjust them with `add_labels` and `remove_labels` (free-form triage tags such as `needs-human`)."""
    message = await _owned_message(db, email, message_id)
    if req.labels is not None:
        message.labels = req.labels
    else:
        current = list(message.labels or [])
        for label in req.add_labels or []:
            if label not in current:
                current.append(label)
        removed = set(req.remove_labels or [])
        current = [label for label in current if label not in removed]
        if len(current) > 20:
            raise HTTPException(status_code=422, detail="A message can have at most 20 labels")
        message.labels = current
    await db.commit()
    await db.refresh(message)
    return message


class PurgeResult(BaseModel):
    deleted: bool
    permanent: bool
    message_id: str


@router.delete("/{message_id}", response_model=MessageResponse | PurgeResult, dependencies=[Depends(require_write)])
async def trash_message(
    email: str,
    message_id: str,
    permanent: bool = Query(False, description="Delete for good instead of moving to trash"),
    confirm: Optional[str] = Query(None, description="Must be DELETE when permanent=true"),
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    """Move a message to trash (cancelling an unsubmitted scheduled send), or delete it permanently.

    Permanent deletion needs ``permanent=true&confirm=DELETE`` and is refused while a review is pending or
    an outbound send is queued or in progress.
    """
    message = await _owned_message(db, email, message_id)
    if permanent:
        if confirm != "DELETE":
            raise HTTPException(status_code=422, detail="Permanent deletion requires confirm=DELETE")
        blocker = await purge_blocker(db, message)
        if blocker:
            raise HTTPException(status_code=409, detail=blocker)
        await purge_message(db, message, purged_by=principal.principal_id, reason="manual")
        await db.commit()
        return PurgeResult(deleted=True, permanent=True, message_id=message_id)
    if message.status == "pending_review":
        raise HTTPException(status_code=409, detail="Resolve the pending review before trashing this message")
    if message.status == "scheduled":
        canceled = await db.execute(
            update(OutboundJob)
            .where(OutboundJob.message_id == message.id, OutboundJob.status == "pending")
            .values(status="cancelled", last_error="Canceled before submission")
        )
        if canceled.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Scheduled message is already being submitted")
        message.status = "canceled"
        message.canceled_by_trash = True
    if message.deleted_at is None:
        message.deleted_at = datetime.utcnow()
    await db.commit()
    await db.refresh(message)
    return message


@router.post("/{message_id}/restore", response_model=MessageResponse, dependencies=[Depends(require_write)])
async def restore_message(email: str, message_id: str, db: AsyncSession = Depends(get_db)):
    """Restore a trashed message. A send canceled by trashing is re-armed if its time has not passed."""
    message = await _owned_message(db, email, message_id)
    if message.deleted_at is None:
        raise HTTPException(status_code=409, detail="not_in_trash")
    message.deleted_at = None
    if message.status == "canceled" and message.canceled_by_trash and message.scheduled_for:
        if message.scheduled_for > datetime.utcnow():
            rearmed = await db.execute(
                update(OutboundJob)
                .where(OutboundJob.message_id == message.id, OutboundJob.status == "cancelled")
                .values(status="pending", last_error=None, available_at=message.scheduled_for)
            )
            if rearmed.rowcount == 1:
                message.status = "scheduled"
                message.canceled_by_trash = None
        # Otherwise the time has passed: the send stays canceled rather than firing late.
    await db.commit()
    await db.refresh(message)
    return message


@router.get("/{message_id}/lifecycle")
async def get_message_lifecycle(email: str, message_id: str, db: AsyncSession = Depends(get_db)):
    """Chronological, derived timeline of what happened to a message."""
    message = await _owned_message(db, email, message_id)
    events = [{"type": "created", "at": message.created_at, "detail": f"{message.direction} message recorded"}]
    if message.scheduled_for:
        events.append({"type": "scheduled", "at": message.created_at, "detail": f"Send deferred until {message.scheduled_for.isoformat()}Z"})
    review = await db.scalar(select(ReviewItem).where(ReviewItem.message_id == message.id))
    if review:
        events.append({"type": "review_requested", "at": review.created_at, "detail": review.reason})
        if review.resolved_at:
            events.append({"type": f"review_{review.status}", "at": review.resolved_at,
                           "detail": review.reviewed_by or "", "notes": review.reviewer_notes})
    job = await db.scalar(select(OutboundJob).where(OutboundJob.message_id == message.id))
    if job:
        events.append({"type": "queued", "at": job.created_at, "detail": f"Outbound job {job.status}, {job.attempts} attempt(s)",
                       "last_error": job.last_error})
        for retry in (await db.execute(select(OutboundRetryEvent).where(OutboundRetryEvent.job_id == job.id)
                                       .order_by(OutboundRetryEvent.requested_at))).scalars():
            events.append({"type": "operator_retry", "at": retry.requested_at, "detail": retry.actor})
    if message.provider_delivery_status:
        events.append({"type": f"provider_{message.provider_delivery_status}", "at": None,
                       "detail": message.provider_delivery_error or ""})
    if message.deleted_at:
        events.append({"type": "trashed", "at": message.deleted_at, "detail": ""})
    events.sort(key=lambda e: (e["at"] is None, e["at"] or datetime.min))
    return {"message_id": message.id, "status": message.status, "events": events}


@router.get("/{message_id}/attachments", response_model=List[MessageAttachmentResponse])
async def list_message_attachments(email: str, message_id: str, db: AsyncSession = Depends(get_db)):
    message_result = await db.execute(
        select(Message.id).where(and_(Message.id == message_id, Message.agent_email == email))
    )
    if not message_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Message not found")
    result = await db.execute(
        select(MessageAttachment).where(MessageAttachment.message_id == message_id)
        .order_by(MessageAttachment.created_at, MessageAttachment.id)
    )
    return [
        {**MessageAttachmentResponse.model_validate(item).model_dump(),
         "download_url": make_attachment_download_url(email, message_id, item.id)}
        for item in result.scalars().all()
    ]

ATTACHMENT_INLINE_MAX_BYTES = 256 * 1024


@router.get("/{message_id}/attachments/{index:int}", operation_id="getAttachment")
async def get_message_attachment_by_index(
    email: str,
    message_id: str,
    index: int,
    inline: bool = Query(False, description="Include base64 data for attachments up to 256 KB."),
    db: AsyncSession = Depends(get_db),
):
    """Return metadata and a short-lived URL for an attachment's zero-based index."""
    if index < 0:
        raise HTTPException(status_code=422, detail="Attachment index must be non-negative")
    result = await db.execute(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(Message.id == message_id, Message.agent_email == email)
        .order_by(MessageAttachment.created_at, MessageAttachment.id)
        .offset(index)
        .limit(1)
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    size_bytes = len(attachment.content)
    if inline and size_bytes > ATTACHMENT_INLINE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Inline attachment exceeds {ATTACHMENT_INLINE_MAX_BYTES} byte limit; use download_url",
        )

    now = int(time.time())
    expires = now + ATTACHMENT_URL_TTL_SECONDS
    response = {
        "index": index,
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": size_bytes,
        "download_url": make_attachment_download_url(email, message_id, attachment.id, now=now),
        "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
    }
    if inline:
        response["data"] = base64.b64encode(attachment.content).decode("ascii")
    return response


@router.get("/{message_id}/attachments/{attachment_id}")
async def download_message_attachment(email: str, message_id: str, attachment_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(and_(Message.id == message_id, Message.agent_email == email, MessageAttachment.id == attachment_id))
    )
    attachment = result.scalar_one_or_none()
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    filename = attachment.filename.replace('"', "_").replace("\\", "_").replace("\r", "_").replace("\n", "_")
    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )

@router.post("", response_model=MessageResponse, status_code=201, dependencies=[Depends(require_write)])
async def send_message(email: str, req: MessageSendRequest, request: Request, db: AsyncSession = Depends(get_db)):
    # Verify agent exists
    ag_res = await db.execute(select(Agent).where(Agent.email == email))
    agent = ag_res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    subject, body_text, body_html = await _outbound_content(email, req, db)

    message = await EmailService.send_outbound(
        db=db,
        agent=agent,
        recipient=req.recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        thread_id=req.thread_id,
        conversation_id=req.conversation_id,
        attachments=decode_attachment_inputs([item.model_dump() for item in req.attachments] if req.attachments else None),
        idempotency_key=request.headers.get("Idempotency-Key"),
        send_at=req.scheduled_at,
    )
    return message

@router.post("/{message_id}/reply", response_model=MessageResponse, status_code=201, dependencies=[Depends(require_write)])
async def reply_message(email: str, message_id: str, req: MessageReplyRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if req.reply_all:
        raise HTTPException(status_code=422, detail={"code": "reply_all_unsupported", "message": "Messages have a single recipient, so reply_all is not supported; reply to the sender instead"})
    # Verify agent exists
    ag_res = await db.execute(select(Agent).where(Agent.email == email))
    agent = ag_res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Fetch original message
    m_res = await db.execute(select(Message).where(and_(Message.id == message_id, Message.agent_email == email)))
    original_msg = m_res.scalar_one_or_none()
    if not original_msg:
        raise HTTPException(status_code=404, detail="Target message to reply to was not found")

    reply_recipient = original_msg.sender if original_msg.direction == "inbound" else original_msg.recipient
    subject = original_msg.subject
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    subject, body_text, body_html = await _outbound_content(email, req, db, reply_subject=subject)
    in_reply_to, references = EmailService.reply_headers(original_msg)

    reply_msg = await EmailService.send_outbound(
        db=db,
        agent=agent,
        recipient=reply_recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        thread_id=original_msg.thread_id,
        conversation_id=req.conversation_id if req.conversation_id is not None else original_msg.conversation_id,
        in_reply_to=in_reply_to,
        references=references,
        attachments=decode_attachment_inputs([item.model_dump() for item in req.attachments] if req.attachments else None),
        idempotency_key=request.headers.get("Idempotency-Key"),
        send_at=req.scheduled_at,
    )
    return reply_msg

@router.post("/{message_id}/forward", response_model=MessageResponse, status_code=201, dependencies=[Depends(require_write)])
async def forward_message(email: str, message_id: str, req: MessageForwardRequest, request: Request, db: AsyncSession = Depends(get_db)):
    agent_res = await db.execute(select(Agent).where(Agent.email == email))
    agent = agent_res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    original_res = await db.execute(
        select(Message).where(and_(Message.id == message_id, Message.agent_email == email))
    )
    original = original_res.scalar_one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Message not found")

    try:
        return await EmailService.forward_to_human(
            db=db,
            agent=agent,
            original=original,
            recipient=req.recipient,
            note=req.note,
            body_text=req.body_text,
            body_html=req.body_html,
            attachments=decode_attachment_inputs([item.model_dump() for item in req.attachments] if req.attachments else None),
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/thread/{thread_id}", response_model=List[MessageResponse])
async def get_thread(
    email: str,
    thread_id: str,
    limit: int = Query(500, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Message)
        .where(and_(Message.agent_email == email, Message.thread_id == thread_id))
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/thread/{thread_id}/page")
async def get_thread_page(
    email: str,
    thread_id: str,
    limit: int = Query(100, ge=1, le=200),
    cursor: Optional[str] = Query(None, max_length=2048),
    page_token: Optional[str] = Query(None, max_length=2048, description="Alias for cursor"),
    db: AsyncSession = Depends(get_db),
):
    cursor = _merge_page_token(cursor, page_token)
    scope = f"/v1/agents/{email}/messages/thread/{thread_id}"
    query = select(Message).where(Message.agent_email == email, Message.thread_id == thread_id)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.fromisoformat(key)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Thread cursor is invalid"}) from None
        query = query.where(or_(Message.created_at > created_at,
                                and_(Message.created_at == created_at, Message.id > item_id)))
    rows = list((await db.execute(query.order_by(Message.created_at.asc(), Message.id.asc()).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [MessageResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}
