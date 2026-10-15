import datetime
import base64
import binascii
import json
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Message, OutboundJob, OutboundRetryEvent
from app.schemas import OutboundJobPageResponse, OutboundJobResponse, OutboundRetryEventResponse
from app.security import Principal, require_admin
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(prefix="/v1/outbox", tags=["Outbound Queue"], dependencies=[Depends(require_admin)])
OUTBOX_STATUSES = {"pending", "processing", "complete", "failed", "uncertain", "blocked"}


def _encode_outbox_cursor(job: OutboundJob, status: Optional[str]) -> str:
    raw = json.dumps(
        {"v": 1, "created_at": job.created_at.isoformat(), "id": job.id, "status": status},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_outbox_cursor(cursor: str, status: Optional[str]) -> tuple[datetime.datetime, str]:
    if len(cursor) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Outbox cursor is invalid or does not match the active filters"})
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        created_at = datetime.datetime.fromisoformat(payload["created_at"])
        job_id = payload["id"]
        if payload.get("v") != 1 or not isinstance(job_id, str) or not job_id or len(job_id) > 64:
            raise ValueError("invalid cursor fields")
        if payload.get("status") != status or created_at.tzinfo is not None:
            raise ValueError("cursor filters changed")
        return created_at, job_id
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Outbox cursor is invalid or does not match the active filters"}) from None


@router.get("", response_model=List[OutboundJobResponse])
async def list_outbound_jobs(
    status: Optional[str] = Query(None, description="pending, processing, complete, failed, uncertain, or blocked"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(OutboundJob)
    if status:
        if status not in OUTBOX_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid outbox status")
        query = query.where(OutboundJob.status == status)
    result = await db.execute(query.order_by(OutboundJob.created_at.desc()).limit(limit))
    return result.scalars().all()


@router.get("/page", response_model=OutboundJobPageResponse)
async def page_outbound_jobs(
    status: Optional[str] = Query(None, description="pending, processing, complete, failed, uncertain, or blocked"),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    if status and status not in OUTBOX_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid outbox status")
    query = select(OutboundJob)
    if status:
        query = query.where(OutboundJob.status == status)
    if cursor:
        cursor_created_at, cursor_id = _decode_outbox_cursor(cursor, status)
        query = query.where(
            or_(
                OutboundJob.created_at < cursor_created_at,
                and_(OutboundJob.created_at == cursor_created_at, OutboundJob.id < cursor_id),
            )
        )
    result = await db.execute(
        query.order_by(OutboundJob.created_at.desc(), OutboundJob.id.desc()).limit(limit + 1)
    )
    jobs = result.scalars().all()
    has_more = len(jobs) > limit
    items = jobs[:limit]
    next_cursor = _encode_outbox_cursor(items[-1], status) if has_more and items else None
    return {"items": items, "next_cursor": next_cursor}


@router.post("/{job_id}/retry", response_model=OutboundJobResponse)
async def retry_outbound_job(
    job_id: str,
    confirm_duplicate_risk: bool = Query(
        False,
        description="Required when retrying an uncertain job because the SMTP relay may already have accepted it.",
    ),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    retried_at = datetime.datetime.utcnow()
    current = await db.get(OutboundJob, job_id)
    if not current:
        raise HTTPException(status_code=404, detail="Outbound job not found")
    previous_status = current.status
    if previous_status not in {"failed", "uncertain"}:
        raise HTTPException(status_code=409, detail="Only failed or uncertain jobs can be retried")
    if previous_status == "uncertain" and not confirm_duplicate_risk:
        raise HTTPException(
            status_code=409,
            detail="Delivery is unknown; retry may send a duplicate. Retry again with confirm_duplicate_risk=true to proceed.",
        )
    prior_attempts = current.attempts
    prior_error = current.last_error
    result = await db.execute(
        update(OutboundJob)
        .where(OutboundJob.id == job_id, OutboundJob.status == previous_status)
        .values(
            status="pending",
            attempts=0,
            available_at=retried_at,
            locked_at=None,
            last_error=None,
            retried_by=principal.principal_id,
            retried_at=retried_at,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried")

    db.add(OutboundRetryEvent(
        id=str(uuid.uuid4()),
        job_id=job_id,
        actor=principal.principal_id,
        requested_at=retried_at,
        previous_attempts=prior_attempts,
        previous_error=prior_error,
        previous_status=previous_status,
    ))

    job = await db.get(OutboundJob, job_id)
    message = await db.get(Message, job.message_id)
    if message:
        message.status = "sending"
        message.delivery_error = None
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{job_id}/retries", response_model=List[OutboundRetryEventResponse])
async def list_outbound_retry_events(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    if not await db.scalar(select(OutboundJob.id).where(OutboundJob.id == job_id)):
        raise HTTPException(status_code=404, detail="Outbound job not found")
    result = await db.execute(
        select(OutboundRetryEvent)
        .where(OutboundRetryEvent.job_id == job_id)
        .order_by(OutboundRetryEvent.requested_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/{job_id}/retries/page")
async def list_outbound_retry_events_page(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    if not await db.scalar(select(OutboundJob.id).where(OutboundJob.id == job_id)):
        raise HTTPException(status_code=404, detail="Outbound job not found")
    scope = f"/v1/outbox/{job_id}/retries"
    query = select(OutboundRetryEvent).where(OutboundRetryEvent.job_id == job_id)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            requested_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Retry history cursor is invalid"}) from None
        query = query.where(or_(OutboundRetryEvent.requested_at < requested_at,
                                and_(OutboundRetryEvent.requested_at == requested_at, OutboundRetryEvent.id < item_id)))
    rows = list((await db.execute(query.order_by(OutboundRetryEvent.requested_at.desc(), OutboundRetryEvent.id.desc()).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].requested_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [OutboundRetryEventResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}
