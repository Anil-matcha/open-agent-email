# SDK naming guide

The SDKs offer two equivalent styles. The original flat names (`send_message`, `client.messages.send`) are unchanged and fully supported. The resource-style names below nest everything under an inbox and use shorter argument names. The REST API accepts the same short field names, so curl, the SDKs and the CLI can all use them.

## Request fields

| Short form (accepted everywhere) | Canonical field | Where |
|---|---|---|
| `to` | `recipient` | send, forward |
| `text` | `body_text` | send, reply |
| `html` | `body_html` | send, reply |
| `page_token` | `cursor` | message and thread page routes |
| `add_labels`, `remove_labels` | `labels` (replaces the set) | message update |
| `event_types` | `events` | webhook create (TypeScript SDK) |

If both forms are sent they must agree, otherwise the request is rejected with 422.

`reply_all` is accepted on reply for client compatibility, but messages have a single recipient, so `true` is rejected with the code `reply_all_unsupported`.

## Python

| Resource style | Flat method |
|---|---|
| `client.inboxes.create(email, display_name=None)` | `create_agent` |
| `client.inboxes.get(inbox)` | `get_agent` |
| `client.inboxes.list(limit, page_token)` / `.iterate()` | `get_agents_page` / `iter_agents` |
| `client.inboxes.messages.send(inbox, to=, subject=, text=, html=)` | `send_message` |
| `client.inboxes.messages.list(inbox, limit=, page_token=)` (one page) | `get_message_page` |
| `client.inboxes.messages.iterate(inbox, ...)` | `iter_messages` |
| `client.inboxes.messages.get(inbox, id)` | `get_message` |
| `client.inboxes.messages.reply(inbox, id, text=, html=)` | `reply_message` |
| `client.inboxes.messages.forward(inbox, id, to=, note=)` | `forward_message` |
| `client.inboxes.messages.update(inbox, id, add_labels=, remove_labels=)` or `labels=` | `update_message_labels` (replace only) |
| `client.inboxes.messages.trash / restore / mark_read` | `trash_message`, `restore_message`, `mark_message_read` |
| `client.inboxes.threads.list / iterate / get` | `get_conversations_page`, `iter_conversations`, `get_conversation` |
| `client.webhooks.list / create(inbox, url=, event_types=) / test / rotate_secret / delete / deliveries` | `list_webhooks` and related |
| `client.api_keys.list / create(name, inbox=) / delete` | `list_account_api_keys`, `create_account_api_key`, `revoke_account_api_key` |

## TypeScript

| Resource style | Existing form |
|---|---|
| `client.inboxes.list / iterate / get / create` | `client.agents.*` |
| `client.inboxes.messages.send(inbox, { to, subject, text, html })` | `client.messages.send(inbox, { recipient, subject, body_text, body_html })` |
| `client.inboxes.messages.list(inbox, { limit, pageToken, query })` (one page) | `client.messages.page` |
| `client.inboxes.messages.reply(inbox, id, { text, html })` | `client.messages.reply` |
| `client.inboxes.messages.forward(inbox, id, { to, note })` | `client.messages.forward(inbox, id, recipient, note)` |
| `client.inboxes.messages.update(inbox, id, { addLabels, removeLabels })` or `{ labels }` | `client.messages.setLabels` (replace only) |
| `client.inboxes.threads.list / iterate / get` | `client.conversations.*` |
| `client.webhooks.create({ agent_email, url, event_types })` | same call with `events` |

## Errors

Both SDKs expose `fix` and `docs` on API errors when the server includes them, next to the existing `code`, `details`, `status` and retry hints. The server does not send them yet, so they are empty today.

## Command line

`oae auth login` stores the key in the macOS keychain when `security` is available and falls back to a 0600 config file otherwise (`--file` forces the file); `oae auth status` shows where the credential comes from and whether it works, without printing it; `oae auth logout` removes it. `oae messages send --dry-run` (and `reply`) prints the request that would be sent and sends nothing.
