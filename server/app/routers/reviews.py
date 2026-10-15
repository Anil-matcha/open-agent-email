import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal, get_db
from app.models import ReviewItem
from app.schemas import ReviewItemResponse, ReviewDecisionRequest
from app.services.hitl_service import HitlService
from app.security import Principal, require_reviewer
from app.services.cursors import decode_cursor, encode_cursor
from app.services.idempotency import reserve_idempotency, complete_idempotency, release_idempotency_claim
from sqlalchemy import and_, or_

router = APIRouter(prefix="/v1/reviews", tags=["HITL Reviews"])

@router.get("", response_model=List[ReviewItemResponse])
async def list_reviews(
    status: Optional[str] = Query(
        None,
        description="Filter by review status",
        pattern="^(pending|approved|rejected)$",
    ),
    agent_email: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db)
):
    query = select(ReviewItem).options(selectinload(ReviewItem.message))
    if status:
        query = query.where(ReviewItem.status == status)
    if agent_email:
        query = query.where(ReviewItem.agent_email == agent_email)
    if not principal.is_admin:
        query = query.where(ReviewItem.agent_email == principal.agent_email)
    
    query = query.order_by(ReviewItem.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/page")
async def list_reviews_page(
    status: Optional[str] = Query(None, pattern="^(pending|approved|rejected)$"),
    agent_email: Optional[str] = Query(None), limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, max_length=2048),
    principal: Principal = Depends(require_reviewer), db: AsyncSession = Depends(get_db),
):
    scope = f"/v1/reviews|status={status or ''}|agent_email={agent_email or ''}|principal={principal.agent_email or 'admin'}"
    query = select(ReviewItem).options(selectinload(ReviewItem.message))
    if status:
        query = query.where(ReviewItem.status == status)
    if agent_email:
        query = query.where(ReviewItem.agent_email == agent_email)
    if not principal.is_admin:
        query = query.where(ReviewItem.agent_email == principal.agent_email)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(ReviewItem.created_at < created_at, and_(ReviewItem.created_at == created_at, ReviewItem.id < item_id)))
    rows = list((await db.execute(query.order_by(ReviewItem.created_at.desc(), ReviewItem.id.desc()).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [ReviewItemResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}

@router.get("/{review_id}", response_model=ReviewItemResponse)
async def get_review(review_id: str, principal: Principal = Depends(require_reviewer), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ReviewItem)
        .options(selectinload(ReviewItem.message))
        .where(ReviewItem.id == review_id)
    )
    rev = result.scalar_one_or_none()
    if not rev:
        raise HTTPException(status_code=404, detail="Review item not found")
    if not principal.is_admin and rev.agent_email != principal.agent_email:
        raise HTTPException(status_code=404, detail="Review item not found")
    return rev

@router.post("/{review_id}/approve", response_model=ReviewItemResponse)
async def approve_review(
    review_id: str,
    request: Request,
    body: Optional[ReviewDecisionRequest] = None,
    principal: Principal = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db)
):
    notes = body.notes if body else "Approved by human reviewer"
    review_agent = await db.scalar(select(ReviewItem.agent_email).where(ReviewItem.id == review_id))
    if not review_agent or (not principal.is_admin and review_agent != principal.agent_email):
        raise HTTPException(status_code=404, detail="Review item not found")

    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        raw_body = await request.body()
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:reviews:approve", key,
                {"review_id": review_id, "body": raw_body.decode("latin1")})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 200,
                                content=decision.response_body,
                                headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            messages = {
                "conflict": "Idempotency-Key was already used with a different request",
                "in_progress": "Idempotency-Key request is still in progress; retry unchanged",
                "expired_in_progress": "Idempotency-Key request did not finish; operator recovery is required",
            }
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409,
                                detail=messages[decision.outcome])

    try:
        review = await HitlService.resolve_review(
            db=db,
            review_id=review_id,
            action="approve",
            notes=notes,
            reviewer_id=principal.principal_id,
            commit=decision is None,
            publish=decision is None,
        )
    except HTTPException:
        if decision is not None:
            await db.rollback()
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise
    # Reload with message
    res = await db.execute(
        select(ReviewItem).options(selectinload(ReviewItem.message)).where(ReviewItem.id == review_id)
    )
    review = res.scalar_one()
    if decision is not None:
        response_body = jsonable_encoder(ReviewItemResponse.model_validate(review))
        await complete_idempotency(db, decision, status=200, body=response_body)
        await db.commit()
        await HitlService.publish_resolution(review, review.message, "approve", notes)
        return JSONResponse(status_code=200, content=response_body)
    return review

@router.post("/{review_id}/reject", response_model=ReviewItemResponse)
async def reject_review(
    review_id: str,
    body: Optional[ReviewDecisionRequest] = None,
    principal: Principal = Depends(require_reviewer),
    db: AsyncSession = Depends(get_db)
):
    notes = body.notes if body else "Rejected by human reviewer"
    if not principal.is_admin:
        review_agent = await db.scalar(select(ReviewItem.agent_email).where(ReviewItem.id == review_id))
        if review_agent != principal.agent_email:
            raise HTTPException(status_code=404, detail="Review item not found")
    review = await HitlService.resolve_review(
        db=db,
        review_id=review_id,
        action="reject",
        notes=notes,
        reviewer_id=principal.principal_id,
    )
    # Reload with message
    res = await db.execute(
        select(ReviewItem).options(selectinload(ReviewItem.message)).where(ReviewItem.id == review_id)
    )
    return res.scalar_one()
