"""Administrator views across every inbox: suppressions, contacts and a data export.

Contacts and suppressions stay stored per inbox (the existing tables); these routes are
read/write views over those rows, not separate records.
"""

import datetime
import uuid
from typing import Optional, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (Agent, Contact, EmailTemplate, Message, RecipientSuppression,
                        ReviewItem, WebhookDelivery, WebhookEndpoint)
from app.routers.contacts import _ensure_unique_email
from app.routers.webhooks import _endpoint_response
from app.schemas import ContactCreate, ContactResponse, ContactUpdate
from app.security import require_admin
from app.services.cursors import decode_cursor, encode_cursor

router = APIRouter(tags=["Account"], dependencies=[Depends(require_admin)])

EXPORT_DEFAULT_LIMIT = 5000
EXPORT_MAX_LIMIT = 20000
METRICS_DEFAULT_WINDOW = datetime.timedelta(days=30)
METRICS_MAX_WINDOW = datetime.timedelta(days=92)
METRICS_MAX_AGENTS = 200
METRICS_MAX_ENDPOINTS = 50

_SUMMARY_FIELDS = (
    "accepted", "submitted", "delivered", "bounced_hard", "bounced_soft",
    "bounced_undetermined", "complained", "suppressed", "loopback", "send_failed",
    "received", "dmarc_pass", "dmarc_fail", "dmarc_none", "dmarc_error",
    "review_held", "review_approved", "review_rejected", "review_expired_approved",
    "review_expired_rejected",
)

_REASON_META = {
    "acceptance.outbound_api": ("accepted", "accepted", False),
    "acceptance.inbound_smtp": ("accepted", "accepted", False),
    "authentication.dmarc_pass": ("authentication", "passed", False),
    "authentication.dmarc_fail": ("authentication", "failed", False),
    "authentication.dmarc_none": ("authentication", "indeterminate", False),
    "authentication.dmarc_temporary_error": ("authentication", "indeterminate", True),
    "authentication.dmarc_permanent_error": ("authentication", "indeterminate", False),
    "review.hold_created": ("review", "pending", False),
    "review.approved": ("review", "approved", False),
    "review.rejected": ("review", "rejected", False),
    "suppression.recipient_blocked": ("suppression", "blocked", False),
    "submission.upstream_accepted": ("submission", "accepted", False),
    "submission.local_loopback_accepted": ("submission", "accepted", False),
    "submission.provider_rejected": ("submission", "failed", False),
    "delivery.recipient_server_accepted": ("delivery", "delivered", False),
    "delivery.permanent_bounce": ("delivery", "bounced", False),
    "delivery.transient_bounce": ("delivery", "bounced", True),
    "delivery.undetermined_bounce": ("delivery", "bounced", False),
    "complaint.recipient_reported": ("complaint", "reported", False),
}


def _zero_summary() -> dict:
    return {name: 0 for name in _SUMMARY_FIELDS}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metrics_rates(summary: dict) -> dict:
    external_accepted = summary["accepted"] - summary["loopback"]
    external_submitted = summary["submitted"] - summary["loopback"]
    bounced = summary["bounced_hard"] + summary["bounced_soft"] + summary["bounced_undetermined"]
    return {
        "delivered_rate": _ratio(summary["delivered"], external_accepted),
        "bounce_rate": _ratio(bounced, external_submitted),
        "complaint_rate": _ratio(summary["complained"], summary["delivered"]),
        "suppression_block_rate": _ratio(summary["suppressed"], external_accepted),
    }


def _blank_metrics() -> dict:
    return {
        "messages_in_window": 0,
        "messages_with_lifecycle": 0,
        "reconstructed_observations": 0,
        "summary": _zero_summary(),
        "_counter_rows": {},
        "_counter_messages": {},
    }


def _observe(metrics: dict, message_id: str, code: str, *, count=1) -> None:
    stage, outcome, retryable = _REASON_META[code]
    row = metrics["_counter_rows"].setdefault(code, {
        "reason_code": code, "stage": stage, "outcome": outcome, "retryable": retryable,
        "observations": 0, "messages": 0,
    })
    row["observations"] += count
    metrics["_counter_messages"].setdefault(code, set()).add(message_id)


def _finalize_metrics(metrics: dict) -> dict:
    counters = metrics.pop("_counter_rows")
    message_sets = metrics.pop("_counter_messages")
    for code, row in counters.items():
        row["messages"] = len(message_sets[code])
    return {**metrics, "rates": _metrics_rates(metrics["summary"]),
            "counters": [counters[key] for key in sorted(counters)]}


def _utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _inferred_observations(message: Message, review: ReviewItem | None) -> list[str]:
    """Reconstruct the lifecycle facts available from this deployment's durable message rows."""
    codes = ["acceptance.outbound_api" if message.direction == "outbound" else "acceptance.inbound_smtp"]
    if message.direction == "inbound":
        dmarc = str(message.dmarc_status or "").lower()
        if dmarc in {"pass", "fail", "none"}:
            codes.append(f"authentication.dmarc_{dmarc}")
        elif dmarc in {"temperror", "temporary_error"}:
            codes.append("authentication.dmarc_temporary_error")
        elif dmarc in {"permerror", "permanent_error"}:
            codes.append("authentication.dmarc_permanent_error")
    if review:
        codes.append("review.hold_created")
        if review.status == "approved":
            codes.append("review.approved")
        elif review.status == "rejected":
            codes.append("review.rejected")
    if message.direction == "outbound":
        if message.status == "accepted_by_relay":
            codes.append("submission.upstream_accepted")
        elif message.status == "delivered":
            codes.append("submission.local_loopback_accepted")
        elif message.status == "failed":
            codes.append("submission.provider_rejected")
        elif message.status == "blocked":
            codes.append("suppression.recipient_blocked")
        provider = str(message.provider_delivery_status or "").lower()
        provider_code = {
            "delivered": "delivery.recipient_server_accepted",
            "bounced": "delivery.undetermined_bounce",
            "complained": "complaint.recipient_reported",
        }.get(provider)
        if provider_code:
            codes.append(provider_code)
    return codes


@router.get("/v1/metrics")
async def get_account_metrics(
    start: datetime.datetime | None = Query(None, description="Inclusive RFC 3339 cohort-window start; defaults to 30 days before end."),
    end: datetime.datetime | None = Query(None, description="Exclusive RFC 3339 cohort-window end; defaults to now."),
    bucket: Literal["day"] | None = Query(None),
    group_by: Literal["agent"] | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate account-owned message lifecycle facts over a half-open cohort window."""
    window_end = _utc(end or datetime.datetime.now(datetime.timezone.utc))
    window_start = _utc(start) if start else window_end - METRICS_DEFAULT_WINDOW
    if window_end <= window_start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if window_end - window_start > METRICS_MAX_WINDOW:
        raise HTTPException(status_code=400, detail="window must not exceed 92 days; request a narrower range")

    # SQLite stores UTC timestamps without an offset; normalize the query bounds
    # to the same representation while preserving UTC in the response.
    db_start = window_start.replace(tzinfo=None)
    db_end = window_end.replace(tzinfo=None)
    owned_emails = list((await db.execute(
        select(Agent.email).where(Agent.deleted_at.is_(None)).order_by(Agent.email)
    )).scalars().all())
    messages = list((await db.execute(
        select(Message).join(Agent, Agent.email == Message.agent_email).where(
            Agent.deleted_at.is_(None), Message.created_at >= db_start, Message.created_at < db_end,
        ).order_by(Message.created_at, Message.id)
    )).scalars().all())
    message_ids = [item.id for item in messages]
    reviews_by_message = {}
    if message_ids:
        reviews = (await db.execute(select(ReviewItem).where(ReviewItem.message_id.in_(message_ids)))).scalars().all()
        reviews_by_message = {item.message_id: item for item in reviews}

    totals = _blank_metrics()
    per_agent: dict[str, dict] = {}
    by_day: dict[datetime.date, dict] = {}
    for item in messages:
        agent_email = item.agent_email
        agent_metrics = per_agent.setdefault(agent_email, _blank_metrics())
        created_day = _utc(item.created_at).date()
        daily = by_day.setdefault(created_day, _blank_metrics())
        review = reviews_by_message.get(item.id)
        observations = _inferred_observations(item, review)
        for aggregate in (totals, agent_metrics, daily):
            aggregate["messages_in_window"] += 1
            # This deployment has no persisted transition ledger. Keep coverage
            # honest and mark state-derived facts as reconstructed.
            for reason_code in observations:
                aggregate["reconstructed_observations"] += 1
                _observe(aggregate, item.id, reason_code)
        summary_sets = (totals["summary"], agent_metrics["summary"], daily["summary"])
        for summary in summary_sets:
            if item.direction == "outbound":
                summary["accepted"] += 1
            else:
                summary["received"] += 1
            dmarc_status = str(item.dmarc_status or "").lower()
            if item.direction == "inbound":
                if dmarc_status == "pass":
                    summary["dmarc_pass"] += 1
                elif dmarc_status == "fail":
                    summary["dmarc_fail"] += 1
                elif dmarc_status == "none":
                    summary["dmarc_none"] += 1
                elif dmarc_status in {"temperror", "temporary_error", "permerror", "permanent_error"}:
                    summary["dmarc_error"] += 1
            if review:
                summary["review_held"] += 1
                if review.status == "approved":
                    summary["review_approved"] += 1
                elif review.status == "rejected":
                    summary["review_rejected"] += 1
            if item.direction != "outbound":
                continue
            status = str(item.status or "").lower()
            if status == "accepted_by_relay":
                summary["submitted"] += 1
            elif status == "delivered":
                summary["submitted"] += 1
                summary["loopback"] += 1
            elif status == "failed":
                summary["send_failed"] += 1
            elif status == "blocked":
                summary["suppressed"] += 1
            provider_status = str(item.provider_delivery_status or "").lower()
            if provider_status == "delivered":
                summary["delivered"] += 1
            elif provider_status == "complained":
                summary["complained"] += 1
            elif provider_status == "bounced":
                summary["bounced_undetermined"] += 1

    totals = _finalize_metrics(totals)
    agents = []
    if group_by == "agent":
        ordered_agents = sorted(per_agent.items(), key=lambda pair: (-pair[1]["messages_in_window"], pair[0]))
        agents = [{"agent_email": email, **_finalize_metrics(metrics)}
                  for email, metrics in ordered_agents[:METRICS_MAX_AGENTS]]

    buckets = []
    if bucket == "day":
        day = window_start.date()
        while day < window_end.date() or (day == window_end.date() and window_end.time() != datetime.time.min):
            daily = _finalize_metrics(by_day.get(day, _blank_metrics()))
            buckets.append({
                "day": datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.timezone.utc),
                "summary": daily["summary"], "rates": daily["rates"],
            })
            day += datetime.timedelta(days=1)

    # The persisted webhook table uses a smaller outcome vocabulary than the
    # metrics contract. Failed terminal deliveries are reported separately
    # from pending rows; endpoint detail only exposes a sanitized host.
    endpoint_rows = list((await db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.agent_email.in_(owned_emails))
    )).scalars().all()) if owned_emails else []
    deliveries = list((await db.execute(
        select(WebhookDelivery).where(
            WebhookDelivery.agent_email.in_(owned_emails),
            WebhookDelivery.status != "expired",
            WebhookDelivery.created_at >= db_start, WebhookDelivery.created_at < db_end,
        ).order_by(WebhookDelivery.created_at, WebhookDelivery.id)
    )).scalars().all()) if owned_emails else []
    endpoint_by_id = {item.id: item for item in endpoint_rows}
    delivery_counts: dict[str, dict] = {}
    for delivery in deliveries:
        counts = delivery_counts.setdefault(delivery.endpoint_id, {
            "deliveries": 0, "delivered": 0, "pending": 0,
            "endpoint_rejected": 0, "no_response": 0, "last_status_code": None,
        })
        counts["deliveries"] += 1
        if delivery.status == "delivered":
            counts["delivered"] += 1
        elif delivery.status in {"pending", "processing"}:
            counts["pending"] += 1
        elif delivery.status == "failed":
            error = str(delivery.last_error or "")
            if error.startswith("http_status_") and error[12:].isdigit():
                counts["endpoint_rejected"] += 1
                counts["last_status_code"] = int(error[12:])
            else:
                counts["no_response"] += 1
    endpoint_metrics = []
    for endpoint_id, counts in sorted(delivery_counts.items(), key=lambda pair: (-pair[1]["deliveries"], pair[0])):
        endpoint = endpoint_by_id.get(endpoint_id)
        if endpoint is None:
            continue
        endpoint_metrics.append({
            "webhook_id": endpoint.id,
            "url_host": urlsplit(endpoint.url).hostname or "",
            "deliveries": counts["deliveries"],
            "delivered": counts["delivered"],
            "pending": counts["pending"],
            "endpoint_rejected": counts["endpoint_rejected"],
            "no_response": counts["no_response"],
            "success_rate": _ratio(counts["delivered"], counts["delivered"] + counts["endpoint_rejected"] + counts["no_response"]),
            "last_status_code": counts["last_status_code"],
            "enabled": endpoint.enabled,
            "auto_disabled_at": None,
            "auto_disable_reason": "",
        })
    webhook_delivered = sum(row["delivered"] for row in delivery_counts.values())
    webhook_pending = sum(row["pending"] for row in delivery_counts.values())
    webhook_rejected = sum(row["endpoint_rejected"] for row in delivery_counts.values())
    webhook_no_response = sum(row["no_response"] for row in delivery_counts.values())
    endpoints_truncated = len(endpoint_metrics) > METRICS_MAX_ENDPOINTS
    endpoint_metrics = endpoint_metrics[:METRICS_MAX_ENDPOINTS]
    webhooks = {
        "deliveries": len(deliveries), "delivered": webhook_delivered, "pending": webhook_pending,
        "endpoint_rejected": webhook_rejected, "no_response": webhook_no_response,
        "success_rate": _ratio(webhook_delivered, webhook_delivered + webhook_rejected + webhook_no_response),
        "window_exceeds_retention": window_start < window_end - datetime.timedelta(
            days=settings.WEBHOOK_DELIVERY_RETENTION_DAYS),
        "endpoints_auto_disabled": 0, "endpoints": endpoint_metrics,
        "endpoints_truncated": endpoints_truncated,
    }
    return {
        "start": window_start, "end": window_end,
        **totals,
        "agents": agents, "agents_truncated": group_by == "agent" and len(per_agent) > METRICS_MAX_AGENTS,
        "buckets": buckets, "webhooks": webhooks,
    }


def _normalize_address(address: str) -> str:
    return address.strip().lower()


def _suppression_row(row: RecipientSuppression) -> dict:
    return {"id": row.id, "agent_email": row.agent_email, "email": row.email,
            "reason": row.reason, "created_at": row.created_at}


# --- Suppressions -----------------------------------------------------------------

@router.get("/v1/account/suppressions")
async def list_account_suppressions(
    address: Optional[str] = Query(None, max_length=320),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    query = select(RecipientSuppression)
    if address:
        query = query.where(RecipientSuppression.email == _normalize_address(address))
    rows = (await db.execute(query.order_by(RecipientSuppression.created_at.desc(),
                                            RecipientSuppression.id).limit(limit))).scalars().all()
    return {"items": [_suppression_row(r) for r in rows]}


@router.get("/v1/account/suppressions/page")
async def list_account_suppressions_page(
    address: Optional[str] = Query(None, max_length=320),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    normalized = _normalize_address(address) if address else None
    scope = f"/v1/account/suppressions?address={normalized or ''}"
    query = select(RecipientSuppression)
    if normalized:
        query = query.where(RecipientSuppression.email == normalized)
    if cursor:
        key, item_id = decode_cursor(cursor, scope=scope)
        try:
            created_at = datetime.datetime.fromisoformat(key)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(RecipientSuppression.created_at < created_at,
                                and_(RecipientSuppression.created_at == created_at,
                                     RecipientSuppression.id > item_id)))
    rows = list((await db.execute(query.order_by(RecipientSuppression.created_at.desc(),
                                                 RecipientSuppression.id).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=rows[-1].created_at.isoformat(), item_id=rows[-1].id) if has_more and rows else None
    return {"items": [_suppression_row(r) for r in rows], "next_cursor": next_cursor}


@router.delete("/v1/account/suppressions/{address}")
async def remove_account_suppression(address: str, db: AsyncSession = Depends(get_db)):
    """Remove an address from every inbox's suppression list."""
    result = await db.execute(
        delete(RecipientSuppression).where(RecipientSuppression.email == _normalize_address(address))
    )
    if result.rowcount == 0:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Address is not suppressed in any inbox")
    await db.commit()
    return {"address": _normalize_address(address), "removed": result.rowcount}


# --- Contacts (cross-inbox view over the per-inbox rows) ---------------------------

class AccountContactCreate(ContactCreate):
    # Plain string: inbox addresses may use special-use domains (e.g. agents.local) that
    # EmailStr rejects. The value is checked against existing inboxes before use.
    agent_email: str = Field(..., min_length=3, max_length=255)

    @field_validator("agent_email", mode="before")
    @classmethod
    def _normalize_agent(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


async def _matching_contacts(db: AsyncSession, address: str, agent_email: Optional[str]) -> list[Contact]:
    query = select(Contact).where(Contact.email == _normalize_address(address))
    if agent_email:
        query = query.where(Contact.agent_email == agent_email.strip().lower())
    rows = (await db.execute(query.order_by(Contact.agent_email))).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Contact not found")
    return rows


async def _single_contact(db: AsyncSession, address: str, agent_email: Optional[str]) -> Contact:
    rows = await _matching_contacts(db, address, agent_email)
    if len(rows) > 1:
        raise HTTPException(
            status_code=409,
            detail="This address exists in several inboxes; pass agent_email to choose one",
        )
    return rows[0]


@router.get("/v1/contacts")
async def list_account_contacts(
    agent_email: Optional[str] = Query(None, max_length=255),
    q: Optional[str] = Query(None, max_length=200, description="Match address, name or company"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    query = select(Contact)
    if agent_email:
        query = query.where(Contact.agent_email == agent_email.strip().lower())
    if q and q.strip():
        pattern = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            func.lower(Contact.email).like(pattern, escape="\\")
            | func.lower(Contact.name).like(pattern, escape="\\")
            | func.lower(Contact.company).like(pattern, escape="\\")
        )
    rows = (await db.execute(query.order_by(Contact.agent_email, Contact.email).limit(limit))).scalars().all()
    return {"items": [ContactResponse.model_validate(r).model_dump(mode="json") for r in rows]}


@router.get("/v1/contacts/page")
async def list_account_contacts_page(
    agent_email: Optional[str] = Query(None, max_length=255),
    q: Optional[str] = Query(None, max_length=200, description="Match address, name or company"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_db),
):
    normalized_agent = agent_email.strip().lower() if agent_email else None
    normalized_q = q.strip().lower() if q and q.strip() else None
    scope = f"/v1/contacts?agent_email={normalized_agent or ''}&q={normalized_q or ''}"
    query = select(Contact)
    if normalized_agent:
        query = query.where(Contact.agent_email == normalized_agent)
    if normalized_q:
        pattern = "%" + normalized_q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(func.lower(Contact.email).like(pattern, escape="\\")
                            | func.lower(Contact.name).like(pattern, escape="\\")
                            | func.lower(Contact.company).like(pattern, escape="\\"))
    if cursor:
        key, _ = decode_cursor(cursor, scope=scope)
        try:
            agent_key, email_key = key.split("\0", 1)
        except ValueError:
            decode_cursor("invalid", scope=scope)
        query = query.where(or_(Contact.agent_email > agent_key,
                                and_(Contact.agent_email == agent_key, Contact.email > email_key)))
    rows = list((await db.execute(query.order_by(Contact.agent_email, Contact.email).limit(limit + 1))).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(scope=scope, key=f"{rows[-1].agent_email}\0{rows[-1].email}", item_id=rows[-1].id) if has_more and rows else None
    return {"items": [ContactResponse.model_validate(r).model_dump(mode="json") for r in rows], "next_cursor": next_cursor}


@router.post("/v1/contacts", response_model=ContactResponse, status_code=201)
async def create_account_contact(payload: AccountContactCreate, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(Agent.id).where(Agent.email == payload.agent_email)) is None:
        raise HTTPException(status_code=404, detail="Agent inbox not found")
    await _ensure_unique_email(payload.agent_email, payload.email, db)
    now = datetime.datetime.utcnow()
    contact = Contact(id=f"ct_{uuid.uuid4().hex[:12]}", agent_email=payload.agent_email, email=payload.email,
                      name=payload.name, company=payload.company, notes=payload.notes,
                      created_at=now, updated_at=now)
    db.add(contact)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A contact with this email already exists in this inbox") from None
    await db.refresh(contact)
    return contact


@router.get("/v1/contacts/{address}")
async def get_account_contact(address: str, agent_email: Optional[str] = Query(None, max_length=255),
                              db: AsyncSession = Depends(get_db)):
    """Every inbox's record for this address (or one inbox with agent_email)."""
    rows = await _matching_contacts(db, address, agent_email)
    return {"items": [ContactResponse.model_validate(r).model_dump(mode="json") for r in rows]}


@router.patch("/v1/contacts/{address}", response_model=ContactResponse)
async def update_account_contact(address: str, payload: ContactUpdate,
                                 agent_email: Optional[str] = Query(None, max_length=255),
                                 db: AsyncSession = Depends(get_db)):
    contact = await _single_contact(db, address, agent_email)
    changes = payload.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"] is not None:
        await _ensure_unique_email(contact.agent_email, changes["email"], db, exclude_id=contact.id)
    for field, value in changes.items():
        if value is not None:
            setattr(contact, field, value)
    contact.updated_at = datetime.datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A contact with this email already exists in this inbox") from None
    await db.refresh(contact)
    return contact


@router.delete("/v1/contacts/{address}", status_code=204)
async def delete_account_contact(address: str, agent_email: Optional[str] = Query(None, max_length=255),
                                 db: AsyncSession = Depends(get_db)):
    contact = await _single_contact(db, address, agent_email)
    await db.delete(contact)
    await db.commit()


# --- Export ------------------------------------------------------------------------

@router.get("/v1/account/export")
async def export_account(
    limit_per_collection: int = Query(EXPORT_DEFAULT_LIMIT, ge=1, le=EXPORT_MAX_LIMIT),
    db: AsyncSession = Depends(get_db),
):
    """Configuration and directory data for backup or migration.

    Excludes message bodies, attachments, API keys and key hashes, SMTP credentials, and
    webhook signing secrets (webhook URLs have query strings redacted). Each collection is
    capped; `truncated` and `truncated_collections` say when rows were left out.
    """
    truncated: list[str] = []

    async def collect(name: str, query, shape):
        rows = (await db.execute(query.limit(limit_per_collection + 1))).scalars().all()
        if len(rows) > limit_per_collection:
            truncated.append(name)
            rows = rows[:limit_per_collection]
        return [shape(r) for r in rows]

    agents = await collect("agents", select(Agent).order_by(Agent.created_at, Agent.id), lambda a: {
        "id": a.id, "email": a.email, "display_name": a.display_name, "description": a.description,
        "status": a.status, "threat_sensitivity": a.threat_sensitivity, "hitl_enabled": a.hitl_enabled,
        "auto_reply_enabled": a.auto_reply_enabled, "created_at": a.created_at, "deleted_at": a.deleted_at,
    })
    contacts = await collect("contacts", select(Contact).order_by(Contact.agent_email, Contact.id), lambda c: {
        "id": c.id, "agent_email": c.agent_email, "email": c.email, "name": c.name, "company": c.company,
        "notes": c.notes, "created_at": c.created_at,
    })
    templates = await collect("templates", select(EmailTemplate).order_by(EmailTemplate.agent_email, EmailTemplate.id),
                              lambda t: {"id": t.id, "agent_email": t.agent_email, "name": t.name, "subject": t.subject,
                                         "body_text": t.body_text, "body_html": t.body_html})
    suppressions = await collect("suppressions",
                                 select(RecipientSuppression).order_by(RecipientSuppression.agent_email,
                                                                       RecipientSuppression.id), _suppression_row)
    webhooks = await collect("webhook_endpoints",
                             select(WebhookEndpoint).order_by(WebhookEndpoint.agent_email, WebhookEndpoint.id),
                             _endpoint_response)
    return {
        "exported_at": datetime.datetime.utcnow(),
        "limit_per_collection": limit_per_collection,
        "truncated": bool(truncated),
        "truncated_collections": truncated,
        "counts": {"agents": len(agents), "contacts": len(contacts), "templates": len(templates),
                   "suppressions": len(suppressions), "webhook_endpoints": len(webhooks)},
        "agents": agents,
        "contacts": contacts,
        "templates": templates,
        "suppressions": suppressions,
        "webhook_endpoints": webhooks,
    }
