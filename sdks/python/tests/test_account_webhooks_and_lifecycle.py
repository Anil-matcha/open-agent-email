import pytest

from open_agent_email import AgentEmailClient
from tests.conftest import SECRET_KEY

AG = "a@agents.local"
BASE = f"/v1/agents/{AG.replace('@', '%40')}"


@pytest.fixture
def client(server):
    return AgentEmailClient(server.url, SECRET_KEY)


def test_create_account_webhook_omits_the_inbox(server, client):
    server.route("POST", "/v1/webhooks", {"id": "wh_1"})
    client.create_account_webhook("https://example.com/h", events=["email.failed"], inboxes=[AG])
    assert server.last["body"] == {"url": "https://example.com/h", "enabled": True,
                                   "events": ["email.failed"], "inboxes": [AG]}


def test_per_inbox_create_is_unchanged_and_none_means_account_level(server, client):
    server.route("POST", "/v1/webhooks", {"id": "wh_1"})
    client.create_webhook(AG, "https://example.com/h")
    assert server.last["body"] == {"agent_email": AG, "url": "https://example.com/h", "enabled": True, "events": ["*"]}
    client.webhooks.create(None, url="https://example.com/h", event_types=["email.received"])
    assert "agent_email" not in server.last["body"] and server.last["body"]["events"] == ["email.received"]
    client.webhooks.create_account(url="https://example.com/h")
    assert "agent_email" not in server.last["body"]


def test_list_filters_by_account_level(server, client):
    server.route("GET", "/v1/webhooks", [])
    client.list_webhooks(account_level=True)
    assert server.last["query"] == {"account_level": "true"}
    client.webhooks.list(account_level=False)
    assert server.last["query"] == {"account_level": "false"}
    client.list_webhooks()
    assert server.last["query"] == {}
    server.route("GET", "/v1/webhooks/page", {"items": [], "next_cursor": None})
    client.get_webhooks_page(account_level=True, limit=5)
    assert server.last["query"] == {"account_level": "true", "limit": "5"}


def test_update_distinguishes_clearing_from_leaving_the_inbox_filter(server, client):
    server.route("PATCH", "/v1/webhooks/wh_1", {"id": "wh_1"})
    client.update_webhook("wh_1", enabled=False)
    assert server.last["body"] == {"enabled": False}
    client.update_webhook("wh_1", inboxes=None)
    assert server.last["body"] == {"inboxes": None}
    client.webhooks.update("wh_1", event_types=["email.failed"], inboxes=[AG])
    assert server.last["body"] == {"events": ["email.failed"], "inboxes": [AG]}


def test_lifecycle_entries_are_returned(server, client):
    body = {"message_id": "m1", "status": "delivered", "events": [],
            "entries": [{"code": "created", "at": "2026-10-15T00:00:00", "detail": ""}]}
    server.route("GET", f"{BASE}/messages/m1/lifecycle", body)
    assert client.inboxes.messages.lifecycle(AG, "m1")["entries"][0]["code"] == "created"
    assert client.get_message_lifecycle(AG, "m1") == body


def test_unset_query_options_are_not_sent_as_the_text_none(server, client):
    server.route("GET", "/v1/agents/page", {"items": [], "next_cursor": None})
    client.get_agents_page()
    assert "cursor" not in server.last["query"]
    assert "None" not in server.last["query"].values()
