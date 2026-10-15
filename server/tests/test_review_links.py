import asyncio
import datetime
import re

import pytest
from sqlalchemy import func, select, update

import app.services.review_notifications as notifications
from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Agent, Message, OutboundJob, ReviewItem, ReviewLinkToken, SmtpConfig
from app.services.review_expiry import expire_stale_reviews
from tests.conftest import ADMIN, make_agent, make_key

AGENT = "a@agents.local"
BASE = f"/v1/agents/{AGENT}"
PAYLOAD = {"recipient": "to@example.com", "subject": "Quarterly <b>plan</b>", "body_text": "Hello <script>x</script> body"}
REVIEWER = "boss@example.com"
LINK_RE = re.compile(r"https://mail\.example\.com(/review-link/[A-Za-z0-9_-]{43})")


@pytest.fixture
def mailbox(monkeypatch):
    """Capture reviewer emails instead of speaking SMTP; enable public links."""
    sent = []

    def fake_send(config, password, from_addr, to_addr, subject, body):
        sent.append({"from": from_addr, "to": to_addr, "subject": subject, "body": body})
        return True, "accepted"

    monkeypatch.setattr(notifications, "_send", fake_send)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://mail.example.com")
    return sent


async def _smtp():
    async with AsyncSessionLocal() as db:
        db.add(SmtpConfig(id="default", host="smtp.example.com", port="587", username="u", password="",
                          use_tls=True, use_ssl=False, from_email="noreply@agents.local", enabled=True))
        await db.commit()


async def _agent(client, notify=REVIEWER, **extra):
    await make_agent(client, AGENT)
    body = {"threat_sensitivity": "medium", "hitl_enabled": True, **extra}
    if notify:
        body["review_notify_email"] = notify
    r = await client.put(f"{BASE}/protection", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text


async def _held(client):
    r = await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    assert r.status_code == 201 and r.json()["status"] == "pending_review", r.text
    await notifications.drain_review_notifications()
    return r.json()["id"]


async def _links(client, mailbox):
    await _smtp()
    await _agent(client)
    mid = await _held(client)
    assert len(mailbox) == 1
    found = LINK_RE.findall(mailbox[0]["body"])
    assert len(found) == 2
    return mid, {"approve": found[0], "reject": found[1]}


async def _count(model):
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def _review():
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(ReviewItem))


# ---- notification -----------------------------------------------------------------------------

async def test_reviewer_gets_two_hashed_links(client, mailbox):
    mid, links = await _links(client, mailbox)
    mail = mailbox[0]
    assert mail["to"] == REVIEWER and mail["from"] == "noreply@agents.local"
    assert "Quarterly" in mail["subject"] and PAYLOAD["recipient"] in mail["body"]
    raw_tokens = [link.rsplit("/", 1)[1] for link in links.values()]
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(ReviewLinkToken))).scalars().all()
        assert sorted(r.action for r in rows) == ["approve", "reject"]
        assert all(len(r.token_hash) == 64 and r.token_hash not in raw_tokens for r in rows)
        assert all(r.expires_at > datetime.datetime.utcnow() for r in rows)
    assert (await _review()).status == "pending"


async def test_no_mail_without_notify_email_smtp_or_public_url(client, mailbox, monkeypatch):
    await _agent(client, notify=None)
    await _smtp()
    await _held(client)
    assert mailbox == [] and await _count(ReviewLinkToken) == 0


async def test_no_smtp_means_no_mail_and_no_tokens(client, mailbox):
    await _agent(client)
    await _held(client)
    assert mailbox == [] and await _count(ReviewLinkToken) == 0


async def test_non_public_base_url_means_no_links(client, mailbox, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    await _smtp()
    await _agent(client)
    await _held(client)
    assert mailbox == [] and await _count(ReviewLinkToken) == 0


async def test_smtp_failure_does_not_break_send(client, monkeypatch):
    monkeypatch.setattr(notifications, "_send", lambda *a, **k: (False, "relay down"))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://mail.example.com")
    await _smtp()
    await _agent(client)
    await _held(client)
    assert (await _review()).status == "pending"


# ---- public pages -----------------------------------------------------------------------------

async def test_get_is_read_only_and_escapes_content(client, mailbox):
    _, links = await _links(client, mailbox)
    for _ in range(3):
        page = await client.get(links["approve"])
        assert page.status_code == 200
    assert "&lt;b&gt;plan&lt;/b&gt;" in page.text and "&lt;script&gt;" in page.text
    assert "<script>" not in page.text and "Approve and send" in page.text
    assert page.headers["cache-control"].startswith("no-store")
    assert (await _review()).status == "pending" and await _count(OutboundJob) == 0
    async with AsyncSessionLocal() as db:
        assert all(r.used_at is None for r in (await db.execute(select(ReviewLinkToken))).scalars())


async def test_approve_link_single_use(client, mailbox):
    mid, links = await _links(client, mailbox)
    r = await client.post(links["approve"], data={"confirm": "approve"})
    assert r.status_code == 200 and "approved" in r.text
    review = await _review()
    assert review.status == "approved" and review.reviewed_by == f"magic_link:{REVIEWER}"
    assert await _count(OutboundJob) == 1
    again = await client.post(links["approve"], data={"confirm": "approve"})
    assert again.status_code == 410
    assert (await client.get(links["approve"])).status_code == 410
    assert await _count(OutboundJob) == 1


async def test_reject_link_blocks_send(client, mailbox):
    mid, links = await _links(client, mailbox)
    r = await client.post(links["reject"], data={"confirm": "reject"})
    assert r.status_code == 200 and "rejected" in r.text
    assert (await _review()).status == "rejected" and await _count(OutboundJob) == 0
    async with AsyncSessionLocal() as db:
        assert (await db.get(Message, mid)).status == "rejected"


async def test_sibling_link_after_decision_is_refused(client, mailbox):
    _, links = await _links(client, mailbox)
    assert (await client.post(links["reject"], data={"confirm": "reject"})).status_code == 200
    sibling = await client.post(links["approve"], data={"confirm": "approve"})
    assert sibling.status_code == 409
    assert (await client.get(links["approve"])).status_code == 409
    assert (await _review()).status == "rejected" and await _count(OutboundJob) == 0


async def test_tampered_malformed_and_unknown_tokens(client, mailbox):
    _, links = await _links(client, mailbox)
    good = links["approve"]
    flipped = good[:-1] + ("A" if good[-1] != "A" else "B")
    for bad in (flipped, "/review-link/short", "/review-link/" + "A" * 43, "/review-link/" + "A" * 44,
                "/review-link/" + "!" * 43):
        assert (await client.get(bad)).status_code == 404
        assert (await client.post(bad, data={"confirm": "approve"})).status_code == 404
    assert (await _review()).status == "pending"


async def test_expired_link_is_refused(client, mailbox):
    _, links = await _links(client, mailbox)
    async with AsyncSessionLocal() as db:
        await db.execute(update(ReviewLinkToken).values(expires_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=1)))
        await db.commit()
    assert (await client.get(links["approve"])).status_code == 404
    assert (await client.post(links["approve"], data={"confirm": "approve"})).status_code == 404
    assert (await _review()).status == "pending" and await _count(OutboundJob) == 0


async def test_link_lifetime_is_capped_by_review_expiry(client, mailbox):
    await _smtp()
    await _agent(client, review_expiry_hours=2)
    await _held(client)
    async with AsyncSessionLocal() as db:
        for row in (await db.execute(select(ReviewLinkToken))).scalars():
            assert row.expires_at <= datetime.datetime.utcnow() + datetime.timedelta(hours=2, seconds=5)


async def test_post_requires_explicit_matching_confirmation(client, mailbox):
    _, links = await _links(client, mailbox)
    assert (await client.post(links["approve"])).status_code == 400
    assert (await client.post(links["approve"], data={"confirm": "reject"})).status_code == 400
    assert (await client.post(links["approve"], data={"confirm": "approve", "x": "1"})).status_code == 400
    assert (await client.post(links["approve"], json={"confirm": "approve"})).status_code == 400
    assert (await client.post(links["approve"], content=b"confirm=" + b"a" * 500,
                              headers={"content-type": "application/x-www-form-urlencoded"})).status_code == 413
    assert (await _review()).status == "pending" and await _count(OutboundJob) == 0
    # A rejected attempt does not burn the link.
    assert (await client.post(links["approve"], data={"confirm": "approve"})).status_code == 200


async def test_concurrent_posts_approve_exactly_once(client, mailbox):
    _, links = await _links(client, mailbox)
    results = await asyncio.gather(*[client.post(links["approve"], data={"confirm": "approve"}) for _ in range(6)])
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and set(codes) <= {200, 409, 410}, codes
    assert await _count(OutboundJob) == 1 and (await _review()).status == "approved"


async def test_concurrent_approve_and_reject_links_resolve_once(client, mailbox):
    _, links = await _links(client, mailbox)
    results = await asyncio.gather(
        client.post(links["approve"], data={"confirm": "approve"}),
        client.post(links["reject"], data={"confirm": "reject"}),
    )
    assert sorted(r.status_code for r in results).count(200) == 1
    review = await _review()
    assert review.status in {"approved", "rejected"}
    assert await _count(OutboundJob) == (1 if review.status == "approved" else 0)


async def test_soft_deleted_inbox_links_are_unavailable(client, mailbox):
    _, links = await _links(client, mailbox)
    async with AsyncSessionLocal() as db:
        await db.execute(update(Agent).values(deleted_at=datetime.datetime.utcnow()))
        await db.commit()
    assert (await client.get(links["approve"])).status_code == 404
    assert (await client.post(links["approve"], data={"confirm": "approve"})).status_code == 404
    assert (await _review()).status == "pending"


# ---- settings ---------------------------------------------------------------------------------

async def test_only_admin_can_change_or_read_review_settings(client):
    await make_agent(client, AGENT)
    key, _ = await make_key(client, AGENT, "full_access")
    body = {"threat_sensitivity": "medium", "hitl_enabled": True, "review_notify_email": "evil@example.com"}
    assert (await client.put(f"{BASE}/protection", headers=key, json=body)).status_code == 403
    assert (await client.put(f"{BASE}/protection", json=body)).status_code == 401
    assert (await client.get(f"{BASE}/protection/review", headers=key)).status_code == 403
    assert "review_notify_email" not in (await client.get(f"{BASE}/protection", headers=key)).json()
    async with AsyncSessionLocal() as db:
        assert (await db.scalar(select(Agent.review_notify_email))) is None


async def test_review_settings_semantics_and_validation(client):
    await make_agent(client, AGENT)
    base = {"threat_sensitivity": "medium", "hitl_enabled": True}
    settings_url = f"{BASE}/protection/review"
    initial = (await client.get(settings_url, headers=ADMIN)).json()
    assert initial == {"review_notify_email": None, "review_expiry_hours": None, "review_expiry_action": "reject"}

    r = await client.put(f"{BASE}/protection", headers=ADMIN, json={
        **base, "review_notify_email": "Boss@Example.com", "review_expiry_hours": 24, "review_expiry_action": "approve"})
    assert r.status_code == 200
    assert (await client.get(settings_url, headers=ADMIN)).json() == {
        "review_notify_email": "boss@example.com", "review_expiry_hours": 24, "review_expiry_action": "approve"}

    # Omitted fields are preserved; explicit null clears.
    await client.put(f"{BASE}/protection", headers=ADMIN, json={**base, "threat_sensitivity": "high"})
    assert (await client.get(settings_url, headers=ADMIN)).json()["review_expiry_hours"] == 24
    await client.put(f"{BASE}/protection", headers=ADMIN, json={
        **base, "review_notify_email": None, "review_expiry_hours": None, "review_expiry_action": None})
    assert (await client.get(settings_url, headers=ADMIN)).json() == initial

    for bad in ({"review_notify_email": "not-an-email"}, {"review_notify_email": "a" * 96 + "@x.io"},
                {"review_expiry_hours": 0}, {"review_expiry_hours": 721}, {"review_expiry_action": "auto"}):
        assert (await client.put(f"{BASE}/protection", headers=ADMIN, json={**base, **bad})).status_code == 422


# ---- expiry sweeper ---------------------------------------------------------------------------

async def _age_reviews(hours):
    async with AsyncSessionLocal() as db:
        await db.execute(update(ReviewItem).values(
            created_at=datetime.datetime.utcnow() - datetime.timedelta(hours=hours)))
        await db.commit()


async def test_expiry_rejects_by_default(client):
    await _agent(client, notify=None, review_expiry_hours=1)
    await _held(client)
    await _age_reviews(2)
    assert await expire_stale_reviews() == 1
    review = await _review()
    assert review.status == "rejected" and review.reviewed_by == "system:expiry"
    assert "rejected automatically" in review.reviewer_notes
    assert await _count(OutboundJob) == 0


async def test_expiry_approves_only_when_explicitly_configured(client):
    await _agent(client, notify=None, review_expiry_hours=1, review_expiry_action="approve")
    await _held(client)
    await _age_reviews(2)
    assert await expire_stale_reviews() == 1
    assert (await _review()).status == "approved" and await _count(OutboundJob) == 1


async def test_expiry_is_idempotent_and_respects_window(client):
    await _agent(client, notify=None, review_expiry_hours=3)
    await _held(client)
    await _age_reviews(2)
    assert await expire_stale_reviews() == 0 and (await _review()).status == "pending"
    await _age_reviews(4)
    assert await expire_stale_reviews() == 1
    assert await expire_stale_reviews() == 0
    assert await expire_stale_reviews() == 0
    assert await _count(OutboundJob) == 0


async def test_expiry_ignores_inboxes_without_a_window(client):
    await _agent(client, notify=None)
    await _held(client)
    await _age_reviews(24 * 30)
    assert await expire_stale_reviews() == 0 and (await _review()).status == "pending"


async def test_expiry_does_not_override_a_human_decision(client):
    await _agent(client, notify=None, review_expiry_hours=1, review_expiry_action="approve")
    await _held(client)
    review = await _review()
    assert (await client.post(f"/v1/reviews/{review.id}/reject", headers=ADMIN)).status_code == 200
    await _age_reviews(2)
    assert await expire_stale_reviews() == 0
    assert (await _review()).status == "rejected" and await _count(OutboundJob) == 0


async def test_expiry_respects_batch_limit_and_concurrent_sweeps(client):
    await _agent(client, notify=None, review_expiry_hours=1, hitl_enabled=True)
    for _ in range(3):
        await client.post(f"{BASE}/messages", headers=ADMIN, json=PAYLOAD)
    await _age_reviews(2)
    assert await expire_stale_reviews(limit=2) == 2
    first, second = await asyncio.gather(expire_stale_reviews(), expire_stale_reviews())
    assert first + second == 1
    async with AsyncSessionLocal() as db:
        statuses = (await db.execute(select(ReviewItem.status))).scalars().all()
    assert statuses == ["rejected"] * 3


async def test_expired_review_link_page_reports_decided(client, mailbox):
    await _smtp()
    await _agent(client, review_expiry_hours=1)
    await _held(client)
    links = LINK_RE.findall(mailbox[0]["body"])
    await _age_reviews(2)
    await expire_stale_reviews()
    assert (await client.get(links[0])).status_code == 409
    assert (await client.post(links[0], data={"confirm": "approve"})).status_code == 409
    assert await _count(OutboundJob) == 0
