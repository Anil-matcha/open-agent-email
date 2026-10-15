import datetime
import uuid
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, Contact, ContactEngagement, RecipientSuppression
from app.schemas import ContactEngagementResponse, ContactEngagementWrite, ContactEngagementUpdate
from app.security import require_agent_access, require_write
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(
    prefix="/v1/agents/{email}/engagements",
    tags=["Contact Engagements"],
    dependencies=[Depends(require_agent_access)],
)


def _utc(value: datetime.datetime | None) -> datetime.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value


async def _get_agent(email: str, db: AsyncSession) -> None:
    if not await db.scalar(select(Agent.email).where(Agent.email == email)):
        raise HTTPException(status_code=404, detail="Agent inbox not found")


async def _response(row, db: AsyncSession) -> ContactEngagementResponse:
    engagement, contact = row
    suppressed = await db.scalar(select(RecipientSuppression.id).where(
        RecipientSuppression.agent_email == engagement.agent_email,
        RecipientSuppression.email == contact.email,
    )) is not None
    return ContactEngagementResponse(
        id=engagement.id, agent_email=engagement.agent_email,
        contact_id=contact.id, contact_email=contact.email, contact_name=contact.name,
        stage=engagement.stage, next_due_at=_utc(engagement.next_due_at), notes=engagement.notes,
        replied_at=_utc(engagement.replied_at), replied=engagement.replied_at is not None, suppressed=suppressed,
        created_at=_utc(engagement.created_at), updated_at=_utc(engagement.updated_at),
    )


async def _is_suppressed(email: str, contact_email: str, db: AsyncSession) -> bool:
    return await db.scalar(select(RecipientSuppression.id).where(
        RecipientSuppression.agent_email == email,
        RecipientSuppression.email == contact_email,
    )) is not None


@router.get("", response_model=list[ContactEngagementResponse])
async def list_engagements(
    email: str,
    due: bool = Query(False, description="Only currently due, unsuppressed follow-ups"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    await _get_agent(email, db)
    query = select(ContactEngagement, Contact).join(
        Contact, and_(Contact.id == ContactEngagement.contact_id, Contact.agent_email == email)
    ).where(ContactEngagement.agent_email == email)
    if due:
        now = datetime.datetime.utcnow()
        query = query.where(
            ContactEngagement.next_due_at.is_not(None),
            ContactEngagement.next_due_at <= now,
            ContactEngagement.replied_at.is_(None),
            ContactEngagement.stage != "closed",
            ~select(RecipientSuppression.id).where(
                RecipientSuppression.agent_email == email,
                RecipientSuppression.email == Contact.email,
            ).exists(),
        )
    query = query.order_by(ContactEngagement.next_due_at.asc().nulls_last(), ContactEngagement.updated_at.desc()).limit(limit)
    return [await _response(row, db) for row in (await db.execute(query)).all()]


@router.get("/page")
async def list_engagements_page(
    email: str,
    due: bool = Query(False, description="Only currently due, unsuppressed follow-ups"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    await _get_agent(email, db)
    scope = f"/v1/agents/{email}/engagements?due={str(due).lower()}"
    query = select(ContactEngagement, Contact).join(
        Contact, and_(Contact.id == ContactEngagement.contact_id, Contact.agent_email == email)
    ).where(ContactEngagement.agent_email == email)
    if due:
        now = datetime.datetime.utcnow()
        query = query.where(
            ContactEngagement.next_due_at.is_not(None), ContactEngagement.next_due_at <= now,
            ContactEngagement.replied_at.is_(None), ContactEngagement.stage != "closed",
            ~select(RecipientSuppression.id).where(
                RecipientSuppression.agent_email == email,
                RecipientSuppression.email == Contact.email,
            ).exists(),
        )
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            decoded = json.loads(key)
            due_at = datetime.datetime.fromisoformat(decoded[0]) if decoded[0] is not None else None
            updated_at = datetime.datetime.fromisoformat(decoded[1])
        except (ValueError, TypeError, IndexError, json.JSONDecodeError):
            decode_cursor("invalid", scope=scope)
        if due_at is None:
            query = query.where(ContactEngagement.next_due_at.is_(None), or_(
                ContactEngagement.updated_at < updated_at,
                and_(ContactEngagement.updated_at == updated_at, ContactEngagement.id > item_id)))
        else:
            query = query.where(or_(
                ContactEngagement.next_due_at > due_at,
                and_(ContactEngagement.next_due_at == due_at, ContactEngagement.updated_at < updated_at),
                and_(ContactEngagement.next_due_at == due_at, ContactEngagement.updated_at == updated_at,
                     ContactEngagement.id > item_id),
                ContactEngagement.next_due_at.is_(None),
            ))
    ordering = (ContactEngagement.next_due_at.asc().nulls_last(), ContactEngagement.updated_at.desc(), ContactEngagement.id)
    rows = list((await db.execute(query.order_by(*ordering).limit(limit + 1))).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        engagement = rows[-1][0]
        key = json.dumps([engagement.next_due_at.isoformat() if engagement.next_due_at else None,
                          engagement.updated_at.isoformat()])
        next_cursor = encode_cursor(scope=scope, key=key, item_id=engagement.id)
    return {"items": [await _response(row, db) for row in rows], "next_cursor": next_cursor}


@router.get("/{engagement_id}", response_model=ContactEngagementResponse)
async def get_engagement(email: str, engagement_id: str, db: AsyncSession = Depends(get_db)):
    """Return an engagement enriched with contact and suppression state."""
    result = await db.execute(select(ContactEngagement, Contact).join(
        Contact, and_(Contact.id == ContactEngagement.contact_id, Contact.agent_email == email)
    ).where(ContactEngagement.id == engagement_id, ContactEngagement.agent_email == email))
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return await _response(row, db)


@router.post("", response_model=ContactEngagementResponse, status_code=201, dependencies=[Depends(require_write)])
async def create_engagement(email: str, request: ContactEngagementWrite, db: AsyncSession = Depends(get_db)):
    await _get_agent(email, db)
    contact = await db.scalar(select(Contact).where(Contact.agent_email == email, Contact.id == request.contact_id))
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found in this inbox")
    if request.next_due_at and await _is_suppressed(email, contact.email, db):
        raise HTTPException(status_code=422, detail="Cannot schedule follow-up for a suppressed recipient")
    existing = await db.scalar(select(ContactEngagement).where(
        ContactEngagement.agent_email == email, ContactEngagement.contact_id == contact.id,
    ))
    if existing:
        raise HTTPException(status_code=409, detail="An engagement already exists for this contact")
    item = ContactEngagement(
        id=f"eng_{uuid.uuid4().hex[:20]}", agent_email=email, contact_id=contact.id,
        stage=request.stage, next_due_at=request.next_due_at, notes=request.notes,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return await _response((item, contact), db)


@router.patch("/{engagement_id}", response_model=ContactEngagementResponse, dependencies=[Depends(require_write)])
async def update_engagement(email: str, engagement_id: str, request: ContactEngagementUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ContactEngagement, Contact).join(Contact).where(
        ContactEngagement.id == engagement_id,
        ContactEngagement.agent_email == email,
        Contact.agent_email == email,
    ))
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Engagement not found")
    item, contact = row
    previous_next_due_at = item.next_due_at
    changes = request.model_dump(exclude_unset=True)
    next_due = changes.get("next_due_at", item.next_due_at)
    stage = changes.get("stage", item.stage)
    if next_due is not None and stage != "closed" and item.replied_at is None and await _is_suppressed(email, contact.email, db):
        raise HTTPException(status_code=422, detail="Cannot schedule follow-up for a suppressed recipient")
    for field in ("stage", "next_due_at", "notes"):
        if field in changes:
            setattr(item, field, changes[field])
    if "next_due_at" in changes and changes["next_due_at"] != previous_next_due_at:
        # A changed schedule is a new wake-up opportunity, including when an
        # operator later restores the same timestamp that was notified before.
        item.due_notified_at = None
    if item.replied_at is not None or item.stage == "closed":
        item.next_due_at = None
    await db.commit()
    await db.refresh(item)
    return await _response((item, contact), db)


@router.delete("/{engagement_id}", status_code=204, dependencies=[Depends(require_write)])
async def delete_engagement(email: str, engagement_id: str, db: AsyncSession = Depends(get_db)):
    item = await db.scalar(select(ContactEngagement).where(
        ContactEngagement.id == engagement_id,
        ContactEngagement.agent_email == email,
    ))
    if not item:
        raise HTTPException(status_code=404, detail="Engagement not found")
    await db.delete(item)
    await db.commit()
