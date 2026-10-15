import datetime
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, RecipientSuppression
from app.schemas import RecipientSuppressionCreate, RecipientSuppressionResponse
from app.security import require_agent_access, require_write
from app.services.webhook_events import enqueue_suppression_added_webhook_event
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(
    prefix="/v1/agents/{email}/suppressions",
    tags=["Recipient Suppressions"],
    dependencies=[Depends(require_agent_access)],
)


@router.get("", response_model=List[RecipientSuppressionResponse])
async def list_suppressions(
    email: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    if not await db.scalar(select(Agent.email).where(Agent.email == email)):
        raise HTTPException(status_code=404, detail="Agent not found")
    result = await db.execute(
        select(RecipientSuppression)
        .where(RecipientSuppression.agent_email == email)
        .order_by(RecipientSuppression.created_at.desc(), RecipientSuppression.id.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/page")
async def list_suppressions_page(email: str, limit: int = Query(50, ge=1, le=200),
                                cursor: str | None = Query(None, max_length=2048), db: AsyncSession = Depends(get_db)):
    if not await db.scalar(select(Agent.email).where(Agent.email == email)):
        raise HTTPException(status_code=404, detail="Agent not found")
    scope = f"/v1/agents/{email}/suppressions"
    query = select(RecipientSuppression).where(RecipientSuppression.agent_email == email).order_by(RecipientSuppression.created_at.desc(), RecipientSuppression.id.desc()).limit(limit + 1)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(RecipientSuppression.created_at < created_at, and_(RecipientSuppression.created_at == created_at, RecipientSuppression.id < item_id)))
    rows = list((await db.execute(query)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [RecipientSuppressionResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}


@router.post("", response_model=RecipientSuppressionResponse, status_code=201, dependencies=[Depends(require_write)])
async def add_suppression(
    email: str,
    request: RecipientSuppressionCreate,
    db: AsyncSession = Depends(get_db),
):
    if not await db.scalar(select(Agent.email).where(Agent.email == email)):
        raise HTTPException(status_code=404, detail="Agent not found")
    entry = await db.scalar(
        select(RecipientSuppression).where(
            RecipientSuppression.agent_email == email,
            RecipientSuppression.email == str(request.email),
        )
    )
    if entry:
        return entry
    entry = RecipientSuppression(
        id=f"sup_{uuid.uuid4().hex[:20]}",
        agent_email=email,
        email=str(request.email),
        reason=request.reason,
    )
    db.add(entry)
    await enqueue_suppression_added_webhook_event(
        db, agent_email=email, recipient=str(request.email), source="operator",
        reason=request.reason, created_at=datetime.datetime.utcnow(),
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        entry = await db.scalar(
            select(RecipientSuppression).where(
                RecipientSuppression.agent_email == email,
                RecipientSuppression.email == str(request.email),
            )
        )
        if entry:
            return entry
        raise
    await db.refresh(entry)
    return entry


@router.delete("/{suppression_id}", status_code=204, dependencies=[Depends(require_write)])
async def remove_suppression(
    email: str,
    suppression_id: str,
    db: AsyncSession = Depends(get_db),
):
    entry = await db.scalar(
        select(RecipientSuppression).where(
            RecipientSuppression.id == suppression_id,
            RecipientSuppression.agent_email == email,
        )
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Suppression not found")
    await db.delete(entry)
    await db.commit()
    return None
