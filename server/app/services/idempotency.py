"""Durable primitives for scoped, replayable API idempotency keys.

Call ``reserve_idempotency`` before applying a side effect. It commits the
claim in its own short transaction. On success, write the operation and call
``complete_idempotency`` in the same transaction as that operation so the
effect and replay receipt commit together.

An expired in-progress claim is deliberately not reclaimed automatically:
the process may have committed its side effect immediately before crashing.
It returns ``expired_in_progress`` and requires explicit recovery. This is a
safe at-most-once default, with a possible stuck key after a crash.
"""

import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import IdempotencyRecord
from app.services.smtp_secrets import decrypt_secret, encrypt_secret

DEFAULT_RETENTION = datetime.timedelta(hours=24)
DEFAULT_LEASE = datetime.timedelta(minutes=5)


def request_digest(request: bytes | str | Any) -> str:
    """Hash raw request bytes or a canonical JSON representation."""
    if isinstance(request, bytes):
        payload = request
    elif isinstance(request, str):
        payload = request.encode("utf-8")
    else:
        payload = json.dumps(request, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class IdempotencyDecision:
    outcome: str  # reserved, replay, conflict, in_progress, expired_in_progress
    scope: str
    key: str
    request_digest: str
    claim_token: str | None = None
    response_status: int | None = None
    response_body: Any = None
    response_headers: dict[str, str] | None = None
    expires_at: datetime.datetime | None = None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


async def reserve_idempotency(
    scope: str,
    key: str,
    request: bytes | str | Any,
    *,
    session_factory: Callable = AsyncSessionLocal,
    now: datetime.datetime | None = None,
    retention: datetime.timedelta = DEFAULT_RETENTION,
    lease: datetime.timedelta = DEFAULT_LEASE,
) -> IdempotencyDecision:
    """Atomically reserve a key, replay a completed response, or report conflict.

    ``scope`` must include the authenticated principal and logical route
    (including resource identifiers). The helper preserves key bytes exactly;
    callers must choose their own header validation policy before calling it.
    """
    if not scope or len(scope) > 512:
        raise ValueError("idempotency scope must contain 1 to 512 characters")
    if not key or len(key) > 128:
        raise ValueError("Idempotency-Key must contain 1 to 128 characters")
    if retention <= datetime.timedelta(0) or lease <= datetime.timedelta(0):
        raise ValueError("idempotency retention and lease must be positive")

    current = now or _utcnow()
    digest = request_digest(request)
    # The unique constraint arbitrates concurrent inserts across workers.
    # A bounded loop handles a duplicate insert or simultaneous expiry refresh.
    for _ in range(4):
        async with session_factory() as db:
            record = await db.scalar(select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
            ))
            if record is None:
                token = uuid.uuid4().hex
                record = IdempotencyRecord(
                    id=f"idem_{uuid.uuid4().hex}", scope=scope,
                    idempotency_key=key, request_digest=digest,
                    state="in_progress", claim_token=token,
                    created_at=current, updated_at=current,
                    expires_at=current + retention,
                    lease_expires_at=current + lease,
                )
                db.add(record)
                try:
                    await db.commit()
                except IntegrityError:
                    await db.rollback()
                    continue
                return IdempotencyDecision("reserved", scope, key, digest, token,
                                           expires_at=record.expires_at)

            if record.state == "completed" and record.expires_at <= current:
                token = uuid.uuid4().hex
                result = await db.execute(update(IdempotencyRecord).where(
                    IdempotencyRecord.id == record.id,
                    IdempotencyRecord.state == "completed",
                    IdempotencyRecord.expires_at <= current,
                ).values(
                    request_digest=digest, state="in_progress", claim_token=token,
                    response_status=None, response_body=None, response_encrypted=False,
                    response_headers=None,
                    created_at=current, updated_at=current,
                    expires_at=current + retention, lease_expires_at=current + lease,
                ))
                if result.rowcount:
                    await db.commit()
                    return IdempotencyDecision("reserved", scope, key, digest, token,
                                               expires_at=current + retention)
                await db.rollback()
                continue

            if record.request_digest != digest:
                return IdempotencyDecision("conflict", scope, key, digest,
                                           expires_at=record.expires_at)
            if record.state == "completed":
                return IdempotencyDecision(
                    "replay", scope, key, digest,
                    response_status=record.response_status,
                    response_body=(
                        json.loads(decrypt_secret(record.response_body, "idempotency response"))
                        if record.response_encrypted else record.response_body
                    ),
                    response_headers=record.response_headers or {},
                    expires_at=record.expires_at,
                )
            outcome = "in_progress" if record.lease_expires_at > current else "expired_in_progress"
            return IdempotencyDecision(outcome, scope, key, digest,
                                       expires_at=record.lease_expires_at)

    raise RuntimeError("Could not reserve idempotency key after concurrent updates")


async def complete_idempotency(
    db: AsyncSession,
    decision: IdempotencyDecision,
    *,
    status: int,
    body: Any,
    headers: dict[str, str] | None = None,
    now: datetime.datetime | None = None,
    retention: datetime.timedelta = DEFAULT_RETENTION,
    encrypt_response: bool = False,
) -> None:
    """Store the response without committing, so callers can commit atomically.

    Must be called with the successful ``reserved`` decision and then committed
    in the same transaction as the protected side effect.
    """
    if decision.outcome != "reserved" or not decision.claim_token:
        raise ValueError("only a reserved idempotency claim can be completed")
    current = now or _utcnow()
    stored_body = body
    if encrypt_response:
        stored_body = encrypt_secret(
            json.dumps(body, separators=(",", ":"), ensure_ascii=False),
            "idempotency response",
        )
    result = await db.execute(update(IdempotencyRecord).where(
        IdempotencyRecord.scope == decision.scope,
        IdempotencyRecord.idempotency_key == decision.key,
        IdempotencyRecord.request_digest == decision.request_digest,
        IdempotencyRecord.claim_token == decision.claim_token,
        IdempotencyRecord.state == "in_progress",
    ).values(
        state="completed", response_status=status,
        response_body=stored_body, response_encrypted=encrypt_response,
        response_headers=headers or {},
        updated_at=current, expires_at=current + retention,
    ))
    if result.rowcount != 1:
        raise RuntimeError("Idempotency claim is missing or no longer owned by this request")
    await db.flush()


def ensure_idempotency_response_encryption() -> None:
    """Fail before reserving a keyed operation whose response contains a secret."""
    encrypt_secret("key-configuration-check", "idempotency response")


async def prune_expired_idempotency_records(
    now: datetime.datetime | None = None,
    limit: int = 500,
    *,
    session_factory: Callable = AsyncSessionLocal,
) -> int:
    """Delete a bounded number of expired completed receipts; retain all claims."""
    current = now or _utcnow()
    batch_limit = max(1, min(int(limit), 5000))
    async with session_factory() as db:
        expired_ids = select(IdempotencyRecord.id).where(
            IdempotencyRecord.state == "completed",
            IdempotencyRecord.expires_at <= current,
        ).order_by(IdempotencyRecord.expires_at, IdempotencyRecord.id).limit(batch_limit)
        result = await db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id.in_(expired_ids)))
        await db.commit()
        return result.rowcount or 0


async def run_idempotency_sweeper() -> None:
    """Periodically prune expired response receipts without reclaiming active claims."""
    import asyncio
    import logging

    logger = logging.getLogger("idempotency")
    while True:
        try:
            removed = await prune_expired_idempotency_records()
            if removed:
                logger.info("Pruned %d expired idempotency receipt(s)", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Idempotency receipt cleanup failed")
        await asyncio.sleep(3600)


async def release_idempotency_claim(
    session_factory: Callable,
    decision: IdempotencyDecision,
) -> bool:
    """Release only a claim known to have failed before its side effect began."""
    if decision.outcome != "reserved" or not decision.claim_token:
        return False
    async with session_factory() as db:
        result = await db.execute(delete(IdempotencyRecord).where(
            IdempotencyRecord.scope == decision.scope,
            IdempotencyRecord.idempotency_key == decision.key,
            IdempotencyRecord.claim_token == decision.claim_token,
            IdempotencyRecord.state == "in_progress",
        ))
        await db.commit()
        return result.rowcount == 1
