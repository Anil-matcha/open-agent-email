"""Transactional helpers for enqueueing metadata-only email webhook events."""

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookDelivery, WebhookEndpoint

SCHEMA_VERSION = "1"


def build_payload(event_type: str, flat: dict[str, Any], *, data_exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    """Wrap an event's flat fields in the versioned envelope.

    The flat fields stay at the top level so existing consumers keep working. `type`,
    `schema_version` and `data` are added; `data` repeats the event-specific fields
    (never bodies) without the envelope keys `id` and `created_at`, and without any
    fields named in `data_exclude`.
    """
    excluded = {"id", "created_at", *data_exclude}
    return {
        **flat,
        "type": event_type,
        "schema_version": SCHEMA_VERSION,
        "data": {key: value for key, value in flat.items() if key not in excluded},
    }


async def _subscribed_endpoints(db: AsyncSession, agent_email: str, event_type: str) -> list[WebhookEndpoint]:
    endpoints = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.agent_email == agent_email,
        WebhookEndpoint.enabled.is_(True),
    ))).scalars().all()
    return [
        endpoint for endpoint in endpoints
        if not endpoint.event_types or "*" in endpoint.event_types or event_type in endpoint.event_types
    ]


async def enqueue_email_webhook_event(
    db: AsyncSession,
    *,
    agent_email: str,
    event_type: str,
    message_id: str,
    direction: str,
    review_id: str,
    status: str,
    created_at: datetime.datetime,
    conversation_id: str | None = None,
) -> None:
    """Add one durable delivery per enabled endpoint in the message inbox.

    The caller owns the transaction and must commit this together with the
    associated message/review transition. Payloads intentionally contain only
    stable identifiers and lifecycle metadata.
    """
    endpoints = await _subscribed_endpoints(db, agent_email, event_type)
    timestamp = created_at.isoformat()
    for endpoint in endpoints:
        event_id = f"evt_{uuid.uuid4().hex}"
        payload = build_payload(event_type, {
            "id": event_id,
            "message_id": message_id,
            "agent_email": agent_email,
            "direction": direction,
            "review_id": review_id,
            "status": status,
            "conversation_id": conversation_id,
            "created_at": timestamp,
        })
        db.add(WebhookDelivery(
            id=event_id,
            endpoint_id=endpoint.id,
            agent_email=agent_email,
            event_type=event_type,
            payload=payload,
            status="pending",
            attempts=0,
            available_at=created_at,
            created_at=created_at,
        ))


async def enqueue_email_received_webhook_event(
    db: AsyncSession,
    *,
    agent_email: str,
    sender: str,
    recipient: str,
    thread_id: str,
    status: str,
    message_id: str,
    created_at: datetime.datetime,
    conversation_id: str | None = None,
) -> None:
    """Queue the existing inbound receipt contract for an inbox release."""
    endpoints = await _subscribed_endpoints(db, agent_email, "email.received")
    for endpoint in endpoints:
        event_id = f"evt_{uuid.uuid4().hex}"
        db.add(WebhookDelivery(
            id=event_id,
            endpoint_id=endpoint.id,
            agent_email=agent_email,
            event_type="email.received",
            payload=build_payload("email.received", {
                "id": event_id,
                "message_id": message_id,
                "agent_email": agent_email,
                "direction": "inbound",
                "sender": sender,
                "recipient": recipient,
                "status": status,
                "thread_id": thread_id,
                "conversation_id": conversation_id,
                "created_at": created_at.isoformat(),
            }),
            status="pending",
            attempts=0,
            available_at=created_at,
            created_at=created_at,
        ))


async def enqueue_outbound_status_webhook_event(
    db: AsyncSession,
    *,
    event_type: str,
    message_id: str,
    agent_email: str,
    recipient: str,
    status: str,
    created_at: datetime.datetime,
    conversation_id: str | None = None,
) -> None:
    """Queue metadata-only outbound lifecycle events for this inbox."""
    endpoints = await _subscribed_endpoints(db, agent_email, event_type)
    for endpoint in endpoints:
        event_id = f"evt_{uuid.uuid4().hex}"
        db.add(WebhookDelivery(
            id=event_id,
            endpoint_id=endpoint.id,
            agent_email=agent_email,
            event_type=event_type,
            # Delivery and failure events are identified by `type`; `status` stays only in the
            # flat legacy fields, never inside `data`.
            payload=build_payload(event_type, {
                "id": event_id,
                "message_id": message_id,
                "agent_email": agent_email,
                "recipient": recipient,
                "status": status,
                "conversation_id": conversation_id,
                "created_at": created_at.isoformat(),
            }, data_exclude=("status",)),
            status="pending",
            attempts=0,
            available_at=created_at,
            created_at=created_at,
        ))


async def enqueue_inbound_security_webhook_event(
    db: AsyncSession,
    *,
    event_type: str,
    message_id: str,
    agent_email: str,
    status: str,
    created_at: datetime.datetime,
    threat_category: str,
    threat_severity: str,
    threat_score: float,
    conversation_id: str | None = None,
) -> None:
    """Queue a bounded security signal without scanner explanations/content."""
    categories = {"prompt_injection", "data_exfiltration", "hidden_payload", "encoded_payload"}
    severities = {"low", "medium", "high", "critical"}
    category = threat_category[:64] if threat_category in categories else "other"
    severity = threat_severity[:16] if threat_severity in severities else "unknown"
    score = round(min(1.0, max(0.0, float(threat_score))), 2)
    endpoints = await _subscribed_endpoints(db, agent_email, event_type)
    for endpoint in endpoints:
        event_id = f"evt_{uuid.uuid4().hex}"
        db.add(WebhookDelivery(
            id=event_id,
            endpoint_id=endpoint.id,
            agent_email=agent_email,
            event_type=event_type,
            payload=build_payload(event_type, {
                "id": event_id,
                "message_id": message_id,
                "agent_email": agent_email,
                "direction": "inbound",
                "status": status,
                "conversation_id": conversation_id,
                "created_at": created_at.isoformat(),
                "threat_category": category,
                "threat_severity": severity,
                "threat_score": score,
            }),
            status="pending",
            attempts=0,
            available_at=created_at,
            created_at=created_at,
        ))


def _queue(db: AsyncSession, endpoint: WebhookEndpoint, event_type: str, flat: dict[str, Any],
           created_at: datetime.datetime) -> None:
    db.add(WebhookDelivery(
        id=flat["id"],
        endpoint_id=endpoint.id,
        agent_email=endpoint.agent_email,
        event_type=event_type,
        payload=build_payload(event_type, flat),
        status="pending",
        attempts=0,
        available_at=created_at,
        created_at=created_at,
    ))


async def enqueue_suppression_added_webhook_event(
    db: AsyncSession,
    *,
    agent_email: str,
    recipient: str,
    source: str,
    reason: str | None,
    created_at: datetime.datetime,
) -> None:
    """`agent.suppression_added`: a recipient was blocked for this inbox.

    `source` is one of operator, unsubscribe, bounce, complaint. The reason text is
    truncated; it never carries message content.
    """
    for endpoint in await _subscribed_endpoints(db, agent_email, "agent.suppression_added"):
        _queue(db, endpoint, "agent.suppression_added", {
            "id": f"evt_{uuid.uuid4().hex}",
            "agent_email": agent_email,
            "recipient": recipient,
            "source": source,
            "reason": (reason or "")[:200] or None,
            "created_at": created_at.isoformat(),
        }, created_at)


async def enqueue_domain_suppression_added_webhook_event(
    db: AsyncSession,
    *,
    agent_email: str,
    address: str,
    source: str,
    reason: str | None,
    message_id: str,
    created_at: datetime.datetime,
) -> None:
    """Queue a durable provider-feedback suppression event for the affected inbox."""
    event_type = "domain.suppression_added"
    for endpoint in await _subscribed_endpoints(db, agent_email, event_type):
        _queue(db, endpoint, event_type, {
            "id": f"evt_{uuid.uuid4().hex}",
            "address": address,
            "source": source,
            "reason": (reason or "")[:200] or None,
            "message_id": message_id,
            "created_at": created_at.isoformat(),
        }, created_at)


async def enqueue_domain_webhook_event(
    db: AsyncSession,
    *,
    event_type: str,
    domain: str,
    status: str,
    records: dict[str, str],
    created_at: datetime.datetime,
) -> None:
    """`domain.sending_verified` / `domain.sending_failed`.

    Webhook endpoints belong to an inbox, and a domain has no single owner, so the event
    goes to every enabled, subscribed endpoint whose inbox address is on that domain.
    """
    endpoints = (await db.execute(select(WebhookEndpoint).where(
        WebhookEndpoint.agent_email.like(f"%@{domain}"),
        WebhookEndpoint.enabled.is_(True),
    ))).scalars().all()
    for endpoint in endpoints:
        if endpoint.event_types and "*" not in endpoint.event_types and event_type not in endpoint.event_types:
            continue
        _queue(db, endpoint, event_type, {
            "id": f"evt_{uuid.uuid4().hex}",
            "agent_email": endpoint.agent_email,
            "domain": domain,
            "status": status,
            "records": records,
            "created_at": created_at.isoformat(),
        }, created_at)
