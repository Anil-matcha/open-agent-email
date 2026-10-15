# @open-agent-email/sdk

TypeScript client for Open Agent Email. Zero runtime dependencies; Node 18+ (global `fetch`).

```ts
import { AgentEmailClient, constructEvent } from "@open-agent-email/sdk";

const client = new AgentEmailClient({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.OAE_KEY! });

// Send with a retry-safe key; defer delivery with scheduled_at.
await client.messages.send("support@agents.local",
  { recipient: "person@example.com", subject: "Hi", body_text: "Hello", scheduled_at: new Date(Date.now() + 3600_000) },
  { idempotencyKey: "welcome-42" });

// Iterate every unread message across pages.
for await (const m of client.messages.iterate("support@agents.local", { read_status: "unread" })) console.log(m.subject);

// Triage.
await client.messages.setLabels("support@agents.local", "msg_1", ["needs-human"]);
await client.messages.trash("support@agents.local", "msg_2");   // cancels an unsent scheduled message
```

## Resource-style names

```ts
await client.inboxes.messages.send("hello@agents.example.com", { to: "person@example.com", subject: "Hi", text: "Hello" });
await client.inboxes.messages.update("hello@agents.example.com", "msg_1", { addLabels: ["vip"] });
```

`client.messages`, `client.agents` and the rest are unchanged. See [docs/sdk-mapping.md](../../docs/sdk-mapping.md).

## Real-time inbox (no public URL)

```ts
for await (const event of client.listen("support@agents.local")) {
  if (event.type === "email.received") console.log(event.data);
}
```

Uses `globalThis.WebSocket` (Node 22+). On older Node pass one: `client.listen(inbox, { WebSocket: (await import("ws")).WebSocket as any })`. Transient disconnects reconnect with backoff; pass `signal` to stop the stream. A newer connection for the same inbox closes the older one with code `4000` and reason `replaced`; the older listener stops without reconnecting. Normal close (`1000`) and unknown application close codes (`4000`–`4999`) also stop the listener. The API key is sent as the `api_key` query parameter because WebSocket constructors cannot set headers, so use an HTTPS/WSS endpoint.

## Webhooks

```ts
// Use the RAW body, not re-serialised JSON. Pass both secrets during a rotation.
const event = constructEvent(rawBody, req.headers, [currentSecret, previousSecret]);
```

Two signature schemes are accepted, and `constructEvent` prefers the current one:

- **Current:** `Webhook-Signature: t=<unix>,v1=<hex>[,v1=<hex>]`, HMAC-SHA256 over `<t>.<raw body>`. There is one `v1` per active secret, so two appear while a rotated secret is still valid. If this header is present it must verify.
- **Legacy:** `X-Webhook-Signature` as `sha256=<hex>`, HMAC-SHA256 over `<X-Webhook-Timestamp>.<X-Webhook-Event-Id>.<raw body>`. Used only when `Webhook-Signature` is absent.

Deliveries older or newer than 5 minutes are rejected. Failures throw `WebhookSignatureError`. `signWebhookV1` and `signWebhook` expose the two signing functions for tests.

## Errors

Non-2xx responses throw `AgentEmailError` with `status` and `detail`; the token is never included in messages. Redirects are refused so credentials are never forwarded.

## Development

```bash
npm ci && npm test
```
