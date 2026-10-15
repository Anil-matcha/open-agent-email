import datetime

import pytest

from open_agent_email import AccountMetrics, AgentEmailClient
from tests.conftest import SECRET_KEY


def _payload() -> AccountMetrics:
    return {
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z",
        "messages_in_window": 1, "messages_with_lifecycle": 1, "reconstructed_observations": 0,
        "summary": {
            "accepted": 1, "submitted": 1, "delivered": 0, "bounced_hard": 0,
            "bounced_soft": 0, "bounced_undetermined": 0, "complained": 0, "suppressed": 0,
            "loopback": 0, "send_failed": 0, "received": 0, "dmarc_pass": 0, "dmarc_fail": 0,
            "dmarc_none": 0, "dmarc_error": 0, "review_held": 0, "review_approved": 0,
            "review_rejected": 0, "review_expired_approved": 0, "review_expired_rejected": 0,
        },
        "rates": {"delivered_rate": 0.0, "bounce_rate": 0.0, "complaint_rate": None, "suppression_block_rate": 0.0},
        "counters": [], "agents": [], "agents_truncated": False, "buckets": [],
        "webhooks": {
            "deliveries": 0, "delivered": 0, "pending": 0, "endpoint_rejected": 0,
            "no_response": 0, "success_rate": None, "window_exceeds_retention": False,
            "endpoints_auto_disabled": 0, "endpoints": [], "endpoints_truncated": False,
        },
    }


def test_account_metrics_sends_filters_and_returns_typed_view(server):
    server.route("GET", "/v1/metrics", _payload())
    client = AgentEmailClient(server.url, SECRET_KEY)
    result: AccountMetrics = client.get_account_metrics({
        "start": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        "end": "2026-01-02T00:00:00+00:00", "bucket": "day", "group_by": "agent",
    })
    assert server.last["path"] == "/v1/metrics"
    assert server.last["headers"]["authorization"] == f"Bearer {SECRET_KEY}"
    assert server.last["query"] == {
        "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-02T00:00:00+00:00",
        "bucket": "day", "group_by": "agent",
    }
    assert result["summary"]["accepted"] == 1
    assert result["rates"]["complaint_rate"] is None


@pytest.mark.parametrize("bound", [datetime.datetime(2026, 1, 1), "2026-01-01T00:00:00"])
def test_account_metrics_requires_timezone_qualified_window_bounds(server, bound):
    client = AgentEmailClient(server.url, SECRET_KEY)
    with pytest.raises(ValueError, match="timezone offset"):
        client.get_account_metrics({"start": bound})


def test_account_metrics_rejects_unknown_breakdown_options(server):
    client = AgentEmailClient(server.url, SECRET_KEY)
    with pytest.raises(ValueError, match="bucket"):
        client.get_account_metrics({"bucket": "week"})  # type: ignore[typeddict-item]
