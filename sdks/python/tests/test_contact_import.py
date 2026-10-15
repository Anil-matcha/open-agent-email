from open_agent_email import AgentEmailClient

from .conftest import SECRET_KEY


def test_structured_contact_import_selects_inbox_and_passes_idempotency(server):
    server.route("POST", "/v1/contacts/import", {"batch_id": "ci_1"})
    client = AgentEmailClient(server.url, SECRET_KEY)
    result = client.import_contacts("inbox@example.com", [{
        "email": "a@example.com", "display_name": "A", "metadata": {"plan": "pro"}, "stage": "new",
    }], on_conflict="merge", idempotency_key="contacts-import-1")
    request = server.last
    assert result == {"batch_id": "ci_1"}
    assert request["path"] == "/v1/contacts/import"
    assert request["headers"]["idempotency-key"] == "contacts-import-1"
    assert request["body"] == {
        "agent_email": "inbox@example.com",
        "contacts": [{"email": "a@example.com", "display_name": "A", "metadata": {"plan": "pro"}, "stage": "new"}],
        "on_conflict": "merge",
    }
