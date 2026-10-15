import pytest

from open_agent_email import AgentEmailClient
from .conftest import SECRET_KEY


def test_canonical_account_key_lifecycle_and_attachment_index(server):
    server.route("GET", "/v1/account/api-keys", {"items": [], "next_cursor": None})
    server.route("POST", "/v1/account/api-keys", {"id": "k1", "key": "one-time"}, status=201)
    server.route("DELETE", "/v1/account/api-keys/k1", {"deleted": True, "id": "k1"})
    server.route("GET", "/v1/agents/inbox%40example.com/messages/m1/attachments/0", {
        "index": 0, "filename": "a.txt", "content_type": "text/plain", "size_bytes": 2,
        "download_url": "/v1/attachments/m1/a1/download?token=signed", "expires_at": "2030-01-01T00:00:00Z",
    })
    client = AgentEmailClient(server.url, SECRET_KEY)
    assert client.list_account_api_keys()["items"] == []
    created = client.create_account_api_key("ci", scope="account", idempotency_key="key-create-1")
    assert created["key"] == "one-time"
    assert server.requests[-1]["headers"]["idempotency-key"] == "key-create-1"
    assert server.requests[-1]["body"] == {"name": "ci", "scope": "account"}
    assert client.revoke_account_api_key("k1")["deleted"] is True
    assert server.last["query"] == {"confirm": "DELETE"}
    assert client.get_attachment_by_index("inbox@example.com", "m1", 0)["download_url"].startswith("/v1/attachments/")
    assert server.last["path"].endswith("/attachments/0")
    with pytest.raises(ValueError, match="non-negative"):
        client.get_attachment_by_index("inbox@example.com", "m1", -1)


def test_agent_scoped_canonical_key_requires_inbox(server):
    client = AgentEmailClient(server.url, SECRET_KEY)
    with pytest.raises(ValueError, match="agent_email"):
        client.create_account_api_key("agent", scope="agent")
