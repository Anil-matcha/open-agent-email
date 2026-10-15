"""Conversation (RFC thread) views derived from stored messages."""

import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Message
from app.schemas import MessageResponse
from app.security import require_agent_access
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(
    prefix="/v1/agents/{email}/conversations",
    tags=["Conversations"],
    dependencies=[Depends(require_agent_access)],
)


def _summary(thread_id: str, messages: List[Message]) -> dict:
    messages = sorted(messages, key=lambda m: m.created_at)
    participants = []
    for m in messages:
        for address in (m.sender, m.recipient):
            if address not in participants:
                participants.append(address)
    return {
        "id": thread_id,
        "subject": messages[0].subject,
        "message_count": len(messages),
        "unread_count": sum(1 for m in messages if m.direction == "inbound" and not m.is_read),
        "participants": participants,
        "labels": sorted({label for m in messages for label in (m.labels or [])}),
        "last_message_at": messages[-1].created_at,
        "last_message_id": messages[-1].id,
    }


@router.get("")
async def list_conversations(email: str, limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    """Most recently active threads first; trashed messages are excluded."""
    latest = (
        select(Message.thread_id, func.max(Message.created_at).label("latest"))
        .where(Message.agent_email == email, Message.deleted_at.is_(None))
        .group_by(Message.thread_id)
        .order_by(func.max(Message.created_at).desc())
        .limit(limit)
        .subquery()
    )
    rows = (await db.execute(
        select(Message).join(latest, Message.thread_id == latest.c.thread_id)
        .where(Message.agent_email == email, Message.deleted_at.is_(None))
    )).scalars().all()
    grouped: dict[str, List[Message]] = {}
    for message in rows:
        grouped.setdefault(message.thread_id, []).append(message)
    items = [_summary(tid, msgs) for tid, msgs in grouped.items()]
    items.sort(key=lambda item: item["last_message_at"], reverse=True)
    return {"items": items}


@router.get("/page")
async def list_conversations_page(
    email: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    scope = f"/v1/agents/{email}/conversations"
    latest = (select(Message.thread_id.label("thread_id"), func.max(Message.created_at).label("latest"))
              .where(Message.agent_email == email, Message.deleted_at.is_(None))
              .group_by(Message.thread_id).subquery())
    query = select(latest.c.thread_id, latest.c.latest)
    if cursor:
        key, thread_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(latest.c.latest < created_at,
                                and_(latest.c.latest == created_at, latest.c.thread_id < thread_id)))
    page_rows = list((await db.execute(query.order_by(latest.c.latest.desc(), latest.c.thread_id.desc())
                                        .limit(limit + 1))).all())
    has_more = len(page_rows) > limit
    page_rows = page_rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=page_rows[-1].latest.isoformat(),
                                item_id=page_rows[-1].thread_id) if has_more and page_rows else None
    thread_ids = [row.thread_id for row in page_rows]
    messages = (await db.execute(select(Message).where(
        Message.agent_email == email, Message.thread_id.in_(thread_ids), Message.deleted_at.is_(None)
    ))).scalars().all() if thread_ids else []
    grouped: dict[str, List[Message]] = {}
    for message in messages:
        grouped.setdefault(message.thread_id, []).append(message)
    items = [_summary(thread_id, grouped[thread_id]) for thread_id in thread_ids]
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{conversation_id}")
async def get_conversation(email: str, conversation_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Message).where(
            Message.agent_email == email, Message.thread_id == conversation_id, Message.deleted_at.is_(None)
        ).order_by(Message.created_at)
    )).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {**_summary(conversation_id, rows),
            "messages": [MessageResponse.model_validate(m).model_dump(mode="json") for m in rows]}
