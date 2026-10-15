import pytest

from app import metrics_registry
from app.config import settings
from tests.conftest import ADMIN, make_agent

SENDER = "sender@agents.example.com"


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics_registry.reset()
    yield
    metrics_registry.reset()


def _sample(text, name, **labels):
    label_text = "{" + ",".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}" if labels else ""
    for line in text.splitlines():
        if line.startswith(f"{name}{label_text} "):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{name}{label_text} not found")


async def test_metrics_requires_credentials_and_rejects_non_admin_keys(client):
    assert (await client.get("/metrics")).status_code == 401
    await make_agent(client, SENDER)
    key = (await client.post("/v1/api-keys", headers=ADMIN, json={"name": "k", "agent_email": SENDER})).json()["key"]
    assert (await client.get("/metrics", headers={"Authorization": f"Bearer {key}"})).status_code == 403
    assert (await client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.get("/metrics", headers=ADMIN)).status_code == 200


async def test_separate_metrics_token_reads_metrics_only(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-secret")
    scrape = {"Authorization": "Bearer scrape-secret"}
    assert (await client.get("/metrics", headers=scrape)).status_code == 200
    assert (await client.get("/v1/agents", headers=scrape)).status_code == 401


async def test_queue_depth_and_oldest_pending_age_follow_the_outbox(client):
    await client.post("/v1/agents", headers=ADMIN, json={"email": SENDER, "display_name": "S", "hitl_enabled": False})
    empty = (await client.get("/metrics", headers=ADMIN)).text
    assert _sample(empty, "oae_outbox_jobs", status="pending") == 0
    assert _sample(empty, "oae_outbox_oldest_pending_age_seconds") == 0
    for i in range(3):
        await client.post(f"/v1/agents/{SENDER}/messages", headers=ADMIN, json={
            "recipient": f"r{i}@example.org", "subject": "s", "body_text": "b"})
    text = (await client.get("/metrics", headers=ADMIN)).text
    assert _sample(text, "oae_outbox_jobs", status="pending") == 3
    assert _sample(text, "oae_outbox_oldest_pending_age_seconds") >= 0
    assert "# TYPE oae_outbox_jobs gauge" in text
    for status in ("processing", "uncertain", "failed", "complete", "blocked"):
        _sample(text, "oae_outbox_jobs", status=status)  # zero-filled, so alert rules never see a missing series
    for status in ("pending", "failed"):
        _sample(text, "oae_webhook_deliveries", status=status)


async def test_worker_heartbeat_and_smtp_counters_are_exported(client):
    text = (await client.get("/metrics", headers=ADMIN)).text
    assert "oae_outbound_worker_heartbeat_age_seconds" not in text  # no worker has run in this process
    metrics_registry.set_gauge("outbound_worker_heartbeat_timestamp_seconds", __import__("time").time() - 30)
    metrics_registry.inc("smtp_sessions_total", 4)
    metrics_registry.inc("smtp_sessions_refused_total")
    metrics_registry.inc("smtp_messages_total", result="accepted")
    metrics_registry.inc("smtp_messages_total", 2, result="scanner_unavailable")
    metrics_registry.inc("outbound_worker_iteration_errors_total")
    text = (await client.get("/metrics", headers=ADMIN)).text
    assert 29 <= _sample(text, "oae_outbound_worker_heartbeat_age_seconds") <= 60
    assert _sample(text, "oae_smtp_sessions_total") == 4
    assert _sample(text, "oae_smtp_sessions_refused_total") == 1
    assert _sample(text, "oae_smtp_messages_total", result="accepted") == 1
    assert _sample(text, "oae_smtp_messages_total", result="scanner_unavailable") == 2
    assert _sample(text, "oae_smtp_messages_total", result="rejected") == 0
    assert _sample(text, "oae_outbound_worker_iteration_errors_total") == 1
    assert _sample(text, "oae_outbound_worker_up") == 0  # lifespan does not run under the test transport


async def test_smtp_handler_counts_outcomes():
    from smtp_receiver import SmtpHandler

    class Handler(SmtpHandler):
        def __init__(self, result):
            self.result = result

        async def _handle_data(self, server, session, envelope):
            return self.result

    for result in ("250 Message accepted for agent delivery", "451 Attachment scanner temporarily unavailable; please retry",
                   "451 Temporary message processing failure; please retry", "550 No", "554 Attachment rejected by malware scanner"):
        await Handler(result).handle_DATA(None, None, None)
    by = {dict(k)["result"]: v for k, v in metrics_registry.counters("smtp_messages_total").items()}
    assert by == {"accepted": 1, "scanner_unavailable": 1, "deferred": 1, "rejected": 2}
