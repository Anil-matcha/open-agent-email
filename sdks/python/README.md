# Open Agent Email Python SDK

A small synchronous Python client for the core agent-to-human email workflows. It uses only the Python standard library and supports Python 3.11+.

## Quickstart

Create an inbox-scoped API key with read access (or full access to send), then:

```python
from open_agent_email import AgentEmailClient

client = AgentEmailClient(
    "https://mail.example.com",
    api_key="oae_live_your_inbox_key",
    timeout=15,
)

messages = client.list_messages("agent@example.com", limit=20, direction="inbound")
for message in messages:
    print(message["id"], message["sender"], message["subject"])

page = client.get_message_page(
    "agent@example.com", query="invoice", read_status="unread",
    status="delivered", limit=50
)
for message in page["items"]:
    print(message["id"], message["subject"])
if page["next_cursor"]:
    next_page = client.get_message_page(
        "agent@example.com", query="invoice", read_status="unread",
        status="delivered",
        limit=50, cursor=page["next_cursor"]
    )

message = client.get_message("agent@example.com", messages[0]["id"])
print(message["body_text"])
updated = client.mark_message_read("agent@example.com", message["id"])
print(updated["is_read"])

result = client.get_recent_otp("agent@example.com", since_seconds=600, wait_seconds=5)
if result.get("found"):
    print("Verification code:", result["code"])
```

`get_message_page` searches sender and subject, accepts `inbound` or `outbound` direction filters, and can filter by `read_status="unread"`, `"read"`, or `"all"` (the default). The optional `status` filter accepts `delivered`, `accepted_by_relay`, `sending`, `delivery_unknown`, `pending_review`, `rejected`, `blocked`, or `failed`. New inbound messages start unread; outbound messages start read. Keep the same search, direction, read-status, and delivery-status parameters when following a `next_cursor`.

Use `mark_message_read(inbox, message_id)` to mark an inbound message read. It returns the updated message; the endpoint requires a full-access API key.


## Resource-style names

```python
client.inboxes.create("support@agents.example.com")
client.inboxes.messages.send("support@agents.example.com", to="person@example.com", subject="Hi", text="Hello")
page = client.inboxes.messages.list("support@agents.example.com", limit=20)
client.inboxes.messages.update("support@agents.example.com", page["items"][0]["id"], add_labels=["vip"])
```

The flat methods above are unchanged. See [docs/sdk-mapping.md](../../docs/sdk-mapping.md) for the full table.

## Inbox contacts

Contacts are an address book scoped to one inbox. Listing is available with a read-only key; creating, updating, and deleting contacts requires a full-access key. Contact email addresses are normalized to lowercase, and duplicate addresses within the same inbox return HTTP 409.

```python
contact = client.create_contact(
    "agent@example.com",
    email="alex@example.net",
    name="Alex Rivera",
    company="Example Co",
    notes="Prefers email follow-up",
)
print(contact["id"], contact["email"])

contacts = client.list_contacts("agent@example.com", limit=100)
updated = client.update_contact(
    "agent@example.com", contact["id"], notes="Follow up next week"
)
client.delete_contact("agent@example.com", contact["id"])
```

The list limit is 1–500 (default 100). Email and name are limited to 255 characters, company to 255, and notes to 4,000. The SDK checks these bounds before sending requests. Contacts are inert records; creating one does not send an email.

Consent changes are operator-entered, append-only records for a contact in one inbox. Read recorded state and up to 100 history entries with `get_contact_consent` (read access); append a grant or revocation with `record_contact_consent` (full access):

```python
history = client.get_contact_consent("agent@example.com", contact["id"])
history = client.record_contact_consent(
    "agent@example.com", contact["id"], action="granted",
    source="Web signup form", evidence="signup-4821", note="Product updates",
)
print(history["current"], history["events"])
```

`source` is required. These entries are an audit trail only: they do not verify evidence, establish legal consent, change send policy, remove suppression, or send email. CSV import does not create consent records.

## Follow-ups

Follow-up records track a contact's stage (`new`, `contacted`, `waiting`, or `closed`), optional due time, and notes. Listing works with a read-access key; creating, updating, and deleting requires full access. A contact can have one follow-up per inbox. Use `due=True` to list only overdue records that are open, unreplied, and not suppressed. A due time must be an ISO 8601/RFC 3339 datetime string; set it to `None` in `update_engagement` to clear it. The SDK checks stage, due-time parsing, and the 4,000-character notes bound before sending.

```python
followup = client.create_engagement(
    "agent@example.com", contact_id=contact["id"],
    stage="waiting", next_due_at="2026-10-20T09:00:00Z",
    notes="Send the requested product details",
)
due_items = client.list_engagements("agent@example.com", due=True, limit=100)
updated = client.update_engagement(
    "agent@example.com", followup["id"], stage="closed", next_due_at=None,
)
client.delete_engagement("agent@example.com", updated["id"])
```

Scheduling a suppressed contact returns HTTP 422. A verified inbound reply is recorded by the server and clears the due time; callers cannot set or clear the reply flag. Due records are reminders only and never send email automatically.

CSV import accepts UTF-8 text with an `email` header and optional `name`, `company`, and `notes` headers. The file may contain at most 1,000 rows and 1 MB. Duplicate and suppressed addresses are skipped with row-level reasons; no email is sent and import does not establish consent.

```python
result = client.import_contacts_csv("agent@example.com", csv_text)
print(result["batch_id"], result["created"], result["skipped"])
batches = client.list_contact_imports("agent@example.com")
reversal = client.reverse_contact_import("agent@example.com", result["batch_id"])
print(reversal["deleted"], reversal["retained_after_edits"])
```

Reversal removes only contacts that have not been edited since import. Full-access scope is required for import and reversal.

## Reusable email templates

Templates are scoped to an inbox and never send by themselves. Listing and previewing are available with read access; creating, updating, and deleting requires a full-access key. Template names must be 1–255 characters, subjects 1–512 characters without line breaks, and each body is limited to 100,000 characters. At least one of the plain-text or HTML bodies must be present. Duplicate names within one inbox return HTTP 409.

Draft or saved templates can be previewed with nested JSON data. Preview data is limited to 16 KB, 500 values, six levels of nesting, 4,096 characters per string, and 128 characters per object key. Missing variables render as empty strings, and values substituted into HTML are escaped.

```python
template = client.create_template(
    "agent@example.com",
    name="Support update",
    subject="Update for {{customer.name}}",
    body_text="Hi {{customer.name}}, your ticket {{ticket_id}} is being reviewed.",
    body_html="<p>Hi {{customer.name}}, your ticket {{ticket_id}} is being reviewed.</p>",
)
preview = client.preview_saved_template(
    "agent@example.com", template["id"],
    data={"customer": {"name": "Alex"}, "ticket_id": "T-42"},
)
print(preview["subject"], preview["body_text"])

templates = client.list_templates("agent@example.com", limit=100)
draft_preview = client.preview_template(
    "agent@example.com", subject="Hello {{name}}", body_text="Welcome, {{name}}!",
    data={"name": "Alex"},
)
client.update_template("agent@example.com", template["id"], name="Support follow-up")
client.delete_template("agent@example.com", template["id"])
```

Use a saved template for an individual outbound message or reply by passing `template_id` and optional `template_data` to the send methods. The SDK rejects mixing a template with direct content. Normal suppression, approval, and idempotency behavior still applies.

```python
sent = client.send_message(
    "agent@example.com",
    recipient="alex@example.net",
    template_id=template["id"],
    template_data={"customer": {"name": "Alex"}, "ticket_id": "T-42"},
)
reply = client.reply_message(
    "agent@example.com",
    "msg_0123456789ab",
    template_id=template["id"],
    template_data={"customer": {"name": "Alex"}, "ticket_id": "T-42"},
)
```

## Recipient suppressions

Suppressions are explicit, operator-managed blocks scoped to one inbox. Listing requires read access; adding or removing an entry requires full-access scope. The SDK normalizes addresses to lowercase and checks email length (maximum 255 characters), optional reason length (maximum 500 characters), and list limit (1–500) before sending.

```python
blocked = client.add_suppression(
    "agent@example.com", email="person@example.net", reason="Requested no further contact"
)
entries = client.list_suppressions("agent@example.com", limit=100)
client.remove_suppression("agent@example.com", blocked["id"])
```

This list is an explicit sending block, not an unsubscribe mechanism or a record of consent/preferences. Removing a suppression permits sending again; it does not establish that the recipient consented.

The SDK sends the key in the `Authorization: Bearer` header. Keep it in a secret manager or environment variable, and use HTTPS except for local development. The client never prints or includes the token in its API exceptions. Each socket operation uses a bounded timeout (15 seconds by default, configurable up to 60 seconds).

## Send and reply

```python
sent = client.send_message(
    "agent@example.com",
    recipient="person@example.net",
    subject="Your support request",
    body_text="Hi Alex, I have an update for you.",
    idempotency_key="ticket-492-update-1",  # reuse on retry of the same request
)

reply = client.reply_message(
    "agent@example.com",
    message_id="inbound-message-id",
    body_text="Thanks for the details. I will follow up shortly.",
    idempotency_key="inbound-message-id-reply-1",
)
```

The server requires a full-access key to send. Idempotency keys must be 1–128 characters; reusing a key with changed content returns HTTP 409.

## Attachments

Attachments are submitted as base64 JSON. Use `attachment_from_bytes` to construct the value without managing the encoding yourself:

```python
from pathlib import Path
from open_agent_email import AgentEmailClient, attachment_from_bytes

client = AgentEmailClient("https://mail.example.com", api_key="oae_live_your_inbox_key")
pdf = attachment_from_bytes(
    "invoice.pdf",
    Path("invoice.pdf").read_bytes(),
    "application/pdf",
)
client.send_message(
    "agent@example.com",
    recipient="person@example.net",
    subject="Invoice",
    body_text="The invoice is attached.",
    attachments=[pdf],
    idempotency_key="invoice-2026-10-14",
)
```

The server defaults to at most 10 attachments, 10 MiB per file, and 20 MiB total decoded content; the encoded JSON request is also subject to the server's request-size limit.

## Errors

`AgentEmailError` includes `status_code` and the server's safe `detail` (when available). Handle 401/403 as credential or scope issues, 404 as a missing inbox/message, 409 as an idempotency conflict, and transport/timeout failures as retryable only when using the same idempotency key for sends.

```python
from open_agent_email import AgentEmailError

try:
    client.send_message(...)
except AgentEmailError as exc:
    print(exc.status_code, exc.detail)
```

## Verify inbound webhooks

`construct_event` authenticates a delivery and returns the parsed event. It uses the `Webhook-Signature` header (`t=<unix>,v1=<hex>`) when present and otherwise the legacy `X-Webhook-*` headers, enforces a 5-minute window, and accepts a list of secrets while one is being rotated.

```python
from open_agent_email import WebhookSignatureError, construct_event

try:
    event = construct_event(raw_body, request.headers, [current_secret, previous_secret])
except WebhookSignatureError:
    return Response(status_code=401)

if event["type"] == "email.received":
    ...  # event["payload"]["data"] holds the event fields; event["id"] is the dedup key
```

The lower-level `verify_webhook_signature` below still works for the legacy headers. Verify against the exact raw request body before decoding JSON, then deduplicate the event ID in durable storage; retries and manual replays reuse that ID.

```python
from open_agent_email import verify_webhook_signature

valid = verify_webhook_signature(
    raw_body,  # exact bytes read from the request, before JSON parsing
    event_id=request.headers.get("X-Webhook-Event-Id", ""),
    timestamp=request.headers.get("X-Webhook-Timestamp", ""),
    signature=request.headers.get("X-Webhook-Signature", ""),
    secret=signing_secret,
    tolerance_seconds=300,
)
if not valid:
    return Response(status_code=401)

event = json.loads(raw_body)
# Atomically record the event ID and apply the event; reject an already-recorded ID.
```

The helper checks the timestamp window and compares HMAC-SHA256 signatures in constant time. During a signing-secret rotation, the sender includes signatures for both the current and previous secret for a 24-hour overlap; receivers should accept a request when either listed signature matches a configured secret. After the overlap expires, only the current signature is sent. Install the new secret and retain the old one through the overlap before removing it. The receiver still needs an atomic event-ID deduplication store to handle retries safely. Route on the payload's `event_type`; acknowledge `webhook.test` without treating it as an email.

## Local install

`oae listen` reconnects after transient disconnects. If another client connects for the same inbox, the older stream closes with code `4000` and reason `replaced`; the CLI stops instead of reconnecting and taking over again. Normal close (`1000`) and unknown application close codes (`4000`–`4999`) are terminal.

From the repository root:

```bash
python -m pip install ./sdks/python
```
