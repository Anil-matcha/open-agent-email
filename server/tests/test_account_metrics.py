import datetime

import pytest
from sqlalchemy import update

from app.database import AsyncSessionLocal
from app.models import Agent, Message
from tests.conftest import ADMIN, make_agent, make_key


async def _message(agent_email, message_id, created_at, *, direction="outbound", status="accepted_by_relay",
                   dmarc_status="unverified", provider_status=None):
    async with AsyncSessionLocal() as db:
        db.add(Message(
            id=message_id, agent_email=agent_email, direction=direction,
            sender=agent_email, recipient="recipient@example.com", subject="metrics",
            body_text="test", thread_id=f"thread-{message_id}", rfc_message_id=f"<{message_id}@example.com>",
            status=status, dmarc_status=dmarc_status,
            provider_delivery_status=provider_status, created_at=created_at,
        ))
        await db.commit()


async def test_account_metrics_cohort_window_aggregates_owned_live_agents(client):
    await make_agent(client, "a@agents.local")
    await make_agent(client, "b@agents.local")
    await make_agent(client, "deleted@agents.local")
    window_start = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    window_end = datetime.datetime(2025, 1, 3, tzinfo=datetime.timezone.utc)
    await _message("a@agents.local", "out1", datetime.datetime(2025, 1, 1, 1),
                   status="accepted_by_relay", provider_status="delivered")
    await _message("b@agents.local", "out2", datetime.datetime(2025, 1, 2, 1),
                   status="failed")
    await _message("b@agents.local", "in1", datetime.datetime(2025, 1, 2, 2),
                   direction="inbound", status="delivered", dmarc_status="fail")
    await _message("a@agents.local", "outside", datetime.datetime(2025, 1, 3, 0),
                   provider_status="delivered")
    await _message("deleted@agents.local", "gone", datetime.datetime(2025, 1, 2, 3),
                   provider_status="delivered")
    async with AsyncSessionLocal() as db:
        await db.execute(update(Agent).where(Agent.email == "deleted@agents.local").values(deleted_at=datetime.datetime.utcnow()))
        await db.commit()

    response = await client.get("/v1/metrics", headers=ADMIN, params={
        "start": window_start.isoformat(), "end": window_end.isoformat(),
        "bucket": "day", "group_by": "agent",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["messages_in_window"] == 3
    assert body["messages_with_lifecycle"] == 0
    assert body["summary"]["accepted"] == 2
    assert body["summary"]["submitted"] == 1
    assert body["summary"]["delivered"] == 1
    assert body["summary"]["send_failed"] == 1
    assert body["summary"]["received"] == 1
    assert body["summary"]["dmarc_fail"] == 1
    assert body["rates"]["delivered_rate"] == 0.5
    assert body["rates"]["bounce_rate"] == 0.0
    assert body["agents_truncated"] is False
    assert [group["agent_email"] for group in body["agents"]] == ["b@agents.local", "a@agents.local"]
    assert len(body["buckets"]) == 2
    assert sum(bucket["summary"]["accepted"] for bucket in body["buckets"]) == body["summary"]["accepted"]
    assert body["reconstructed_observations"] > 0
    assert body["webhooks"]["deliveries"] == 0


async def test_account_metrics_denies_agent_credentials_and_validates_window(client):
    await make_agent(client, "a@agents.local")
    key, _ = await make_key(client, "a@agents.local", "full_access")
    assert (await client.get("/v1/metrics")).status_code == 401
    assert (await client.get("/v1/metrics", headers=key)).status_code == 403
    assert (await client.get("/v1/metrics", headers=ADMIN,
                            params={"start": "2025-02-02T00:00:00Z", "end": "2025-02-01T00:00:00Z"})).status_code == 400
    assert (await client.get("/v1/metrics", headers=ADMIN,
                            params={"start": "2024-01-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"})).status_code == 400
    assert (await client.get("/v1/metrics", headers=ADMIN, params={"bucket": "week"})).status_code == 422
