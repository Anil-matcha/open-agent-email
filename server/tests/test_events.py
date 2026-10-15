import datetime

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import WebhookDelivery
from app.services.webhook_delivery import prune_expired_deliveries
from tests.conftest import ADMIN, make_agent, make_key


async def _endpoint(client):
    await make_agent(client, "a@agents.local")
    r = await client.post("/v1/webhooks", headers=ADMIN, json={
        "agent_email": "a@agents.local", "url": "https://example.com/hook", "events": ["*"]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _delivery(endpoint_id, status="failed"):
    async with AsyncSessionLocal() as db:
        db.add(WebhookDelivery(id="evt_1", endpoint_id=endpoint_id, agent_email="a@agents.local",
                               event_type="email.received", payload={"x": 1}, status=status,
                               created_at=datetime.datetime.utcnow()))
        await db.commit()


async def test_events_list_get_redeliver(client):
    eid = await _endpoint(client)
    await _delivery(eid)
    page = (await client.get("/v1/events", headers=ADMIN)).json()
    assert [i["id"] for i in page["items"]] == ["evt_1"]
    assert (await client.get("/v1/events?agent_email=other@agents.local", headers=ADMIN)).json()["items"] == []
    assert (await client.get("/v1/events/evt_1", headers=ADMIN)).json()["payload"] == {"x": 1}
    assert (await client.get("/v1/events/missing", headers=ADMIN)).status_code == 404
    r = await client.post("/v1/events/evt_1/redeliver", headers=ADMIN)
    assert r.status_code in (200, 202) and r.json()["status"] == "pending"
    assert (await client.post("/v1/events/evt_1/redeliver", headers=ADMIN)).status_code == 409  # already pending


async def test_events_admin_only(client):
    await make_agent(client, "a@agents.local")
    key, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await client.get("/v1/events", headers=key)).status_code == 403
    assert (await client.get("/v1/events")).status_code == 401


async def test_get_webhook_and_its_deliveries(client):
    eid = await _endpoint(client)
    await _delivery(eid)
    assert (await client.get(f"/v1/webhooks/{eid}", headers=ADMIN)).json()["id"] == eid
    assert (await client.get("/v1/webhooks/nope", headers=ADMIN)).status_code == 404
    assert len((await client.get(f"/v1/webhooks/{eid}/deliveries", headers=ADMIN)).json()) == 1
    # static route still wins over the {endpoint_id} capture
    assert (await client.get("/v1/webhooks/deliveries", headers=ADMIN)).status_code == 200


async def test_event_filter_is_bound_to_cursor(client):
    eid = await _endpoint(client)
    async with AsyncSessionLocal() as db:
        db.add_all([
            WebhookDelivery(id="evt_a1", endpoint_id=eid, agent_email="a@agents.local", event_type="email.received",
                            payload={"n": 1}, status="failed", created_at=datetime.datetime.utcnow()),
            WebhookDelivery(id="evt_a2", endpoint_id=eid, agent_email="a@agents.local", event_type="email.received",
                            payload={"n": 2}, status="failed", created_at=datetime.datetime.utcnow()),
        ])
        await db.commit()
    first = await client.get("/v1/events?limit=1&agent_email=a%40agents.local", headers=ADMIN)
    assert first.status_code == 200 and first.json()["next_cursor"]
    cursor = first.json()["next_cursor"]
    mismatched = await client.get(f"/v1/events?limit=1&agent_email=other%40agents.local&cursor={cursor}", headers=ADMIN)
    assert mismatched.status_code == 400
    assert mismatched.json()["error"]["code"] == "invalid_cursor"


async def test_event_time_filters_are_inclusive_and_bound_to_cursor(client):
    eid = await _endpoint(client)
    start = datetime.datetime(2026, 1, 1, 0, 0, 0)
    async with AsyncSessionLocal() as db:
        db.add_all([
            WebhookDelivery(id=f"evt_time_{i}", endpoint_id=eid, agent_email="a@agents.local",
                            event_type="email.received", payload={}, status="failed",
                            created_at=start + datetime.timedelta(days=i))
            for i in range(3)
        ])
        await db.commit()
    url = "/v1/events?since=2026-01-02T00%3A00%3A00Z&until=2026-01-03T00%3A00%3A00Z&limit=1"
    first = await client.get(url, headers=ADMIN)
    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == ["evt_time_2"]
    assert first.json()["next_cursor"]
    cursor = first.json()["next_cursor"]
    second = await client.get(
        "/v1/events",
        params={"since": "2026-01-02T00:00:00Z", "until": "2026-01-03T00:00:00Z", "limit": 1, "cursor": cursor},
        headers=ADMIN,
    )
    assert [item["id"] for item in second.json()["items"]] == ["evt_time_1"]
    mismatch = await client.get(
        f"/v1/events?since=2026-01-01T00%3A00%3A00Z&until=2026-01-03T00%3A00%3A00Z&limit=1&cursor={cursor}",
        headers=ADMIN,
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["error"]["code"] == "invalid_cursor"
    naive = await client.get("/v1/events?since=2026-01-01T00%3A00%3A00", headers=ADMIN)
    assert naive.status_code == 422
    inverted = await client.get("/v1/events?since=2026-01-03T00%3A00%3A00Z&until=2026-01-01T00%3A00%3A00Z", headers=ADMIN)
    assert inverted.status_code == 422


async def test_event_payload_expires_but_id_returns_gone(client, monkeypatch):
    eid = await _endpoint(client)
    now = datetime.datetime.utcnow()
    await _delivery(eid)
    monkeypatch.setattr(settings, "WEBHOOK_DELIVERY_RETENTION_DAYS", 30)
    async with AsyncSessionLocal() as db:
        row = await db.get(WebhookDelivery, "evt_1")
        row.created_at = now - datetime.timedelta(days=31)
        await db.commit()
        assert await prune_expired_deliveries(db, now=now) == 1
        await db.commit()
        expired = await db.get(WebhookDelivery, "evt_1")
        assert expired.status == "expired" and expired.payload == {}
    assert (await client.get("/v1/events/evt_1", headers=ADMIN)).status_code == 410
    assert (await client.post("/v1/events/evt_1/redeliver", headers=ADMIN)).status_code == 410
    assert (await client.get("/v1/events", headers=ADMIN)).json()["items"] == []
