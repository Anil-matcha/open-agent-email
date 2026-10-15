# Dashboard coverage

What the REST API can do compared with what the dashboard (`client/`) lets an operator do. Everything listed as missing is available through the API, the SDKs, the `oae` command line, or MCP; the dashboard simply has no screen for it yet.

How this was checked: every `/v1` operation in `api/openapi.snapshot.json` was compared with the request paths in `client/src`, then the doubtful ones were searched by hand. Findings reflect the code at the time of writing, not a browser walkthrough, so a screen that builds its URLs in an unusual way could be misreported.

## Covered

| Area | Screens |
|---|---|
| Inboxes | Create, edit threat sensitivity and approval hold, delete (Agents) |
| Messages | List, search, filter by read and delivery status, open, mark read, reply, forward, compose, attachments (Inbox) |
| Review queue | List, approve, reject (Reviews) |
| Contacts and follow-ups | Contacts, CSV import and reversal, consent history, follow-ups (Contacts, Engagements) |
| Templates and suppressions | Template CRUD and preview, recipient suppressions (Templates, Suppressions) |
| Sending | Outbound queue, history, manual retry with duplicate-risk confirmation (Outbox); relay settings and connection test (Settings) |
| Domains | Register, DNS records, verify (Domains) |
| Webhooks | Endpoints, secrets, test event, delivery history, replay (Webhooks) |
| API keys | Create, revoke (API Keys) |
| Overview | Totals and account metrics (home page) |
| MCP | Setup snippets (MCP) |

## Not in the dashboard

Ordered by how likely an operator is to want it.

| Capability | API | Notes |
|---|---|---|
| Trash, restore, permanent delete, labels | `DELETE`/`POST .../messages/{id}/restore`, labels update | The inbox has no trash view or label editor |
| Scheduled send | `scheduled_at` on send and reply | The composer cannot pick a send time |
| Conversations and message lifecycle | `/conversations`, `/messages/{id}/lifecycle` | Threads exist in the inbox, but there is no lifecycle timeline |
| Inbox protection settings | `GET`/`PUT /protection`, `/protection/review` | Sensitivity and hold are set at create or edit time only; review-notification settings have no screen |
| Deleted inboxes | `include_deleted`, `POST /agents/{email}/restore` | Deleted inboxes cannot be listed or restored |
| Event log | `/v1/events`, `/v1/events/{id}`, redeliver | Webhook deliveries are visible, the underlying event log is not |
| Account export and account-wide views | `/v1/account/export`, `/account/suppressions`, `/v1/contacts` | Per-inbox views exist; the cross-inbox ones do not |
| Starter templates | `/v1/starter-templates`, `from-starter/{alias}`, `/v1/templates/validate` | Templates are written from scratch |
| Per-inbox metrics | `GET /agents/{email}/metrics` | Only the account-level numbers are shown |
| Test message to an inbox | `POST /agents/{email}/test` | Only the first-run guide sends a test |
| Retry history for an outbound job | `/outbox/{job}/retries/page` | The queue shows state but not the retry log |
| Signed attachment download links | `/attachments/{message}/{attachment}/download` | Attachments are listed in the inbox |

## Suggested order

1. Trash, restore and labels in the inbox, since mail triage is the main daily task.
2. Scheduled send in the composer.
3. An event log page next to webhooks.
4. Protection and review-notification settings on the agent page.
5. Starter templates in the template picker.

None of these were built in this change because each needs a screen design and a browser pass, and the checks available here were type checks only.
