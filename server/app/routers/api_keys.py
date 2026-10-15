import uuid
import secrets
import hashlib
import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.database import AsyncSessionLocal, get_db
from app.models import ApiKey, Agent
from app.schemas import (
    ApiKeyCreate,
    AccountApiKeyCreate,
    AccountApiKeyResponse,
    AccountApiKeyCreatedResponse,
    AccountApiKeyPageResponse,
    ApiKeyResponse,
    ApiKeyCreatedResponse,
    ApiKeyPageResponse,
    ApiKeyDeleteResponse,
)
from app.security import Principal, require_admin
from app.services.cursors import decode_cursor, encode_cursor
from app.services.idempotency import (
    reserve_idempotency, complete_idempotency, release_idempotency_claim,
    ensure_idempotency_response_encryption,
)

router = APIRouter(prefix="/v1/api-keys", tags=["API Keys"], dependencies=[Depends(require_admin)])
account_router = APIRouter(
    prefix="/v1/account/api-keys",
    tags=["Account", "API Keys"],
    dependencies=[Depends(require_admin)],
)

@router.get("", response_model=List[ApiKeyResponse], dependencies=[Depends(require_admin)])
async def list_api_keys(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return result.scalars().all()


@router.get("/page")
async def list_api_keys_page(limit: int = Query(50, ge=1, le=200), cursor: str | None = Query(None, max_length=2048),
                             db: AsyncSession = Depends(get_db), *, scope: str = "/v1/api-keys"):
    query = select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(ApiKey.created_at < created_at, and_(ApiKey.created_at == created_at, ApiKey.id < item_id)))
    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [ApiKeyResponse.model_validate(row) for row in rows], "next_cursor": next_cursor}

@router.post("", response_model=ApiKeyCreatedResponse, status_code=201, dependencies=[Depends(require_admin)])
async def create_api_key(payload: ApiKeyCreate, request: Request,
                         principal: Principal = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)):
    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        try:
            ensure_idempotency_response_encryption()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:api-keys:create", key, await request.body())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 201,
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

    agent = await db.execute(select(Agent).where(Agent.email == payload.agent_email))
    if agent.scalar_one_or_none() is None:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=404, detail="Agent not found")
    token_secret = secrets.token_urlsafe(32)
    prefix = f"oae_{token_secret[:8]}"
    full_key = f"{prefix}_{token_secret[8:]}"
    key_hash = hashlib.sha256(full_key.encode("utf-8")).hexdigest()

    key_id = f"key_{uuid.uuid4().hex[:12]}"
    api_key = ApiKey(
        id=key_id,
        name=payload.name,
        prefix=prefix,
        key_hash=key_hash,
        scope=payload.scope,
        agent_email=payload.agent_email,
        created_at=datetime.datetime.utcnow(),
        expires_at=(
            datetime.datetime.utcnow() + datetime.timedelta(days=payload.expires_in_days)
            if payload.expires_in_days is not None else None
        ),
    )
    db.add(api_key)
    response_body = jsonable_encoder(ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key=full_key,
        prefix=api_key.prefix,
        scope=api_key.scope,
        agent_email=api_key.agent_email,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    ))
    if decision is not None:
        await complete_idempotency(db, decision, status=201, body=response_body, encrypt_response=True)
    await db.commit()
    await db.refresh(api_key)

    if decision is not None:
        return JSONResponse(status_code=201, content=response_body)
    return ApiKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key=full_key,
        prefix=api_key.prefix,
        scope=api_key.scope,
        agent_email=api_key.agent_email,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )

@router.delete("/{key_id}", status_code=204, dependencies=[Depends(require_admin)])
async def revoke_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")

    await db.delete(api_key)
    await db.commit()
    return None


# Canonical account-prefixed compatibility routes. Keep the existing route behavior
# centralized so both URL families share authorization, validation and key lifecycle.
@account_router.get("", response_model=AccountApiKeyPageResponse)
async def list_account_api_keys(
    limit: int = Query(100, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
) -> AccountApiKeyPageResponse:
    query = select(ApiKey).where(ApiKey.scope.in_(("account", "agent"))).order_by(
        ApiKey.created_at.desc(), ApiKey.id.desc())
    if cursor:
        key, item_id = decode_cursor(cursor, scope="/v1/account/api-keys")
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope="/v1/account/api-keys")
        query = query.where(or_(ApiKey.created_at < created_at,
                                and_(ApiKey.created_at == created_at, ApiKey.id < item_id)))
    rows = list((await db.execute(query.limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope="/v1/account/api-keys", key=rows[-1].created_at.isoformat(),
                                item_id=rows[-1].id) if has_more and rows else None
    return AccountApiKeyPageResponse(items=[AccountApiKeyResponse.from_key(row) for row in rows],
                                     next_cursor=next_cursor)


@account_router.post("", response_model=AccountApiKeyCreatedResponse, status_code=201)
async def create_account_api_key(
    payload: AccountApiKeyCreate,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    agent_email = payload.agent_email.strip().lower() if payload.agent_email else None
    if payload.scope == "agent":
        agent = await db.scalar(select(Agent).where(Agent.email == agent_email))
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")

    decision = None
    idem_key = request.headers.get("Idempotency-Key")
    if idem_key is not None:
        try:
            ensure_idempotency_response_encryption()
            decision = await reserve_idempotency(
                f"{principal.principal_id}:account-api-keys:create", idem_key, await request.body())
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decision.outcome == "replay":
            return JSONResponse(status_code=decision.response_status or 201,
                                content=decision.response_body, headers=decision.response_headers or {})
        if decision.outcome != "reserved":
            messages = {
                "conflict": "Idempotency-Key was already used with a different request",
                "in_progress": "Idempotency-Key request is still in progress; retry unchanged",
                "expired_in_progress": "Idempotency-Key request did not finish; operator recovery is required",
            }
            raise HTTPException(status_code=422 if decision.outcome == "conflict" else 409,
                                detail=messages[decision.outcome])

    token_secret = secrets.token_urlsafe(32)
    prefix = f"oae_{token_secret[:8]}"
    full_key = f"{prefix}_{token_secret[8:]}"
    now = datetime.datetime.utcnow()
    api_key = ApiKey(
        id=f"key_{uuid.uuid4().hex[:12]}", name=payload.name, prefix=prefix,
        key_hash=hashlib.sha256(full_key.encode("utf-8")).hexdigest(), scope=payload.scope,
        agent_email=agent_email, created_at=now,
        expires_at=now + datetime.timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None else None,
    )
    db.add(api_key)
    response_body = jsonable_encoder(AccountApiKeyCreatedResponse(
        id=api_key.id, name=api_key.name, key=full_key, key_prefix=api_key.prefix,
        scope=api_key.scope, agent_email=api_key.agent_email, created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    ))
    if decision is not None:
        await complete_idempotency(db, decision, status=201, body=response_body, encrypt_response=True)
    await db.commit()
    if decision is not None:
        return JSONResponse(status_code=201, content=response_body)
    return AccountApiKeyCreatedResponse(**response_body)


@account_router.delete("/{id}", status_code=200, response_model=ApiKeyDeleteResponse)
async def revoke_account_api_key(
    id: str,
    confirm: str = Query(..., pattern="^DELETE$", description="Must be the literal DELETE."),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(select(ApiKey).where(
        ApiKey.id == id, ApiKey.scope.in_(("account", "agent"))))
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": id}
