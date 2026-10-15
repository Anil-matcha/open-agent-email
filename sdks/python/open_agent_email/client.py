"""Typed synchronous client for Open Agent Email's core agent workflows."""

from __future__ import annotations

import base64
import datetime
import json
from typing import Any, Generic, Literal, NotRequired, TypedDict, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit


class Attachment(TypedDict):
    filename: str
    content_type: str
    content_base64: str


class Message(TypedDict, total=False):
    id: str
    agent_email: str
    direction: str
    is_read: bool
    sender: str
    recipient: str
    subject: str
    body_text: str
    body_html: str | None
    thread_id: str
    conversation_id: str | None
    status: str
    created_at: str


class AgentTestSendResult(TypedDict):
    status: Literal["accepted", "pending_review"]
    message_id: str


class AccountApiKeyInput(TypedDict, total=False):
    name: str
    scope: Literal["account", "agent"]
    agent_email: str | None
    expires_in_days: int | None


class OtpResult(TypedDict, total=False):
    found: bool
    code: str
    message_id: str
    received_at: str
    sender: str
    subject: str


class AccountMetricsParams(TypedDict, total=False):
    """Query options for account metrics; the time window is half-open [start, end)."""
    start: str | datetime.datetime
    end: str | datetime.datetime
    bucket: Literal["day"]
    group_by: Literal["agent"]


class AccountApiKey(TypedDict, total=False):
    id: str
    name: str
    key_prefix: str
    scope: Literal["account", "agent"]
    agent_email: str | None
    created_at: str
    expires_at: str | None


class AttachmentByIndex(TypedDict, total=False):
    index: int
    filename: str
    content_type: str
    size_bytes: int
    download_url: str
    expires_at: str
    data: str


class MetricsCounter(TypedDict):
    reason_code: str
    stage: str
    outcome: str
    retryable: bool
    observations: int
    messages: int


class MetricsRates(TypedDict):
    delivered_rate: float | None
    bounce_rate: float | None
    complaint_rate: float | None
    suppression_block_rate: float | None


class MetricsSummary(TypedDict):
    accepted: int
    submitted: int
    delivered: int
    bounced_hard: int
    bounced_soft: int
    bounced_undetermined: int
    complained: int
    suppressed: int
    loopback: int
    send_failed: int
    received: int
    dmarc_pass: int
    dmarc_fail: int
    dmarc_none: int
    dmarc_error: int
    review_held: int
    review_approved: int
    review_rejected: int
    review_expired_approved: int
    review_expired_rejected: int


class AgentMetricsGroup(TypedDict):
    agent_email: str
    messages_in_window: int
    messages_with_lifecycle: int
    reconstructed_observations: int
    summary: MetricsSummary
    rates: MetricsRates
    counters: list[MetricsCounter]


class MetricsBucket(TypedDict):
    day: str
    summary: MetricsSummary
    rates: MetricsRates


class WebhookEndpointMetrics(TypedDict):
    webhook_id: str
    url_host: str
    deliveries: int
    delivered: int
    pending: int
    endpoint_rejected: int
    no_response: int
    success_rate: float | None
    last_status_code: int | None
    enabled: bool
    auto_disabled_at: str | None
    auto_disable_reason: str


class WebhookMetrics(TypedDict):
    deliveries: int
    delivered: int
    pending: int
    endpoint_rejected: int
    no_response: int
    success_rate: float | None
    window_exceeds_retention: bool
    endpoints_auto_disabled: int
    endpoints: list[WebhookEndpointMetrics]
    endpoints_truncated: bool


class AccountMetrics(TypedDict):
    start: str
    end: str
    messages_in_window: int
    messages_with_lifecycle: int
    reconstructed_observations: int
    summary: MetricsSummary
    rates: MetricsRates
    counters: list[MetricsCounter]
    agents: list[AgentMetricsGroup]
    agents_truncated: bool
    buckets: list[MetricsBucket]
    webhooks: WebhookMetrics


class MessagePage(TypedDict):
    items: list[Message]
    next_cursor: str | None


PageItem = TypeVar("PageItem")


class Page(TypedDict, Generic[PageItem]):
    """A cursor page returned by the collection page endpoints."""
    items: list[PageItem]
    next_cursor: str | None


class Contact(TypedDict):
    id: str
    agent_email: str
    email: str
    name: str
    company: str
    notes: str
    metadata: dict[str, str | int | float | bool | None]
    created_at: str
    updated_at: str


class ContactCreate(TypedDict):
    email: str
    name: str
    company: NotRequired[str]
    notes: NotRequired[str]
    metadata: NotRequired[dict[str, str | int | float | bool | None]]


class ContactUpdate(TypedDict, total=False):
    email: str
    name: str
    company: str
    notes: str
    metadata: dict[str, str | int | float | bool | None]


class ContactImportRow(TypedDict):
    email: str
    display_name: NotRequired[str]
    company: NotRequired[str]
    notes: NotRequired[str]
    metadata: NotRequired[dict[str, str | int | float | bool | None]]
    suppressed: NotRequired[bool]
    stage: NotRequired[Literal["new", "contacted", "waiting", "closed"]]


class Suppression(TypedDict):
    id: str
    agent_email: str
    email: str
    reason: str | None
    created_at: str


class EmailTemplate(TypedDict):
    id: str
    agent_email: str
    name: str
    subject: str
    body_text: str
    body_html: str
    created_at: str
    updated_at: str


class EmailTemplatePreview(TypedDict):
    subject: str
    body_text: str
    body_html: str
    variables: list[str]


class ContactEngagement(TypedDict):
    id: str
    agent_email: str
    contact_id: str
    contact_email: str
    contact_name: str
    stage: str
    next_due_at: str | None
    notes: str
    replied_at: str | None
    replied: bool
    suppressed: bool
    created_at: str
    updated_at: str


_UNSET = object()


MessageStatus = Literal[
    "delivered",
    "accepted_by_relay",
    "sending",
    "delivery_unknown",
    "pending_review",
    "rejected",
    "blocked",
    "failed",
]


def _format_send_at(value: datetime.datetime | str) -> str:
    """Serialize an offset-qualified scheduling instant."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone offset")
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("scheduled_at must be a datetime or ISO-8601 string")
    result = value.strip()
    try:
        parsed = datetime.datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scheduled_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("scheduled_at must include a timezone offset")
    return result


def _format_metrics_time(value: datetime.datetime | str | None) -> str | None:
    """Serialize an offset-qualified RFC 3339 metrics window bound."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("metrics start/end must include a timezone offset")
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("metrics start/end must be RFC 3339 datetimes")
    result = value.strip()
    try:
        parsed = datetime.datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("metrics start/end must be RFC 3339 datetimes") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("metrics start/end must include a timezone offset")
    return result


class AgentEmailError(Exception):
    """An API or transport error. The bearer token is never included in errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        detail: Any = None,
        code: str | None = None,
        details: Any = None,
        retry_after_seconds: float | None = None,
        fix: str | None = None,
        docs: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        #: The response's legacy `detail` field (string, list, or the whole body).
        self.detail = detail
        #: Stable machine code from the server's error catalog, e.g. ``rate_limited``.
        self.code = code
        #: Structured ``error.details`` (validation errors: list of ``{path, message, type}``).
        self.details = details
        #: Seconds to wait before retrying, taken from ``error.details`` or ``Retry-After``.
        self.retry_after_seconds = retry_after_seconds
        #: Suggested next step, when the server provides one.
        self.fix = fix
        #: Link to the error's documentation, when the server provides one.
        self.docs = docs


class RateLimitError(AgentEmailError):
    """HTTP 429 / code ``rate_limited``. Wait ``retry_after_seconds`` before retrying."""


class IdempotencyKeyReuseError(AgentEmailError):
    """Code ``idempotency_key_reuse``: the key was already used with a different request."""


def _error_from_http(status: int, body: Any, retry_after_header: str | None) -> AgentEmailError:
    """Build the most specific error for a failed response; works with or without `error`."""
    structured = body.get("error") if isinstance(body, dict) else None
    structured = structured if isinstance(structured, dict) else {}
    detail = body.get("detail") if isinstance(body, dict) else body
    code = structured.get("code") if isinstance(structured.get("code"), str) else None
    details = structured.get("details")

    retry_after: float | None = None
    from_details = details.get("retry_after_seconds") if isinstance(details, dict) else None
    if isinstance(from_details, (int, float)) and not isinstance(from_details, bool):
        retry_after = float(from_details)
    elif retry_after_header:
        try:
            parsed = float(retry_after_header)
            retry_after = parsed if parsed >= 0 else None
        except ValueError:
            retry_after = None

    fix = structured.get("fix") if isinstance(structured.get("fix"), str) else None
    docs = structured.get("docs") if isinstance(structured.get("docs"), str) else None
    kwargs = dict(status_code=status, detail=detail, code=code, details=details, retry_after_seconds=retry_after,
                  fix=fix, docs=docs)
    message = f"Open Agent Email API returned HTTP {status}"
    if code == "rate_limited" or status == 429:
        return RateLimitError(message, **kwargs)
    if code == "idempotency_key_reuse":
        return IdempotencyKeyReuseError(message, **kwargs)
    return AgentEmailError(message, **kwargs)


class _NoRedirect(HTTPRedirectHandler):
    """Do not let urllib forward bearer credentials to a redirect target."""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def attachment_from_bytes(
    filename: str, content: bytes, content_type: str = "application/octet-stream"
) -> Attachment:
    """Build an API attachment object from raw file bytes."""
    return {
        "filename": filename,
        "content_type": content_type,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


class AgentEmailClient:
    """Synchronous REST client. Use an inbox-scoped API key where possible."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0):
        base_url = base_url.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute http:// or https:// URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, a query, or a fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("HTTP is allowed only for localhost; use HTTPS for remote servers")
        if not api_key or any(char.isspace() for char in api_key):
            raise ValueError("api_key must be a non-empty token without whitespace")
        if not 0 < timeout <= 60:
            raise ValueError("timeout must be greater than 0 and no more than 60 seconds")
        self.base_url = base_url
        self._api_key = api_key
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())
        from .namespaces import ApiKeys, Inboxes, Webhooks
        self.inboxes = Inboxes(self)
        self.webhooks = Webhooks(self)
        self.api_keys = ApiKeys(self)

    @staticmethod
    def _inbox_path(inbox: str) -> str:
        return quote(inbox, safe="")

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str | int | float] | None = None,
        payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urlencode(query)
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw_body is not None:
            data = raw_body
            headers["Content-Type"] = content_type or "application/octet-stream"
        if idempotency_key is not None:
            if not 1 <= len(idempotency_key) <= 128:
                raise ValueError("idempotency_key must contain 1–128 characters")
            headers["Idempotency-Key"] = idempotency_key
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read(64 * 1024)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = None
            retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
            raise _error_from_http(exc.code, body, retry_after) from None
        except (URLError, TimeoutError, OSError) as exc:
            # Avoid including request headers or URL credentials in transport diagnostics.
            reason = getattr(exc, "reason", None)
            raise AgentEmailError(f"Open Agent Email request failed: {reason or type(exc).__name__}") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentEmailError("Open Agent Email returned invalid JSON") from exc

    def list_messages(
        self,
        inbox: str,
        *,
        direction: str | None = None,
        status: str | None = None,
        thread_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        label: str | None = None,
        trashed: bool = False,
    ) -> list[Message]:
        """List messages, newest first (server limit is 1–500)."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query: dict[str, str | int | float] = {"limit": limit}
        for key, value in (("direction", direction), ("status", status), ("thread_id", thread_id), ("conversation_id", conversation_id)):
            if value is not None:
                query[key] = value
        if label is not None:
            query["label"] = label
        if trashed:
            query["trashed"] = "true"
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/messages", query=query)

    def get_attachment_by_index(self, inbox: str, message_id: str, index: int, *, inline: bool = False) -> AttachmentByIndex:
        """Get attachment metadata and a short-lived signed download URL by zero-based index."""
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("index must be a non-negative integer")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/messages/{quote(message_id, safe='')}/attachments/{index}",
            query={"inline": "true"} if inline else None,
        )

    def get_message(self, inbox: str, message_id: str) -> Message:
        """Fetch one message by ID."""
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/messages/{quote(message_id, safe='')}"
        )

    def mark_message_read(self, inbox: str, message_id: str) -> Message:
        """Mark an inbound message as read and return its updated representation."""
        return self._request(
            "POST",
            f"/v1/agents/{self._inbox_path(inbox)}/messages/{quote(message_id, safe='')}/read",
        )

    def get_message_page(
        self,
        inbox: str,
        *,
        direction: str | None = None,
        read_status: Literal["unread", "read", "all"] = "all",
        status: MessageStatus | None = None,
        query: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> MessagePage:
        """Fetch a stable inbox page, optionally filtering read state or searching sender/subject."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if direction is not None and direction not in {"inbound", "outbound"}:
            raise ValueError("direction must be 'inbound' or 'outbound'")
        if read_status not in {"unread", "read", "all"}:
            raise ValueError("read_status must be 'unread', 'read', or 'all'")
        if status is not None and status not in {
            "delivered", "accepted_by_relay", "sending", "delivery_unknown",
            "pending_review", "rejected", "blocked", "failed",
        }:
            raise ValueError("status must be a supported message delivery status")
        if query is not None and len(query) > 200:
            raise ValueError("query must be no more than 200 characters")
        params: dict[str, str | int | float] = {"limit": limit}
        if direction is not None:
            params["direction"] = direction
        if read_status != "all":
            params["read_status"] = read_status
        if status is not None:
            params["status"] = status
        if query:
            params["q"] = query
        if cursor:
            params["cursor"] = cursor
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/messages/page", query=params
        )

    def iter_messages(
        self, inbox: str, *, direction: str | None = None,
        read_status: Literal["unread", "read", "all"] = "all",
        status: MessageStatus | None = None, query: str | None = None, limit: int = 50,
    ):
        """Yield every message matching the supplied filters across all pages."""
        return self._iterate_pages(
            lambda **kw: self.get_message_page(inbox, **kw), direction=direction,
            read_status=read_status, status=status, query=query, limit=limit,
        )

    @staticmethod
    def _validate_contact_fields(
        *, email: str | None = None, name: str | None = None,
        company: str | None = None, notes: str | None = None,
    ) -> None:
        if email is not None:
            normalized = email.strip().lower()
            local, separator, domain = normalized.rpartition("@")
            if (
                not separator or not local or not domain or len(normalized) > 255
                or any(char.isspace() for char in normalized)
                or "@" in local or "." not in domain
            ):
                raise ValueError("email must be a valid address of at most 255 characters")
        if name is not None and not 1 <= len(name.strip()) <= 255:
            raise ValueError("name must contain 1–255 non-whitespace characters")
        if company is not None and len(company) > 255:
            raise ValueError("company must be no more than 255 characters")
        if notes is not None and len(notes) > 4000:
            raise ValueError("notes must be no more than 4000 characters")

    def list_contacts(self, inbox: str, *, limit: int = 100) -> list[Contact]:
        """List contacts in an inbox, ordered by name and email (server limit is 1–500)."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/contacts", query={"limit": limit}
        )

    def create_contact(
        self, inbox: str, *, email: str, name: str,
        company: str = "", notes: str = "",
    ) -> Contact:
        """Create an inbox contact. A full-access API key is required."""
        self._validate_contact_fields(email=email, name=name, company=company, notes=notes)
        payload = {
            "email": email.strip().lower(), "name": name.strip(),
            "company": company.strip(), "notes": notes.strip(),
        }
        return self._request("POST", f"/v1/agents/{self._inbox_path(inbox)}/contacts", payload=payload)

    def update_contact(
        self, inbox: str, contact_id: str, *, email: str | None = None,
        name: str | None = None, company: str | None = None, notes: str | None = None,
    ) -> Contact:
        """Update the provided contact fields. A full-access API key is required."""
        changes = {key: value for key, value in {
            "email": email, "name": name, "company": company, "notes": notes,
        }.items() if value is not None}
        if not changes:
            raise ValueError("provide at least one contact field to update")
        self._validate_contact_fields(**changes)
        if "email" in changes:
            changes["email"] = changes["email"].strip().lower()
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        for key in ("company", "notes"):
            if key in changes:
                changes[key] = changes[key].strip()
        return self._request(
            "PATCH",
            f"/v1/agents/{self._inbox_path(inbox)}/contacts/{quote(contact_id, safe='')}",
            payload=changes,
        )

    def delete_contact(self, inbox: str, contact_id: str) -> None:
        """Delete a contact. A full-access API key is required."""
        self._request(
            "DELETE",
            f"/v1/agents/{self._inbox_path(inbox)}/contacts/{quote(contact_id, safe='')}",
        )

    @staticmethod
    def _validate_engagement_fields(
        *, stage: str | None = None, next_due_at: str | None | object = _UNSET,
        notes: str | None = None,
    ) -> None:
        if stage is not None and stage not in {"new", "contacted", "waiting", "closed"}:
            raise ValueError("stage must be 'new', 'contacted', 'waiting', or 'closed'")
        if next_due_at is not _UNSET and next_due_at is not None:
            if not isinstance(next_due_at, str):
                raise ValueError("next_due_at must be an RFC 3339 datetime string or None")
            try:
                datetime.datetime.fromisoformat(next_due_at.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("next_due_at must be an RFC 3339 datetime string or None") from None
        if notes is not None and len(notes) > 4000:
            raise ValueError("notes must be no more than 4000 characters")

    def list_engagements(
        self, inbox: str, *, due: bool = False, limit: int = 100,
    ) -> list[ContactEngagement]:
        """List follow-ups; due=True returns only currently due, unsuppressed contacts."""
        if not isinstance(due, bool):
            raise ValueError("due must be a boolean")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/engagements",
            query={"due": str(due).lower(), "limit": limit},
        )

    def create_engagement(
        self, inbox: str, *, contact_id: str, stage: str = "new",
        next_due_at: str | None = None, notes: str = "",
    ) -> ContactEngagement:
        """Create one follow-up record for a contact. Requires a full-access key."""
        if not isinstance(contact_id, str) or not 1 <= len(contact_id) <= 64:
            raise ValueError("contact_id must contain 1–64 characters")
        self._validate_engagement_fields(stage=stage, next_due_at=next_due_at, notes=notes)
        payload = {"contact_id": contact_id, "stage": stage, "notes": notes.strip()}
        if next_due_at is not None:
            payload["next_due_at"] = next_due_at
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/engagements", payload=payload
        )

    def update_engagement(
        self, inbox: str, engagement_id: str, *, stage: str | None = None,
        next_due_at: str | None | object = _UNSET, notes: str | None = None,
    ) -> ContactEngagement:
        """Update a follow-up; pass next_due_at=None to clear its due time."""
        if stage is None and next_due_at is _UNSET and notes is None:
            raise ValueError("provide at least one engagement field to update")
        self._validate_engagement_fields(stage=stage, next_due_at=next_due_at, notes=notes)
        payload: dict[str, Any] = {}
        if stage is not None:
            payload["stage"] = stage
        if next_due_at is not _UNSET:
            payload["next_due_at"] = next_due_at
        if notes is not None:
            payload["notes"] = notes.strip()
        return self._request(
            "PATCH",
            f"/v1/agents/{self._inbox_path(inbox)}/engagements/{quote(engagement_id, safe='')}",
            payload=payload,
        )

    def delete_engagement(self, inbox: str, engagement_id: str) -> None:
        """Delete a follow-up. Requires a full-access key."""
        self._request(
            "DELETE",
            f"/v1/agents/{self._inbox_path(inbox)}/engagements/{quote(engagement_id, safe='')}",
        )

    def import_contacts_csv(self, inbox: str, csv_text: str) -> dict[str, Any]:
        """Import up to 1,000 inert contacts from CSV; returns per-row duplicate/suppression outcomes."""
        data = csv_text.encode("utf-8")
        if len(data) > 1_000_000:
            raise ValueError("CSV must be at most 1 MB")
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/contacts/imports",
            raw_body=data, content_type="text/csv; charset=utf-8",
        )

    def import_contacts(self, agent_email: str, contacts: list[ContactImportRow], *,
                        on_conflict: Literal["merge", "skip"] = "merge",
                        idempotency_key: str | None = None) -> dict[str, Any]:
        """Import up to 1,000 structured contacts into one explicitly selected inbox."""
        if not 1 <= len(contacts) <= 1000:
            raise ValueError("contacts must contain 1–1,000 rows")
        if on_conflict not in {"merge", "skip"}:
            raise ValueError("on_conflict must be 'merge' or 'skip'")
        return self._request("POST", "/v1/contacts/import", payload={
            "agent_email": agent_email, "contacts": contacts, "on_conflict": on_conflict,
        }, idempotency_key=idempotency_key)

    def list_contact_imports(self, inbox: str) -> list[dict[str, Any]]:
        """List the 50 most recent reversible contact import batches."""
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/contacts/imports")

    def reverse_contact_import(self, inbox: str, batch_id: str) -> dict[str, Any]:
        """Reverse an import, deleting untouched imported records and retaining edited contacts."""
        return self._request(
            "DELETE",
            f"/v1/agents/{self._inbox_path(inbox)}/contacts/imports/{quote(batch_id, safe='')}",
            query={"confirm": "DELETE"},
        )

    def get_contact_consent(self, inbox: str, contact_id: str) -> dict[str, Any]:
        """Return current recorded consent state and up to 100 append-only changes."""
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/contacts/{quote(contact_id, safe='')}/consent"
        )

    def record_contact_consent(
        self, inbox: str, contact_id: str, *, action: Literal["granted", "revoked"],
        source: str, evidence: str = "", note: str = "",
    ) -> dict[str, Any]:
        """Append an operator-entered consent grant/revocation record; requires full access."""
        if action not in ("granted", "revoked"):
            raise ValueError("action must be 'granted' or 'revoked'")
        if not source.strip() or len(source.strip()) > 255:
            raise ValueError("source must contain 1–255 characters")
        if len(evidence) > 1000 or len(note) > 1000:
            raise ValueError("evidence and note must be at most 1,000 characters")
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/contacts/{quote(contact_id, safe='')}/consent",
            payload={"action": action, "source": source, "evidence": evidence, "note": note},
        )

    @staticmethod
    def _validate_template_fields(
        *, name: str | None = None, subject: str | None = None,
        body_text: str | None = None, body_html: str | None = None,
    ) -> None:
        if name is not None and not 1 <= len(name.strip()) <= 255:
            raise ValueError("name must contain 1–255 non-whitespace characters")
        if subject is not None and (
            not 1 <= len(subject.strip()) <= 512 or "\r" in subject or "\n" in subject
        ):
            raise ValueError("subject must contain 1–512 characters and no line breaks")
        for field, value in (("body_text", body_text), ("body_html", body_html)):
            if value is not None and len(value) > 100_000:
                raise ValueError(f"{field} must be no more than 100,000 characters")

    @staticmethod
    def _validate_template_preview_data(data: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("preview data must contain valid JSON values") from None
        if len(encoded) > 16_384:
            raise ValueError("preview data must be no more than 16 KB")

        count = 0

        def visit(value: Any, depth: int) -> None:
            nonlocal count
            count += 1
            if count > 500 or depth > 6:
                raise ValueError("preview data exceeds 500 values or 6 levels of nesting")
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str) or len(key) > 128:
                        raise ValueError("preview object keys must be strings of at most 128 characters")
                    visit(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    visit(child, depth + 1)
            elif isinstance(value, str):
                if len(value) > 4096:
                    raise ValueError("preview string values must be no more than 4,096 characters")
            elif value is not None and not isinstance(value, (int, float, bool)):
                raise ValueError("preview data must contain JSON values")

        visit(data, 0)

    def list_templates(self, inbox: str, *, limit: int = 100) -> list[EmailTemplate]:
        """List reusable inbox templates (limit 1–500; default 100)."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/templates", query={"limit": limit}
        )

    def get_template(self, inbox: str, template_id: str) -> EmailTemplate:
        """Fetch one saved template by ID."""
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/templates/{quote(template_id, safe='')}"
        )

    def create_template(
        self, inbox: str, *, name: str, subject: str,
        body_text: str = "", body_html: str = "",
    ) -> EmailTemplate:
        """Save an inbox template. At least one body is required; this does not send email."""
        self._validate_template_fields(name=name, subject=subject, body_text=body_text, body_html=body_html)
        if not body_text.strip() and not body_html.strip():
            raise ValueError("at least one of body_text or body_html is required")
        payload = {
            "name": name.strip(), "subject": subject.strip(),
            "body_text": body_text.strip(), "body_html": body_html.strip(),
        }
        return self._request("POST", f"/v1/agents/{self._inbox_path(inbox)}/templates", payload=payload)

    def update_template(
        self, inbox: str, template_id: str, *, name: str | None = None,
        subject: str | None = None, body_text: str | None = None, body_html: str | None = None,
    ) -> EmailTemplate:
        """Update saved template fields; a full-access API key is required."""
        changes = {key: value for key, value in {
            "name": name, "subject": subject, "body_text": body_text, "body_html": body_html,
        }.items() if value is not None}
        if not changes:
            raise ValueError("provide at least one template field to update")
        self._validate_template_fields(**changes)
        changes = {key: value.strip() for key, value in changes.items()}
        return self._request(
            "PATCH", f"/v1/agents/{self._inbox_path(inbox)}/templates/{quote(template_id, safe='')}",
            payload=changes,
        )

    def delete_template(self, inbox: str, template_id: str) -> None:
        """Delete a saved template; a full-access API key is required."""
        self._request(
            "DELETE", f"/v1/agents/{self._inbox_path(inbox)}/templates/{quote(template_id, safe='')}"
        )

    def preview_template(
        self, inbox: str, *, subject: str, data: dict[str, Any],
        body_text: str = "", body_html: str = "",
    ) -> EmailTemplatePreview:
        """Render a draft without saving it; HTML variable values are escaped by the server."""
        self._validate_template_fields(subject=subject, body_text=body_text, body_html=body_html)
        if not body_text.strip() and not body_html.strip():
            raise ValueError("at least one of body_text or body_html is required")
        self._validate_template_preview_data(data)
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/templates/preview",
            payload={"subject": subject.strip(), "body_text": body_text, "body_html": body_html, "data": data},
        )

    def preview_saved_template(
        self, inbox: str, template_id: str, *, data: dict[str, Any]
    ) -> EmailTemplatePreview:
        """Render a saved template with sample data without sending or changing it."""
        self._validate_template_preview_data(data)
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/templates/{quote(template_id, safe='')}/preview",
            payload={"data": data},
        )

    @staticmethod
    def _validate_suppression_email(email: str) -> str:
        normalized = email.strip().lower()
        local, separator, domain = normalized.rpartition("@")
        if (
            not separator or not local or not domain or len(normalized) > 255
            or any(char.isspace() for char in normalized) or "@" in local or "." not in domain
        ):
            raise ValueError("email must be a valid address of at most 255 characters")
        return normalized

    def list_suppressions(self, inbox: str, *, limit: int = 100) -> list[Suppression]:
        """List operator-managed recipient blocks for one inbox (limit 1–500)."""
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/suppressions", query={"limit": limit}
        )

    def add_suppression(
        self, inbox: str, *, email: str, reason: str | None = None
    ) -> Suppression:
        """Block an address for this inbox; this does not record unsubscribe or consent state."""
        normalized = self._validate_suppression_email(email)
        if reason is not None and len(reason) > 500:
            raise ValueError("reason must be no more than 500 characters")
        payload: dict[str, Any] = {"email": normalized}
        if reason is not None:
            payload["reason"] = reason.strip() or None
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/suppressions", payload=payload
        )

    def remove_suppression(self, inbox: str, suppression_id: str) -> None:
        """Remove an operator-managed block from this inbox."""
        self._request(
            "DELETE",
            f"/v1/agents/{self._inbox_path(inbox)}/suppressions/{quote(suppression_id, safe='')}",
        )

    def send_message(
        self,
        inbox: str,
        *,
        recipient: str,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        template_id: str | None = None,
        template_data: dict[str, Any] | None = None,
        thread_id: str | None = None,
        conversation_id: str | None = None,
        attachments: list[Attachment] | None = None,
        idempotency_key: str | None = None,
        send_at: datetime.datetime | str | None = None,
        scheduled_at: datetime.datetime | str | None = None,
    ) -> Message:
        """Send direct content or render an inbox template for this message.

        `scheduled_at` defers submission (at most 90 days ahead); `send_at` remains a deprecated alias.
        """
        payload: dict[str, Any] = {"recipient": recipient}
        if send_at is not None and scheduled_at is not None and _format_send_at(send_at) != _format_send_at(scheduled_at):
            raise ValueError("scheduled_at and send_at must match when both are provided")
        schedule = scheduled_at if scheduled_at is not None else send_at
        if schedule is not None:
            payload["scheduled_at"] = _format_send_at(schedule)
        if template_id is not None:
            payload["template_id"] = template_id
            if template_data is not None:
                payload["template_data"] = template_data
            if subject is not None or body_text is not None or body_html is not None:
                raise ValueError("provide template_id or direct message content, not both")
        else:
            if template_data is not None:
                raise ValueError("template_data requires template_id")
            if subject is None or body_text is None:
                raise ValueError("subject and body_text are required without template_id")
            payload.update({"subject": subject, "body_text": body_text})
            if body_html is not None:
                payload["body_html"] = body_html
        if thread_id is not None:
            payload["thread_id"] = thread_id
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if attachments:
            payload["attachments"] = attachments
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/messages", payload=payload,
            idempotency_key=idempotency_key,
        )

    def test_agent(self, inbox: str, *, idempotency_key: str | None = None) -> AgentTestSendResult:
        """Queue a platform-originated test email to the inbox's own address."""
        return self._request(
            "POST", f"/v1/agents/{self._inbox_path(inbox)}/test",
            idempotency_key=idempotency_key,
        )

    def reply_message(
        self,
        inbox: str,
        message_id: str,
        *,
        body_text: str | None = None,
        body_html: str | None = None,
        template_id: str | None = None,
        template_data: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        attachments: list[Attachment] | None = None,
        idempotency_key: str | None = None,
        send_at: datetime.datetime | str | None = None,
        scheduled_at: datetime.datetime | str | None = None,
    ) -> Message:
        """Reply with direct content or render an inbox template in-thread (optionally scheduled)."""
        payload: dict[str, Any] = {}
        if send_at is not None and scheduled_at is not None and _format_send_at(send_at) != _format_send_at(scheduled_at):
            raise ValueError("scheduled_at and send_at must match when both are provided")
        schedule = scheduled_at if scheduled_at is not None else send_at
        if schedule is not None:
            payload["scheduled_at"] = _format_send_at(schedule)
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        if template_id is not None:
            payload["template_id"] = template_id
            if template_data is not None:
                payload["template_data"] = template_data
            if body_text is not None or body_html is not None:
                raise ValueError("provide template_id or direct reply content, not both")
        else:
            if template_data is not None:
                raise ValueError("template_data requires template_id")
            if body_text is None:
                raise ValueError("body_text is required without template_id")
            payload["body_text"] = body_text
            if body_html is not None:
                payload["body_html"] = body_html
        if attachments:
            payload["attachments"] = attachments
        return self._request(
            "POST",
            f"/v1/agents/{self._inbox_path(inbox)}/messages/{quote(message_id, safe='')}/reply",
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def get_recent_otp(
        self,
        inbox: str,
        *,
        since_seconds: int = 600,
        wait_seconds: float = 0,
    ) -> OtpResult:
        """Find a recent contextual verification code without fetching the email body."""
        if not 1 <= since_seconds <= 86400:
            raise ValueError("since_seconds must be between 1 and 86400")
        if not 0 <= wait_seconds <= 10:
            raise ValueError("wait_seconds must be between 0 and 10")
        return self._request(
            "GET",
            f"/v1/agents/{self._inbox_path(inbox)}/messages/otp",
            query={"since_seconds": since_seconds, "wait_seconds": wait_seconds},
        )

    # ---- identity, discovery and inbox administration -------------------------------------

    def info(self) -> dict[str, Any]:
        """Public service discovery document."""
        return self._request("GET", "/v1/info")

    def whoami(self) -> dict[str, Any]:
        """Describe the credential making the call."""
        return self._request("GET", "/v1/whoami")

    def list_agents(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        """List inboxes (administrator key required)."""
        query = {"include_deleted": "true"} if include_deleted else None
        return self._request("GET", "/v1/agents", query=query)

    def create_agent(
        self, email: str, display_name: str | None = None, *, description: str | None = None,
        threat_sensitivity: str = "medium", hitl_enabled: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "email": email,
            "threat_sensitivity": threat_sensitivity, "hitl_enabled": hitl_enabled,
        }
        if display_name is not None:
            payload["display_name"] = display_name
        if description is not None:
            payload["description"] = description
        return self._request("POST", "/v1/agents", payload=payload)

    def get_agent(self, inbox: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}")

    def get_agent_metrics(self, inbox: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/metrics")

    def get_account_metrics(self, params: AccountMetricsParams | None = None) -> AccountMetrics:
        """Fetch account-wide metrics; requires an account/admin bearer credential."""
        options = params or {}
        bucket = options.get("bucket")
        group_by = options.get("group_by")
        if bucket not in {None, "day"}:
            raise ValueError("bucket must be 'day'")
        if group_by not in {None, "agent"}:
            raise ValueError("group_by must be 'agent'")
        query: dict[str, str] = {}
        start = _format_metrics_time(options.get("start"))
        end = _format_metrics_time(options.get("end"))
        if start is not None:
            query["start"] = start
        if end is not None:
            query["end"] = end
        if bucket is not None:
            query["bucket"] = bucket
        if group_by is not None:
            query["group_by"] = group_by
        return self._request("GET", "/v1/metrics", query=query)

    def get_protection(self, inbox: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/protection")

    def update_protection(self, inbox: str, *, threat_sensitivity: str, hitl_enabled: bool) -> dict[str, Any]:
        """Administrator only: an inbox key cannot weaken its own approval gate."""
        return self._request(
            "PUT", f"/v1/agents/{self._inbox_path(inbox)}/protection",
            payload={"threat_sensitivity": threat_sensitivity, "hitl_enabled": hitl_enabled},
        )

    # ---- message triage ------------------------------------------------------------------

    def _message_path(self, inbox: str, message_id: str) -> str:
        return f"/v1/agents/{self._inbox_path(inbox)}/messages/{quote(message_id, safe='')}"

    def update_message_labels(self, inbox: str, message_id: str, labels: list[str]) -> Message:
        """Replace a message's labels."""
        return self._request("PATCH", self._message_path(inbox, message_id), payload={"labels": labels})

    def trash_message(self, inbox: str, message_id: str) -> Message:
        """Move to trash; cancels a scheduled send that has not been submitted."""
        return self._request("DELETE", self._message_path(inbox, message_id))

    def restore_message(self, inbox: str, message_id: str) -> Message:
        return self._request("POST", f"{self._message_path(inbox, message_id)}/restore")

    def get_message_lifecycle(self, inbox: str, message_id: str) -> dict[str, Any]:
        return self._request("GET", f"{self._message_path(inbox, message_id)}/lifecycle")

    def forward_message(self, inbox: str, message_id: str, *, recipient: str, note: str | None = None) -> Message:
        payload: dict[str, Any] = {"recipient": recipient}
        if note is not None:
            payload["note"] = note
        return self._request("POST", f"{self._message_path(inbox, message_id)}/forward", payload=payload)

    def list_conversations(self, inbox: str, *, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        return self._request(
            "GET", f"/v1/agents/{self._inbox_path(inbox)}/conversations", query={"limit": limit}
        )

    def get_conversation(self, inbox: str, conversation_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/agents/{self._inbox_path(inbox)}/conversations/{quote(conversation_id, safe='')}",
        )

    # ---- human review --------------------------------------------------------------------

    def list_reviews(
        self, *, status: Literal["pending", "approved", "rejected"] | None = None,
        agent_email: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        query: dict[str, str | int | float] = {"limit": limit}
        if status is not None:
            query["status"] = status
        if agent_email is not None:
            query["agent_email"] = agent_email
        return self._request("GET", "/v1/reviews", query=query)

    def approve_review(self, review_id: str, *, notes: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/reviews/{quote(review_id, safe='')}/approve",
            payload={"action": "approve", "notes": notes},
        )

    def reject_review(self, review_id: str, *, notes: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/reviews/{quote(review_id, safe='')}/reject",
            payload={"action": "reject", "notes": notes},
        )

    # ---- webhooks and events (administrator key) -----------------------------------------

    def list_account_api_keys(self, *, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        """List canonical account and agent keys; secret values are never returned."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query: dict[str, str | int | float] = {"limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        return self._request("GET", "/v1/account/api-keys", query=query)

    def create_account_api_key(
        self, name: str, *, scope: Literal["account", "agent"], agent_email: str | None = None,
        expires_in_days: int | None = None, idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a canonical account or inbox key; the secret is returned once."""
        if scope not in {"account", "agent"}:
            raise ValueError("scope must be 'account' or 'agent'")
        if scope == "agent" and not agent_email:
            raise ValueError("agent_email is required for agent scope")
        payload: dict[str, Any] = {"name": name, "scope": scope}
        if agent_email is not None:
            payload["agent_email"] = agent_email
        if expires_in_days is not None:
            payload["expires_in_days"] = expires_in_days
        return self._request("POST", "/v1/account/api-keys", payload=payload, idempotency_key=idempotency_key)

    def revoke_account_api_key(self, key_id: str) -> dict[str, Any]:
        """Revoke a canonical account or inbox key."""
        return self._request("DELETE", f"/v1/account/api-keys/{quote(key_id, safe='')}", query={"confirm": "DELETE"})

    def list_webhooks(self, *, agent_email: str | None = None) -> list[dict[str, Any]]:
        query = {"agent_email": agent_email} if agent_email else None
        return self._request("GET", "/v1/webhooks", query=query)

    def create_webhook(
        self, agent_email: str, url: str, *, events: list[str] | None = None, enabled: bool = True,
    ) -> dict[str, Any]:
        """Create an endpoint. The response contains the signing secret exactly once."""
        return self._request(
            "POST", "/v1/webhooks",
            payload={"agent_email": agent_email, "url": url, "enabled": enabled, "events": events or ["*"]},
        )

    def test_webhook(self, endpoint_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/webhooks/{quote(endpoint_id, safe='')}/test")

    def rotate_webhook_secret(self, endpoint_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/webhooks/{quote(endpoint_id, safe='')}/rotate-secret")

    def delete_webhook(self, endpoint_id: str) -> None:
        self._request("DELETE", f"/v1/webhooks/{quote(endpoint_id, safe='')}")

    def list_webhook_deliveries(
        self, *, endpoint_id: str | None = None, status: str | None = None,
        event_type: str | None = None, limit: int = 50, cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query: dict[str, str | int | float] = {"limit": limit}
        for key, value in (("endpoint_id", endpoint_id), ("status", status),
                           ("event_type", event_type), ("cursor", cursor)):
            if value is not None:
                query[key] = value
        return self._request("GET", "/v1/webhooks/deliveries/page", query=query)

    def list_events(
        self, *, agent_email: str | None = None, event_type: str | None = None,
        status: str | None = None, since: str | None = None, until: str | None = None,
        limit: int = 50, cursor: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query: dict[str, str | int | float] = {"limit": limit}
        for key, value in (("agent_email", agent_email), ("event_type", event_type),
                           ("status", status), ("since", since), ("until", until), ("cursor", cursor)):
            if value is not None:
                query[key] = value
        return self._request("GET", "/v1/events", query=query)

    # ---- cursor pagination ---------------------------------------------------------------

    @staticmethod
    def _iterate_pages(fetch_page, **params):
        """Yield all items from a page method, preserving its filters."""
        cursor = None
        while True:
            page = fetch_page(cursor=cursor, **params)
            yield from page["items"]
            cursor = page.get("next_cursor")
            if not cursor:
                break

    def get_contacts_page(self, inbox: str, *, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/contacts/page", query={"limit": limit, "cursor": cursor})

    def iter_contacts(self, inbox: str, *, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_contacts_page(inbox, **kw), limit=limit)

    def get_engagements_page(self, inbox: str, *, due: bool = False, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/engagements/page", query={"due": due, "limit": limit, "cursor": cursor})

    def iter_engagements(self, inbox: str, *, due: bool = False, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_engagements_page(inbox, **kw), due=due, limit=limit)

    def get_templates_page(self, inbox: str, *, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/templates/page", query={"limit": limit, "cursor": cursor})

    def iter_templates(self, inbox: str, *, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_templates_page(inbox, **kw), limit=limit)

    def get_suppressions_page(self, inbox: str, *, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/suppressions/page", query={"limit": limit, "cursor": cursor})

    def iter_suppressions(self, inbox: str, *, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_suppressions_page(inbox, **kw), limit=limit)

    def get_agents_page(self, *, include_deleted: bool = False, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", "/v1/agents/page", query={"include_deleted": include_deleted, "limit": limit, "cursor": cursor})

    def iter_agents(self, *, include_deleted: bool = False, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_agents_page(**kw), include_deleted=include_deleted, limit=limit)

    def get_conversations_page(self, inbox: str, *, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", f"/v1/agents/{self._inbox_path(inbox)}/conversations/page", query={"limit": limit, "cursor": cursor})

    def iter_conversations(self, inbox: str, *, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_conversations_page(inbox, **kw), limit=limit)

    def get_reviews_page(self, *, status: Literal["pending", "approved", "rejected"] | None = None, agent_email: str | None = None, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", "/v1/reviews/page", query={"status": status, "agent_email": agent_email, "limit": limit, "cursor": cursor})

    def iter_reviews(self, *, status: Literal["pending", "approved", "rejected"] | None = None, agent_email: str | None = None, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_reviews_page(**kw), status=status, agent_email=agent_email, limit=limit)

    def get_webhooks_page(self, *, agent_email: str | None = None, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", "/v1/webhooks/page", query={"agent_email": agent_email, "limit": limit, "cursor": cursor})

    def iter_webhooks(self, *, agent_email: str | None = None, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_webhooks_page(**kw), agent_email=agent_email, limit=limit)

    def get_domains_page(self, *, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", "/v1/domains/page", query={"limit": limit, "cursor": cursor})

    def iter_domains(self, *, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_domains_page(**kw), limit=limit)

    def get_outbox_page(self, *, status: str | None = None, limit: int = 50, cursor: str | None = None) -> Page:
        return self._request("GET", "/v1/outbox/page", query={"status": status, "limit": limit, "cursor": cursor})

    def iter_outbox(self, *, status: str | None = None, limit: int = 50):
        return self._iterate_pages(lambda **kw: self.get_outbox_page(**kw), status=status, limit=limit)

    def get_event(self, event_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/events/{quote(event_id, safe='')}")

    def redeliver_event(self, event_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/events/{quote(event_id, safe='')}/redeliver")
