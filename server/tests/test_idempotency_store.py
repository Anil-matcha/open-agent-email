import datetime
import asyncio

from app.database import AsyncSessionLocal
from app.models import IdempotencyRecord
from sqlalchemy import func, select
from app.services.idempotency import complete_idempotency, prune_expired_idempotency_records, reserve_idempotency


async def _complete(decision, *, now=None, retention=None):
    async with AsyncSessionLocal() as db:
        await complete_idempotency(
            db, decision, status=201,
            body={"id": "resource-1", "secret": "once"},
            headers={"content-type": "application/json"}, now=now,
            **({"retention": retention} if retention is not None else {}),
        )
        await db.commit()


async def test_completed_idempotency_response_replays_verbatim():
    reserved = await reserve_idempotency(
        "account:webhooks:create", "retry-1", {"url": "https://example.com/hook"})
    assert reserved.outcome == "reserved"
    await _complete(reserved)

    replay = await reserve_idempotency(
        "account:webhooks:create", "retry-1", {"url": "https://example.com/hook"})
    assert replay.outcome == "replay"
    assert replay.response_status == 201
    assert replay.response_body == {"id": "resource-1", "secret": "once"}
    assert replay.response_headers == {"content-type": "application/json"}


async def test_same_key_with_changed_request_conflicts():
    reserved = await reserve_idempotency("inbox:a:contacts:create", "retry-2", {"email": "a@example.com"})
    await _complete(reserved)

    changed = await reserve_idempotency(
        "inbox:a:contacts:create", "retry-2", {"email": "b@example.com"})
    assert changed.outcome == "conflict"


async def test_concurrent_reservations_have_one_owner():
    decisions = await asyncio.gather(*[
        reserve_idempotency("inbox:a:messages:send", "simultaneous-1", {"body": "same"})
        for _ in range(2)
    ])
    assert sorted(decision.outcome for decision in decisions) == ["in_progress", "reserved"]


async def test_in_progress_and_expired_claims_are_not_reexecuted_automatically():
    started = datetime.datetime(2026, 1, 1)
    reserved = await reserve_idempotency(
        "account:api-keys:create", "retry-3", {"name": "ci"}, now=started,
        lease=datetime.timedelta(seconds=30))

    active_retry = await reserve_idempotency(
        "account:api-keys:create", "retry-3", {"name": "ci"},
        now=started + datetime.timedelta(seconds=10))
    assert active_retry.outcome == "in_progress"

    expired_retry = await reserve_idempotency(
        "account:api-keys:create", "retry-3", {"name": "ci"},
        now=started + datetime.timedelta(seconds=31))
    assert expired_retry.outcome == "expired_in_progress"
    assert expired_retry.claim_token is None

    changed_retry = await reserve_idempotency(
        "account:api-keys:create", "retry-3", {"name": "other"},
        now=started + datetime.timedelta(seconds=31))
    assert changed_retry.outcome == "conflict"
    assert reserved.claim_token is not None


async def test_expired_completed_key_can_be_reserved_for_a_new_request():
    started = datetime.datetime(2026, 1, 1)
    short_retention = datetime.timedelta(seconds=10)
    reserved = await reserve_idempotency(
        "account:webhooks:create", "retry-4", {"url": "https://one.example/hook"},
        now=started, retention=short_retention)
    await _complete(reserved, now=started, retention=short_retention)

    after_retention = await reserve_idempotency(
        "account:webhooks:create", "retry-4", {"url": "https://two.example/hook"},
        now=started + short_retention + datetime.timedelta(seconds=1),
        retention=short_retention)
    assert after_retention.outcome == "reserved"
    assert after_retention.claim_token != reserved.claim_token


async def test_cleanup_is_bounded_and_preserves_expired_in_progress_claims():
    started = datetime.datetime(2026, 1, 1)
    retention = datetime.timedelta(seconds=10)
    first = await reserve_idempotency("cleanup:done", "done-1", {"n": 1}, now=started, retention=retention)
    second = await reserve_idempotency("cleanup:done", "done-2", {"n": 2}, now=started, retention=retention)
    await _complete(first, now=started, retention=retention)
    await _complete(second, now=started, retention=retention)
    await reserve_idempotency("cleanup:pending", "pending-1", {"n": 3}, now=started, retention=retention)

    removed = await prune_expired_idempotency_records(
        now=started + retention + datetime.timedelta(seconds=1), limit=1)
    assert removed == 1
    async with AsyncSessionLocal() as db:
        completed = await db.scalar(select(func.count()).select_from(IdempotencyRecord).where(
            IdempotencyRecord.state == "completed"))
        in_progress = await db.scalar(select(func.count()).select_from(IdempotencyRecord).where(
            IdempotencyRecord.state == "in_progress"))
    assert completed == 1
    assert in_progress == 1
