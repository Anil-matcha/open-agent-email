import datetime

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import WebhookDelivery
from app.services.outbound_worker import _dispatch_one
from app.services.webhook_events import enqueue_domain_webhook_event, enqueue_outbound_status_webhook_event
from tests.conftest import ADMIN, make_agent, make_key
from tests.test_mcp_tools_full import ok

A = "a@agents.example.com"
B = "b@agents.example.com"
URL = "https://example.com/hook"


async def _create(client, **body):
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, **body})
    assert r.status_code == 201, r.text
    return r.json()


async def _fire(event_type, agent_email):
    async with AsyncSessionLocal() as db:
        await enqueue_outbound_status_webhook_event(
            db, event_type=event_type, message_id="msg_x", agent_email=agent_email,
            recipient="r@example.org", status="failed", created_at=datetime.datetime.utcnow())
        await db.commit()


async def _deliveries(endpoint_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(WebhookDelivery).where(WebhookDelivery.endpoint_id == endpoint_id)
                                 .order_by(WebhookDelivery.created_at, WebhookDelivery.id))).scalars().all()


async def test_account_endpoint_has_no_inbox_and_receives_every_inbox(client):
    await make_agent(client, A)
    await make_agent(client, B)
    account = await _create(client, events=["email.failed"])
    assert account["agent_email"] is None and account["account_level"] is True and account["inboxes"] is None
    own = await _create(client, agent_email=A, events=["email.failed"])
    assert own["account_level"] is False

    await _fire("email.failed", A)
    await _fire("email.failed", B)

    assert sorted(d.agent_email for d in await _deliveries(account["id"])) == [A, B]
    assert [d.agent_email for d in await _deliveries(own["id"])] == [A]  # per-inbox endpoints are unchanged


async def test_inbox_and_event_filters(client):
    await make_agent(client, A)
    await make_agent(client, B)
    only_a = await _create(client, inboxes=[A.upper()], events=["email.failed"])
    assert only_a["inboxes"] == [A]  # normalised
    failed_only = await _create(client, events=["email.failed"])

    await _fire("email.failed", A)
    await _fire("email.failed", B)
    await _fire("email.bounced", B)

    assert [d.agent_email for d in await _deliveries(only_a["id"])] == [A]
    assert sorted(d.event_type for d in await _deliveries(failed_only["id"])) == ["email.failed", "email.failed"]


async def test_disabled_account_endpoint_receives_nothing(client):
    await make_agent(client, A)
    account = await _create(client, enabled=False)
    await _fire("email.failed", A)
    assert await _deliveries(account["id"]) == []


async def test_validation(client):
    await make_agent(client, A)
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "agent_email": A, "inboxes": [A]})
    assert r.status_code == 422  # a per-inbox endpoint cannot carry an inbox filter
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "inboxes": [B]})
    assert r.status_code == 404  # unknown inbox
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "inboxes": ["not-an-address"]})
    assert r.status_code == 422
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "inboxes": [A, A.upper()]})
    assert r.status_code == 422
    r = await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "agent_email": "ghost@agents.example.com"})
    assert r.status_code == 404


async def test_account_level_endpoints_are_admin_only(client):
    await make_agent(client, A)
    key, _ = await make_key(client, A, "full_access")
    assert (await client.post("/v1/webhooks", headers=key, json={"url": URL})).status_code == 403
    assert (await client.get("/v1/webhooks?account_level=true", headers=key)).status_code == 403


async def test_list_filter_and_update_inboxes(client):
    await make_agent(client, A)
    await make_agent(client, B)
    account = await _create(client)
    own = await _create(client, agent_email=A)
    ids = lambda rows: sorted(r["id"] for r in rows)  # noqa: E731
    assert ids((await client.get("/v1/webhooks?account_level=true", headers=ADMIN)).json()) == [account["id"]]
    assert ids((await client.get("/v1/webhooks?account_level=false", headers=ADMIN)).json()) == [own["id"]]
    assert ids((await client.get("/v1/webhooks", headers=ADMIN)).json()) == sorted([account["id"], own["id"]])
    page = (await client.get("/v1/webhooks/page?account_level=true&limit=1", headers=ADMIN)).json()
    assert [r["id"] for r in page["items"]] == [account["id"]]

    r = await client.patch(f"/v1/webhooks/{account['id']}", headers=ADMIN, json={"inboxes": [B]})
    assert r.status_code == 200 and r.json()["inboxes"] == [B]
    r = await client.patch(f"/v1/webhooks/{account['id']}", headers=ADMIN, json={"inboxes": None})
    assert r.json()["inboxes"] is None
    assert (await client.patch(f"/v1/webhooks/{own['id']}", headers=ADMIN, json={"inboxes": [B]})).status_code == 422


async def test_account_endpoint_limit_is_separate(client):
    await make_agent(client, A)
    for _ in range(50):
        await _create(client)
    assert (await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL})).status_code == 409
    assert (await client.post("/v1/webhooks", headers=ADMIN, json={"url": URL, "agent_email": A})).status_code == 201


async def test_test_event_works_for_account_endpoint(client):
    account = await _create(client)
    r = await client.post(f"/v1/webhooks/{account['id']}/test", headers=ADMIN)
    assert r.status_code == 202, r.text
    assert len(await _deliveries(account["id"])) == 1


async def test_domain_events_reach_account_endpoints_by_domain_filter(client):
    await make_agent(client, A)
    anywhere = await _create(client, events=["domain.sending_verified"])
    elsewhere = await _create(client, events=["domain.sending_verified"], inboxes=[A])
    async with AsyncSessionLocal() as db:
        await enqueue_domain_webhook_event(
            db, event_type="domain.sending_verified", domain="other.example.net", status="verified",
            records={}, created_at=datetime.datetime.utcnow())
        await db.commit()
    assert len(await _deliveries(anywhere["id"])) == 1
    assert await _deliveries(elsewhere["id"]) == []  # its inbox filter names a different domain


async def test_inbound_mail_reaches_account_endpoint_and_respects_event_filter(client):
    await client.post("/v1/agents", headers=ADMIN, json={"email": A, "display_name": "A", "hitl_enabled": False})
    await make_agent(client, B)
    wants = await _create(client, events=["email.received"])
    other = await _create(client, events=["email.bounced"])
    sent = (await client.post(f"/v1/agents/{A}/messages", headers=ADMIN, json={
        "recipient": B, "subject": "s", "body_text": "b"})).json()
    assert sent["status"] == "sending"
    assert await _dispatch_one() is True
    received = await _deliveries(wants["id"])
    assert [d.event_type for d in received] == ["email.received"] and received[0].agent_email == B
    assert await _deliveries(other["id"]) == []


async def test_mcp_creates_and_lists_account_level_webhooks(client):
    await make_agent(client, A)
    created = await ok(client, ADMIN, "create_webhook", url=URL, events=["email.failed"], inboxes=[A])
    assert created["account_level"] is True and created["inboxes"] == [A] and created["signing_secret"]
    listed = await ok(client, ADMIN, "list_webhooks", account_level=True)
    assert [w["id"] for w in listed] == [created["id"]]
    assert await ok(client, ADMIN, "list_webhooks", account_level=False) == []
    updated = await ok(client, ADMIN, "update_webhook", webhook_id=created["id"], inboxes=[])
    assert updated["inboxes"] is None
