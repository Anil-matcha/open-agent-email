# Operations guide

## Database migrations

Schema changes are managed with Alembic (`server/migrations`).

- On startup the server applies pending migrations automatically.
- A database created before Alembic is stamped at baseline `0001` and then upgraded; a new database is stamped at head.
- Run manually: `cd server && python -m app.migrate`.
- Add a change: edit `app/models.py`, then `alembic revision --autogenerate -m "describe change"`, review the generated file, commit it. `tests/test_migrations.py` fails if models and migrations drift.
- Take a backup before upgrading production (below). Downgrades are not guaranteed to preserve data.

## Backup and restore

Back up **both** of these together; one without the other is not recoverable:

1. **The database.**
   - SQLite: stop writes (or use `sqlite3 open_agent_email.db ".backup backup.db"`), then copy the file.
   - PostgreSQL: `pg_dump --format=custom "$DATABASE_URL" > backup.dump`.
2. **`SMTP_CONFIG_ENCRYPTION_KEY`** (and `SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS` during a rotation). SMTP relay passwords and webhook signing secrets are encrypted with it; a restored database without the key fails startup by design. Also back up `ATTACHMENT_URL_SIGNING_KEY` if you set it explicitly; without that key, previously issued attachment links cannot be verified.

Message bodies and attachments live in the database, so they are covered by the database backup.

Restore:

1. Stop the service.
2. Restore the database (`cp backup.db ...` or `pg_restore --clean --dbname "$DATABASE_URL" backup.dump`).
3. Set the same `SMTP_CONFIG_ENCRYPTION_KEY`.
4. Start the service; migrations run, then check `GET /ready`.
5. Verify: sign in, open an inbox, send a test webhook (`POST /v1/webhooks/{id}/test`).

Rehearse a restore into a scratch environment on a schedule; an untested backup is not evidence.

## Encryption key rotation

1. Generate a new Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
2. Set `SMTP_CONFIG_ENCRYPTION_KEY=<new>` and `SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS=<old>`; restart. Startup re-encrypts every stored secret with the new key.
3. Confirm `/ready` is healthy and a webhook test succeeds, then remove the previous-key setting and restart.
4. Back up the new key with the database.

## Attachment download links

Message attachment metadata contains signed download URLs that expire after five minutes. Set `ATTACHMENT_URL_SIGNING_KEY` to a stable secret if links must remain valid across restarts or independently of the SMTP encryption key; otherwise the server derives the signing key from `SMTP_CONFIG_ENCRYPTION_KEY`, falling back to the admin API key. Back up an explicitly configured signing key with the database and protect it as a secret.

## Optional attachment malware scanning

Attachment scanning is disabled by default. To enable the Compose ClamAV service, set `ATTACHMENT_SCAN_ENABLED=true` in `.env` and run `docker compose --profile attachment-scan up --build`. The daemon listens only on the Compose network; do not publish its port to the host or the internet. The Compose service persists signature data in the `clamav-signatures` volume. For a non-Compose deployment, point the server at a trusted clamd service using the settings below.

| Setting | Default | Purpose |
| --- | --- | --- |
| `ATTACHMENT_SCAN_ENABLED` | `false` | Enable scanning at the shared inbound/outbound service boundary. |
| `ATTACHMENT_SCAN_HOST` | `clamav` | Private clamd hostname or IP. |
| `ATTACHMENT_SCAN_PORT` | `3310` | clamd TCP port. |
| `ATTACHMENT_SCAN_TIMEOUT_SECONDS` | `15` | Per-message scan budget, including queue wait, all attachment connections, transfers, and replies. |
| `ATTACHMENT_SCAN_MAX_BYTES` | `26214400` | Largest decoded attachment eligible for scanning. |
| `ATTACHMENT_SCAN_MAX_CONCURRENT` | `4` | Maximum simultaneous scan operations per server process. |
| `ATTACHMENT_SCAN_CHUNK_BYTES` | `65536` | Maximum data chunk sent to clamd at a time. |

The server sends attachment bytes through clamd's `INSTREAM` protocol without creating temporary files. Attachments are scanned sequentially per message; concurrent requests are bounded by the configured scan limit. A clean scan permits normal persistence or dispatch. Detected malware is a permanent rejection (SMTP 554; REST/MCP HTTP 422), before message or idempotency-record writes. A file over `ATTACHMENT_SCAN_MAX_BYTES` is permanently rejected as too large (SMTP 552; REST/MCP HTTP 413). If clamd is unavailable, returns an error, or misses the message scan deadline, the server fails closed with a transient error (SMTP 451; REST/MCP HTTP 503); SMTP senders may retry after service recovery. Monitor daemon health and signature-update logs, provision clamd memory/CPU for expected message volume, and alert on repeated scanner errors. During scanner maintenance, attachment-bearing requests are unavailable while scanning remains enabled; disable the feature only as an explicit operator decision.

This scans file bytes for signatures recognized by the installed ClamAV database. It does not replace the separate prompt-injection checks on message text, and it cannot guarantee detection of zero-day or otherwise unknown malware.

## Webhook event retention

`WEBHOOK_DELIVERY_RETENTION_DAYS` defaults to 30. The delivery worker erases payloads, retry errors, replay metadata, and leases for older delivered or terminally failed events, while keeping a compact event ID tombstone. Listing excludes expired tombstones; `GET /v1/events/{id}` and replay return HTTP 410 after expiry. Endpoint deletion removes its deliveries and tombstones.

## Review approval links and expiry

- Approval links need a public `https` `PUBLIC_BASE_URL` reachable by the reviewer and an enabled upstream SMTP relay (Settings → SMTP). The reviewer email is sent directly through the relay, not through the inbox's own outbound queue, so it is never itself held for review.
- Link tokens are stored hashed in `review_link_tokens`; the raw token exists only in the email. Expired rows are harmless and can be deleted at any time: `DELETE FROM review_link_tokens WHERE expires_at < now()`.
- `GET /ready` does not depend on the notification path; a failed or skipped notification is logged (`review_notifications`) and the review simply waits in the dashboard.
- The expiry sweeper runs every 60 seconds in each API process and is safe with several instances: each review is resolved through a compare-and-set, so it is decided once. Auto-resolutions appear in the review history as reviewer `system:expiry` and emit `email.review_approved` / `email.review_rejected` webhooks.
- Choose the expiry action deliberately. The default is reject; `approve` sends held mail without a human decision once the window passes.

## Scheduled sending

`scheduled_at` (an ISO-8601 datetime with an explicit timezone offset, at most 90 days ahead) on send/reply defers submission. `send_at` remains accepted as a deprecated compatibility alias. Timezone-naive values are rejected. A scheduled message has status `scheduled`; trashing it before submission (`DELETE /v1/agents/{email}/messages/{id}`) cancels it. With the approval gate on, the message is held for review first and the deferral is honored on approval. Delivery time is best-effort within the worker poll interval, and a message whose time passes while the service is down is sent on recovery.

Restoring a message from trash before its `scheduled_at` re-arms the send (the canceled queue job returns to pending, using an atomic compare-and-set so a job that has since changed is never touched). Restoring after `scheduled_at` has passed leaves it canceled rather than sending late.

## Trash, permanent deletion and retention

- **Trash** is a soft delete: the message disappears from lists but stays readable by id and restorable. Restoring a message that is not in trash returns 409 `not_in_trash`.
- **Permanent delete**: `DELETE /v1/agents/{email}/messages/{id}?permanent=true&confirm=DELETE` (full-access key or admin). It removes the message, attachments, queue job, retry history, review record and any emailed review links. It is refused with 409 while a review is pending or the message has a queued (`pending`) or in-progress outbound job, so a send can never be dropped silently. A scheduled send must be canceled (trashed) first.
- **Retention**: a background janitor purges messages that have been in trash longer than `TRASH_RETENTION_DAYS` (default 30; `0` disables; maximum 3650). Each sweep handles at most 100 messages every 5 minutes and skips any message with a queued or in-progress send, so a large backlog drains over several sweeps and the job is safe to run on multiple instances.
- **Audit**: every purge, manual or by retention, writes a row to `message_purge_log` (inbox, message id, who, `manual` or `retention`, time). No subject, body, address or attachment data is kept. Backups taken before a purge still contain the message, so apply your backup retention policy accordingly.

## Custom domains and DKIM

1. Register a domain: `POST /v1/domains {"domain": "agents.example.com"}`. This generates a 2048-bit DKIM key (the private key is stored encrypted with `SMTP_CONFIG_ENCRYPTION_KEY`; registration is refused with 503 when no key is configured) and returns the exact records to publish.
2. Publish the MX, SPF, DKIM (`oae1._domainkey.<domain>`) and DMARC records.
   - MX points at `MAIL_HOSTNAME` (default `mx.<SHARED_DOMAIN>`), which must be the host running the SMTP listener.
   - If your outbound relay requires an SPF include (for example `amazonses.com`), set `SPF_INCLUDE` before registering.
3. `POST /v1/domains/{domain}/verify` looks the records up (3 s per query). Each record is `verified`, `pending` (not found), `mismatch` (found but wrong, including duplicate SPF records), or `error` (DNS failure). The domain is `verified` when MX, SPF and DKIM all match; DMARC is recommended but not required.

4. Mail sent from a verified domain through the configured relay is DKIM-signed with the domain's own key. `Date` and `Message-ID` are deliberately not covered by the signature, because relays such as Amazon SES replace both and would invalidate it (found in a live test: Gmail reported `dkim=fail` for our key while SES's own signatures passed). Through SES the message also carries SES's aligned signature, which is what normally satisfies DMARC. A signing failure never blocks delivery; the message is sent unsigned and the failure is logged.

Inbound SMTP disposition follows the effective published DMARC policy only when authentication returns an exact `fail`: `p=reject` returns SMTP 550 before any inbox write, while `p=quarantine` stores the message in the normal pending-review queue. For a From subdomain using a parent policy, `sp` supplies the effective disposition when present; otherwise `p` applies. `p=none`, absent or invalid policy, DNS temporary errors, and unverified results use the normal inbound workflow. The implementation does not apply `np` or `pct` sampling semantics.

Domains registered before this feature carry placeholder DKIM data and cannot be verified; delete and register them again.

## Production validation record

Passing unit tests and `/ready` checks do not prove a production mail deployment. Before enabling external traffic, run each exercise against an isolated staging environment and retain its date, software revision, operator, result, and sanitized evidence:

| Exercise | Evidence to retain |
| --- | --- |
| PostgreSQL deployment | Apply all migrations to a clean database and an upgraded copy; exercise inbox reads, message writes, cursor pagination, and worker claims; confirm `/ready` returns healthy. |
| Backup and restore | Restore the database and matching encryption/signing keys into a separate environment; verify message/attachment reads, encrypted relay/webhook secrets, and a signed webhook test. |
| Key rotation | Rotate the encryption key using the previous-key setting; verify stored credentials and webhook signatures, then remove the previous key and restart successfully. |
| Domain and authentication | Publish staging MX, SPF, DKIM, and DMARC records; verify DNS, receive a message with known SPF/DKIM/DMARC outcomes, and confirm outbound DKIM against the delivered raw message. Include `p=reject`, `p=quarantine`, and inherited `sp` cases. |
| Provider feedback | Send through the configured staging provider and deliver its signed lifecycle notification; verify exact message/recipient correlation, duplicate notification handling, and the resulting webhook event. |
| SMTP ambiguity and recovery | Simulate an upstream relay accepting a message and dropping the connection before the response reaches this service; verify the message becomes uncertain, is not automatically resent, and manual retry requires duplicate-risk confirmation. |
| Operational monitoring | Confirm alerts cover readiness failures, database/worker errors, growing outbox and webhook backlogs, uncertain SMTP outcomes, and attachment scanner failures. `/ready` provides point-in-time health and queue counts; deployment-specific dashboards and alert delivery must be verified separately. |

These are operator-run release gates, not claims established by the repository test suite. Do not enable production inbound or outbound mail until the applicable exercises pass and their records are reviewed.

## Running the validation drills locally

- **PostgreSQL test run:** `TEST_DATABASE_URL=postgresql+asyncpg://user:pw@host:port/db python -m pytest -q` (from `server/`; the suite creates and drops tables in that database, so use a scratch one). `pytest.ini` runs all tests on one event loop because asyncpg connections cannot move between loops.
- **Migration paths:** `DATABASE_URL=... alembic upgrade head` on an empty database, and `alembic upgrade 0001` plus seeded rows then `upgrade head` for the upgrade path. A drift check (`alembic.autogenerate.compare_metadata`) should report no differences.
- **Backup/restore and key rotation:** `python scripts/ops_drill.py` boots the real server on PostgreSQL, creates a message with an attachment, a webhook secret and a domain DKIM key, dumps and restores into a fresh database, checks it refuses to start with a missing or wrong `SMTP_CONFIG_ENCRYPTION_KEY`, verifies the data and secrets after restore, then rotates the key using `SMTP_CONFIG_ENCRYPTION_KEY_PREVIOUS`. It prints PASS/FAIL per step. CI runs it on every push (`server-postgres` job).
- These drills use a throwaway local server. They do not replace the staging exercises in the production validation record (live DNS, provider feedback, SMTP ambiguity, monitoring).
- **Live SES feedback:** `python scripts/ses_live_drill.py` (needs an SNS topic, an SES configuration set publishing to it, a verified sender, AWS CLI credentials and ngrok; see the script's docstring). It sends only to SES mailbox-simulator addresses and checks the whole path: relay acceptance, the signed SNS subscription handshake, delivered/bounced/complained outcomes, suppressions, duplicate and forged notifications. Set `SES_CONFIGURATION_SET` on the server so outbound mail carries `X-SES-CONFIGURATION-SET`.
