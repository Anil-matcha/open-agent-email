"""Administrator-managed, inbox-scoped webhook endpoints and delivery history."""

import base64
import binascii
import datetime
import json
import secrets
import uuid
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_db
from app.models import Agent, WebhookDelivery, WebhookEndpoint
from app.security import Principal, require_admin
from app.services.smtp_secrets import encrypt_secret
from app.services.idempotency import (
    reserve_idempotency, complete_idempotency, release_idempotency_claim,
    ensure_idempotency_response_encryption,
)
from app.services.webhook_delivery import validate_webhook_url
from app.services.webhook_events import build_payload
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"], dependencies=[Depends(require_admin)])
MAX_ENDPOINTS_PER_INBOX = 50
DELIVERY_STATUSES = {"pending", "processing", "delivered", "failed"}
WEBHOOK_EVENT_TYPES = {
    "email.received", "email.review_requested", "email.review_approved",
    "email.review_rejected", "email.flagged", "email.blocked",
    "email.accepted_by_relay", "email.failed", "email.delivery_unknown",
    "email.delivered", "email.bounced", "email.complained", "email.delivery_delayed",
    "email.sent", "domain.sending_verified", "domain.sending_failed", "domain.suppression_added",
    "agent.suppression_added",
    "contact.due",
}


def _validate_event_types(value: list[str]) -> list[str]:
    if not value:
        raise ValueError("event_types must contain at least one event")
    if len(value) > 32:
        raise ValueError("event_types may contain at most 32 values")
    if len(set(value)) != len(value):
        raise ValueError("event_types values must be unique")
    if "*" in value and value != ["*"]:
        raise ValueError("'*' must be the sole event_types value")
    invalid = set(value) - WEBHOOK_EVENT_TYPES - {"*"}
    if invalid:
        raise ValueError(f"Unsupported webhook event type: {sorted(invalid)[0]}")
    return value


def _encode_delivery_cursor(
    item: WebhookDelivery, endpoint_id: Optional[str], status: Optional[str], event_type: Optional[str],
    agent_email: Optional[str] = None, since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
) -> str:
    payload = {
        "created_at": item.created_at.isoformat(),
        "id": item.id,
        "endpoint_id": endpoint_id,
        "status": status,
        "event_type": event_type,
        "agent_email": agent_email,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_delivery_cursor(
    cursor: str, endpoint_id: Optional[str], status: Optional[str], event_type: Optional[str],
    agent_email: Optional[str] = None, since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        old_keys = {"created_at", "id", "endpoint_id", "status"}
        prior_keys = old_keys | {"event_type"}
        new_keys = prior_keys | {"agent_email"}
        date_keys = new_keys | {"since", "until"}
        if not isinstance(payload, dict) or set(payload) not in (old_keys, prior_keys, new_keys, date_keys):
            raise ValueError
        cursor_event_type = payload.get("event_type")
        if cursor_event_type != event_type or payload.get("agent_email") != agent_email:
            raise ValueError("filter mismatch")
        if payload["endpoint_id"] != endpoint_id or payload["status"] != status:
            raise ValueError("filter mismatch")
        if payload.get("since") != (since.isoformat() if since else None):
            raise ValueError("filter mismatch")
        if payload.get("until") != (until.isoformat() if until else None):
            raise ValueError("filter mismatch")
        if not isinstance(payload["id"], str) or not payload["id"] or len(payload["id"]) > 64:
            raise ValueError
        if payload["endpoint_id"] is not None and not isinstance(payload["endpoint_id"], str):
            raise ValueError
        if payload["status"] is not None and not isinstance(payload["status"], str):
            raise ValueError
        if cursor_event_type is not None and (
            not isinstance(cursor_event_type, str) or not cursor_event_type or len(cursor_event_type) > 64
        ):
            raise ValueError
        created_at = datetime.datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is not None:
            raise ValueError
        return created_at, payload["id"]
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_cursor", "message": "Delivery cursor is invalid or does not match the active filters"}) from exc


class WebhookCreate(BaseModel):
    agent_email: str = Field(min_length=3, max_length=255)
    url: str = Field(min_length=9, max_length=2048)
    enabled: bool = True
    events: list[str] = Field(default_factory=lambda: ["*"], max_length=32)

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: list[str]) -> list[str]:
        return _validate_event_types(value)


class WebhookUpdate(BaseModel):
    url: Optional[str] = Field(None, min_length=9, max_length=2048)
    enabled: Optional[bool] = None
    events: Optional[list[str]] = Field(None, max_length=32)

    @field_validator("events")
    @classmethod
    def validate_events(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        return _validate_event_types(value) if value is not None else value


class WebhookResponse(BaseModel):
    id: str
    agent_email: str
    url: str
    enabled: bool
    events: list[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime


class WebhookCreated(WebhookResponse):
    signing_secret: str


def _endpoint_response(endpoint: WebhookEndpoint) -> dict:
    parsed = urlsplit(endpoint.url)
    display_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "[redacted]" if parsed.query else "", ""))
    return {"id": endpoint.id, "agent_email": endpoint.agent_email, "url": display_url,
            "enabled": endpoint.enabled, "events": endpoint.event_types or ["*"],
            "created_at": endpoint.created_at, "updated_at": endpoint.updated_at}


@router.post("", response_model=WebhookCreated, status_code=201)
async def create_webhook(payload: WebhookCreate, request: Request,
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
                f"{principal.principal_id}:webhooks:create", key, await request.body())
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

    agent_result = await db.execute(select(Agent).where(
        Agent.email == payload.agent_email.strip().lower(),
    ).with_for_update())
    agent = agent_result.scalar_one_or_none()
    if not agent:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=404, detail="Agent inbox not found")
    endpoint_count = await db.scalar(select(func.count()).select_from(WebhookEndpoint).where(
        WebhookEndpoint.agent_email == agent.email,
    ))
    if endpoint_count >= MAX_ENDPOINTS_PER_INBOX:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=409, detail=f"An inbox can have at most {MAX_ENDPOINTS_PER_INBOX} webhook endpoints")
    try:
        url = validate_webhook_url(payload.url)
        raw_secret = "whsec_" + secrets.token_urlsafe(32)
        encrypted = encrypt_secret(raw_secret, "webhook signing secret")
    except ValueError as exc:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    now = datetime.datetime.utcnow()
    endpoint = WebhookEndpoint(id=f"wh_{uuid.uuid4().hex}", agent_email=agent.email, url=url,
                               signing_secret=encrypted, enabled=payload.enabled,
                               event_types=payload.events, created_at=now, updated_at=now)
    db.add(endpoint)
    response_body = jsonable_encoder({**_endpoint_response(endpoint), "signing_secret": raw_secret})
    if decision is not None:
        await complete_idempotency(db, decision, status=201, body=response_body, encrypt_response=True)
    await db.commit()
    await db.refresh(endpoint)
    if decision is not None:
        return JSONResponse(status_code=201, content=response_body)
    return {**_endpoint_response(endpoint), "signing_secret": raw_secret}


@router.get("")
async def list_webhooks(agent_email: Optional[str] = Query(None, max_length=255),
                        limit: int = Query(100, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    query = select(WebhookEndpoint)
    if agent_email:
        query = query.where(WebhookEndpoint.agent_email == agent_email.strip().lower())
    endpoints = (await db.execute(query.order_by(WebhookEndpoint.created_at.desc()).limit(limit))).scalars().all()
    return [_endpoint_response(item) for item in endpoints]


@router.get("/page")
async def list_webhooks_page(agent_email: Optional[str] = Query(None, max_length=255),
                             limit: int = Query(50, ge=1, le=200),
                             cursor: Optional[str] = Query(None, max_length=2048),
                             db: AsyncSession = Depends(get_db)):
    normalized_email = agent_email.strip().lower() if agent_email else None
    scope = f"/v1/webhooks|agent_email={normalized_email or ''}"
    query = select(WebhookEndpoint)
    if normalized_email:
        query = query.where(WebhookEndpoint.agent_email == normalized_email)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(WebhookEndpoint.created_at < created_at,
                                and_(WebhookEndpoint.created_at == created_at, WebhookEndpoint.id < item_id)))
    rows = list((await db.execute(query.order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
                                   .limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [_endpoint_response(item) for item in rows], "next_cursor": next_cursor}


@router.patch("/{endpoint_id}", response_model=WebhookResponse)
async def update_webhook(endpoint_id: str, payload: WebhookUpdate, db: AsyncSession = Depends(get_db)):
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    if payload.url is not None:
        try:
            endpoint.url = validate_webhook_url(payload.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.enabled is not None:
        endpoint.enabled = payload.enabled
    if payload.events is not None:
        endpoint.event_types = payload.events
    endpoint.updated_at = datetime.datetime.utcnow()
    await db.commit()
    await db.refresh(endpoint)
    return _endpoint_response(endpoint)


@router.post("/{endpoint_id}/rotate-secret", response_model=WebhookCreated)
async def rotate_webhook_secret(endpoint_id: str, request: Request,
                                principal: Principal = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    """Rotate a signing secret and return its plaintext exactly once.

    The currently active secret remains valid for 24 hours. A repeated rotation
    replaces any still-active overlap window with the immediately prior secret.
    """
    key = request.headers.get("Idempotency-Key")
    decision = None
    if key is not None:
        try:
            ensure_idempotency_response_encryption()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            decision = await reserve_idempotency(
                f"{principal.principal_id}:webhooks:rotate-secret", key,
                {"endpoint_id": endpoint_id, "body": (await request.body()).decode("latin1")})
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

    result = await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id).with_for_update()
    )
    endpoint = result.scalar_one_or_none()
    if not endpoint:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")

    try:
        raw_secret = "whsec_" + secrets.token_urlsafe(32)
        encrypted = encrypt_secret(raw_secret, "webhook signing secret")
    except RuntimeError as exc:
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    now = datetime.datetime.utcnow()
    previous_expires_at = now + datetime.timedelta(hours=24)
    result = await db.execute(update(WebhookEndpoint).where(
        WebhookEndpoint.id == endpoint.id,
        WebhookEndpoint.signing_secret == endpoint.signing_secret,
    ).values(
        previous_signing_secret=endpoint.signing_secret,
        previous_secret_expires_at=previous_expires_at,
        signing_secret=encrypted,
        updated_at=now,
    ))
    if result.rowcount != 1:
        await db.rollback()
        if decision is not None:
            await release_idempotency_claim(AsyncSessionLocal, decision)
        raise HTTPException(status_code=409, detail="Webhook secret changed; retry rotation")
    await db.refresh(endpoint)
    response_body = jsonable_encoder({**_endpoint_response(endpoint), "signing_secret": raw_secret})
    if decision is not None:
        await complete_idempotency(db, decision, status=200, body=response_body, encrypt_response=True)
    await db.commit()
    if decision is not None:
        return JSONResponse(status_code=200, content=response_body)
    return {**_endpoint_response(endpoint), "signing_secret": raw_secret}


@router.post("/{endpoint_id}/test", status_code=202)
async def send_test_webhook(endpoint_id: str, db: AsyncSession = Depends(get_db)):
    """Queue a signed synthetic event to verify an enabled endpoint."""
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    if not endpoint.enabled:
        raise HTTPException(status_code=409, detail="Enable the webhook endpoint before sending a test event")

    now = datetime.datetime.utcnow()
    delivery_id = f"evt_{uuid.uuid4().hex}"
    db.add(WebhookDelivery(
        id=delivery_id,
        endpoint_id=endpoint.id,
        agent_email=endpoint.agent_email,
        event_type="webhook.test",
        payload=build_payload("webhook.test", {
            "id": delivery_id,
            "event_type": "webhook.test",
            "test": True,
            "created_at": now.isoformat(),
            "message": "Synthetic test event; no email was sent or received.",
        }),
        status="pending",
        attempts=0,
        available_at=now,
        created_at=now,
    ))
    await db.commit()
    return {"delivery_id": delivery_id, "status": "pending"}


@router.delete("/{endpoint_id}", status_code=204)
async def delete_webhook(endpoint_id: str, db: AsyncSession = Depends(get_db)):
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    await db.execute(delete(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id))
    await db.delete(endpoint)
    await db.commit()


@router.get("/deliveries")
async def list_deliveries(endpoint_id: Optional[str] = Query(None, max_length=64),
                          status: Optional[str] = Query(None),
                          event_type: Optional[str] = Query(None, min_length=1, max_length=64),
                          agent_email: Optional[str] = Query(None, max_length=255),
                          limit: int = Query(100, ge=1, le=200),
                          db: AsyncSession = Depends(get_db)):
    if status and status not in {"pending", "processing", "delivered", "failed"}:
        raise HTTPException(status_code=400, detail="Invalid webhook delivery status")
    query = select(WebhookDelivery)
    if endpoint_id:
        query = query.where(WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        query = query.where(WebhookDelivery.status == status)
    if event_type is not None:
        query = query.where(WebhookDelivery.event_type == event_type)
    if agent_email:
        query = query.where(WebhookDelivery.agent_email == agent_email.strip().lower())
    query = query.where(WebhookDelivery.status != "expired")
    items = (await db.execute(query.order_by(WebhookDelivery.created_at.desc()).limit(limit))).scalars().all()
    return [{"id": item.id, "endpoint_id": item.endpoint_id, "agent_email": item.agent_email,
             "event_type": item.event_type, "payload": item.payload, "status": item.status,
             "attempts": item.attempts, "last_error": item.last_error, "created_at": item.created_at,
             "delivered_at": item.delivered_at, "replayed_by": item.replayed_by,
             "replayed_at": item.replayed_at, "replay_count": item.replay_count} for item in items]


@router.get("/deliveries/page")
async def page_deliveries(endpoint_id: Optional[str] = Query(None, max_length=64),
                          status: Optional[str] = Query(None),
                          event_type: Optional[str] = Query(None, min_length=1, max_length=64),
                          agent_email: Optional[str] = Query(None, max_length=255),
                          since: Optional[datetime.datetime] = Query(None, description="Include events at or after this offset-qualified timestamp"),
                          until: Optional[datetime.datetime] = Query(None, description="Include events at or before this offset-qualified timestamp"),
                          limit: int = Query(50, ge=1, le=100),
                          cursor: Optional[str] = Query(None, max_length=2048),
                          db: AsyncSession = Depends(get_db)):
    if status and status not in DELIVERY_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid webhook delivery status")

    def utc_naive(value: datetime.datetime | None, name: str) -> datetime.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise HTTPException(status_code=422, detail=f"{name} must include a timezone offset")
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    since = utc_naive(since, "since")
    until = utc_naive(until, "until")
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="since must be earlier than or equal to until")

    normalized_agent_email = agent_email.strip().lower() if agent_email else None
    query = select(WebhookDelivery).where(WebhookDelivery.status != "expired")
    if endpoint_id:
        query = query.where(WebhookDelivery.endpoint_id == endpoint_id)
    if status:
        query = query.where(WebhookDelivery.status == status)
    if event_type is not None:
        query = query.where(WebhookDelivery.event_type == event_type)
    if normalized_agent_email:
        query = query.where(WebhookDelivery.agent_email == normalized_agent_email)
    if since:
        query = query.where(WebhookDelivery.created_at >= since)
    if until:
        query = query.where(WebhookDelivery.created_at <= until)
    if cursor is not None:
        created_at, delivery_id = _decode_delivery_cursor(
            cursor, endpoint_id, status, event_type, normalized_agent_email, since, until,
        )
        query = query.where(or_(
            WebhookDelivery.created_at < created_at,
            and_(WebhookDelivery.created_at == created_at, WebhookDelivery.id < delivery_id),
        ))

    rows = (await db.execute(query.order_by(
        WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc(),
    ).limit(limit + 1))).scalars().all()
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_delivery_cursor(
        items[-1], endpoint_id, status, event_type, normalized_agent_email, since, until,
    ) if has_more and items else None
    return {
        "items": [{"id": item.id, "endpoint_id": item.endpoint_id, "agent_email": item.agent_email,
                   "event_type": item.event_type, "payload": item.payload, "status": item.status,
                   "attempts": item.attempts, "last_error": item.last_error, "created_at": item.created_at,
                   "delivered_at": item.delivered_at, "replayed_by": item.replayed_by,
                   "replayed_at": item.replayed_at, "replay_count": item.replay_count} for item in items],
        "next_cursor": next_cursor,
    }


@router.post("/deliveries/{delivery_id}/replay")
async def replay_delivery(delivery_id: str, principal: Principal = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    now = datetime.datetime.utcnow()
    delivery = await db.get(WebhookDelivery, delivery_id)
    if not delivery:
        raise HTTPException(status_code=404, detail="Webhook delivery not found")
    if delivery.status == "expired":
        raise HTTPException(status_code=410, detail="Webhook event has expired")
    if delivery.status not in {"failed", "delivered"}:
        raise HTTPException(status_code=409, detail="Only failed or previously delivered events can be replayed")
    endpoint = await db.get(WebhookEndpoint, delivery.endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=409, detail="Webhook endpoint no longer exists")
    if not endpoint.enabled:
        raise HTTPException(status_code=409, detail="Enable the webhook endpoint before replaying this event")
    result = await db.execute(update(WebhookDelivery).where(
        WebhookDelivery.id == delivery_id, WebhookDelivery.status == delivery.status,
    ).values(status="pending", attempts=0, available_at=now, locked_at=None, lease_token=None,
             last_error=None, delivered_at=None, replayed_by=principal.principal_id,
             replayed_at=now, replay_count=WebhookDelivery.replay_count + 1))
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="Webhook delivery changed; retry")
    await db.commit()
    return {"id": delivery_id, "status": "pending", "replayed_at": now}


# Declared after the static /deliveries routes so "/deliveries" is never captured as an id.
@router.get("/{endpoint_id}")
async def get_webhook(endpoint_id: str, db: AsyncSession = Depends(get_db)):
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return _endpoint_response(endpoint)


@router.get("/{endpoint_id}/deliveries")
async def list_webhook_deliveries(endpoint_id: str, status: Optional[str] = Query(None),
                                  limit: int = Query(100, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    if not await db.get(WebhookEndpoint, endpoint_id):
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return await list_deliveries(endpoint_id=endpoint_id, status=status, event_type=None, agent_email=None, limit=limit, db=db)
