"""Resource-style namespaces over :class:`AgentEmailClient`.

``client.inboxes.messages.send(inbox, to=..., subject=..., text=...)`` and friends are thin
wrappers around the existing flat methods (``send_message`` and so on), which keep working
unchanged. Argument names follow the short forms the REST API also accepts: ``to``, ``text``,
``html``, ``page_token``, ``add_labels`` and ``remove_labels``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:  # pragma: no cover
    from .client import AgentEmailClient, Message, MessagePage, Page


class Messages:
    """Messages in one inbox. ``inbox`` is the inbox address."""

    def __init__(self, client: "AgentEmailClient"):
        self._c = client

    def send(self, inbox: str, *, to: str, subject: str | None = None, text: str | None = None,
             html: str | None = None, **options: Any) -> "Message":
        """Send a message. ``options`` accepts the same keywords as ``send_message`` (attachments, scheduled_at, ...)."""
        return self._c.send_message(inbox, recipient=to, subject=subject, body_text=text, body_html=html, **options)

    def list(self, inbox: str, *, limit: int = 50, page_token: str | None = None, direction: str | None = None,
             read_status: str = "all", status: str | None = None, query: str | None = None) -> "MessagePage":
        """One page of messages, newest first: ``{"items": [...], "next_cursor": ...}``."""
        return self._c.get_message_page(inbox, direction=direction, read_status=read_status, status=status,
                                        query=query, limit=limit, cursor=page_token)  # type: ignore[arg-type]

    def iterate(self, inbox: str, **filters: Any):
        """Yield every matching message across pages."""
        return self._c.iter_messages(inbox, **filters)

    def get(self, inbox: str, message_id: str) -> "Message":
        return self._c.get_message(inbox, message_id)

    def reply(self, inbox: str, message_id: str, *, text: str | None = None, html: str | None = None,
              reply_all: bool = False, **options: Any) -> "Message":
        """Reply in the same thread to the other party. Messages have a single recipient, so ``reply_all`` is not supported."""
        if reply_all:
            raise ValueError("reply_all is not supported: messages have a single recipient")
        return self._c.reply_message(inbox, message_id, body_text=text, body_html=html, **options)

    def forward(self, inbox: str, message_id: str, *, to: str, note: str | None = None) -> "Message":
        return self._c.forward_message(inbox, message_id, recipient=to, note=note)

    def update(self, inbox: str, message_id: str, *, add_labels: list[str] | None = None,
               remove_labels: list[str] | None = None, labels: list[str] | None = None) -> "Message":
        """Change labels: replace the set with ``labels``, or adjust it with ``add_labels`` / ``remove_labels``."""
        if labels is not None:
            if add_labels is not None or remove_labels is not None:
                raise ValueError("labels cannot be combined with add_labels or remove_labels")
            return self._c.update_message_labels(inbox, message_id, labels)
        if add_labels is None and remove_labels is None:
            raise ValueError("provide labels, or add_labels and/or remove_labels")
        payload: dict[str, Any] = {}
        if add_labels is not None:
            payload["add_labels"] = add_labels
        if remove_labels is not None:
            payload["remove_labels"] = remove_labels
        return self._c._request("PATCH", self._c._message_path(inbox, message_id), payload=payload)

    def trash(self, inbox: str, message_id: str) -> "Message":
        return self._c.trash_message(inbox, message_id)

    def restore(self, inbox: str, message_id: str) -> "Message":
        return self._c.restore_message(inbox, message_id)

    def mark_read(self, inbox: str, message_id: str) -> "Message":
        return self._c.mark_message_read(inbox, message_id)


class Threads:
    """Conversations in one inbox."""

    def __init__(self, client: "AgentEmailClient"):
        self._c = client

    def list(self, inbox: str, *, limit: int = 50, page_token: str | None = None) -> "Page":
        return self._c.get_conversations_page(inbox, limit=limit, cursor=page_token)

    def iterate(self, inbox: str, *, limit: int = 50):
        return self._c.iter_conversations(inbox, limit=limit)

    def get(self, inbox: str, conversation_id: str) -> dict[str, Any]:
        return self._c.get_conversation(inbox, conversation_id)


class Inboxes:
    """Inbox management (administrator key) with ``messages`` and ``threads`` below it."""

    def __init__(self, client: "AgentEmailClient"):
        self._c = client
        self.messages = Messages(client)
        self.threads = Threads(client)

    def create(self, email: str, *, display_name: str | None = None, **options: Any) -> dict[str, Any]:
        return self._c.create_agent(email, display_name, **options)

    def get(self, inbox: str) -> dict[str, Any]:
        return self._c.get_agent(inbox)

    def list(self, *, limit: int = 50, page_token: str | None = None, include_deleted: bool = False) -> "Page":
        return self._c.get_agents_page(include_deleted=include_deleted, limit=limit, cursor=page_token)

    def iterate(self, **kwargs: Any):
        return self._c.iter_agents(**kwargs)


class Webhooks:
    """Webhook endpoints (administrator key)."""

    def __init__(self, client: "AgentEmailClient"):
        self._c = client

    def list(self, *, inbox: str | None = None) -> list[dict[str, Any]]:
        return self._c.list_webhooks(agent_email=inbox)

    def create(self, inbox: str, *, url: str, event_types: list[str] | None = None, enabled: bool = True) -> dict[str, Any]:
        """Create an endpoint; the response contains the signing secret exactly once."""
        return self._c.create_webhook(inbox, url, events=event_types, enabled=enabled)

    def test(self, webhook_id: str) -> dict[str, Any]:
        return self._c.test_webhook(webhook_id)

    def rotate_secret(self, webhook_id: str) -> dict[str, Any]:
        return self._c.rotate_webhook_secret(webhook_id)

    def delete(self, webhook_id: str) -> None:
        self._c.delete_webhook(webhook_id)

    def deliveries(self, *, webhook_id: str | None = None, status: str | None = None,
                   event_type: str | None = None, limit: int = 50, page_token: str | None = None) -> dict[str, Any]:
        return self._c.list_webhook_deliveries(endpoint_id=webhook_id, status=status, event_type=event_type,
                                               limit=limit, cursor=page_token)


class ApiKeys:
    """Account and inbox API keys (administrator key)."""

    def __init__(self, client: "AgentEmailClient"):
        self._c = client

    def list(self, *, limit: int = 100, page_token: str | None = None) -> dict[str, Any]:
        return self._c.list_account_api_keys(limit=limit, cursor=page_token)

    def create(self, name: str, *, inbox: str | None = None, expires_in_days: int | None = None,
               idempotency_key: str | None = None) -> dict[str, Any]:
        """Create a key for one inbox, or an account key when ``inbox`` is omitted. The secret is returned once."""
        return self._c.create_account_api_key(
            name, scope="agent" if inbox else "account", agent_email=inbox,
            expires_in_days=expires_in_days, idempotency_key=idempotency_key)

    def delete(self, key_id: str) -> dict[str, Any]:
        return self._c.revoke_account_api_key(key_id)


__all__ = ["Messages", "Threads", "Inboxes", "Webhooks", "ApiKeys"]
