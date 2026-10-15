# Quickstart: first inbox, first sent and received email

About five minutes. You need Python 3.11 or newer (or `uv`) and nothing else.

## 1. Start the server

```bash
git clone https://github.com/Anil-matcha/open-agent-email.git
cd open-agent-email
./scripts/dev.sh
```

The script creates a virtualenv, generates the required secrets on first run (saved in `server/.env`, which is git-ignored, so back it up), prints your admin key, and starts the API on `http://127.0.0.1:8000` and the SMTP listener on `127.0.0.1:2525`.

In a second terminal, keep the admin key handy:

```bash
export OAE_ADMIN_KEY="$(grep '^ADMIN_API_KEY=' server/.env | cut -d= -f2-)"
export OAE_URL=http://127.0.0.1:8000
```

### Or start with Docker

```bash
docker compose up -d --build
docker compose logs server | grep "Admin key"
```

No variables need to be set. On first start the server generates an admin key and an encryption key, stores both in the `oae-data` volume (never regenerated), and prints the admin key once in its log. Open the dashboard at `http://localhost:3000`, sign in with that key, and the first-run guide walks you through creating an inbox and its API key. Values you provide yourself (`ADMIN_API_KEY`, `SMTP_CONFIG_ENCRYPTION_KEY`) are used as given and are not written to the volume.

Back up the `oae-data` volume: it holds the database and the encryption key that protects stored credentials. If you ran an earlier version that kept the database in `./server/open_agent_email.db`, copy that file into the volume before upgrading, or the stack will start with an empty database.

For the steps below, use `http://127.0.0.1:8000` and that admin key (`export OAE_ADMIN_KEY=...`).

## 2. Create an inbox and an API key

`display_name` is optional and defaults to the part before the `@`. Local examples use `agents.example.com`; addresses under reserved names such as `.local` are rejected as recipients.

```bash
curl -s -X POST $OAE_URL/v1/agents \
  -H "Authorization: Bearer $OAE_ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"email":"hello@agents.example.com","hitl_enabled":false}'

curl -s -X POST $OAE_URL/v1/api-keys \
  -H "Authorization: Bearer $OAE_ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"name":"hello-key","agent_email":"hello@agents.example.com"}'
```

The second response contains the key once (`oae_...`). Save it:

```bash
export OAE_API_KEY=oae_...
```

`hitl_enabled` is on by default, which holds suspicious or outbound mail for human review; it is switched off here so the first message flows straight through. Leave it on for real agents.

## 3. Receive an email

Anything that speaks SMTP can deliver to the listener. This sends one as an outside sender (use a sender domain without a strict DMARC policy; a domain publishing `p=reject` is refused with 550, which is the intended behaviour):

```bash
python3 - <<'PY'
import smtplib
from email.message import EmailMessage
m = EmailMessage()
m["From"], m["To"], m["Subject"] = "person@sender.test", "hello@agents.example.com", "Hi from outside"
m.set_content("Hello agent")
with smtplib.SMTP("127.0.0.1", 2525) as s:
    s.send_message(m)
PY

curl -s $OAE_URL/v1/agents/hello@agents.example.com/messages \
  -H "Authorization: Bearer $OAE_API_KEY"
```

## 4. Send an email

Sending to an outside address needs an upstream SMTP relay (see "Upstream SMTP Relay" in [reference.md](reference.md)); without one the message is recorded as `failed` with a clear reason.

```bash
curl -s -X POST $OAE_URL/v1/agents/hello@agents.example.com/messages \
  -H "Authorization: Bearer $OAE_API_KEY" -H 'content-type: application/json' \
  -H 'Idempotency-Key: first-send-1' \
  -d '{"recipient":"you@yourdomain.com","subject":"Hello","body_text":"First message"}'
```

### Send between two local inboxes

Mail between two inboxes on the same server needs no relay. It is stored in the recipient's inbox like any other inbound message, goes through the same threat screening and review rules, and is marked as locally delivered rather than DNS-authenticated.

```bash
curl -s -X POST $OAE_URL/v1/agents \
  -H "Authorization: Bearer $OAE_ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"email":"friend@agents.example.com","hitl_enabled":false}'

curl -s -X POST $OAE_URL/v1/agents/hello@agents.example.com/messages \
  -H "Authorization: Bearer $OAE_API_KEY" -H 'content-type: application/json' \
  -d '{"recipient":"friend@agents.example.com","subject":"Hello","body_text":"First message"}'

curl -s $OAE_URL/v1/agents/friend@agents.example.com/messages \
  -H "Authorization: Bearer $OAE_ADMIN_KEY"
```

## The same thing from code

Python (`pip install ./sdks/python`):

```python
from open_agent_email import AgentEmailClient

client = AgentEmailClient("http://127.0.0.1:8000", api_key="oae_...")
for m in client.list_messages("hello@agents.example.com", direction="inbound"):
    print(m["sender"], m["subject"])
```

TypeScript (`@open-agent-email/sdk`, built from `./sdks/typescript`):

```ts
import { AgentEmailClient } from "@open-agent-email/sdk";

const client = new AgentEmailClient({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.OAE_API_KEY! });
for await (const m of client.messages.iterate("hello@agents.example.com", { read_status: "unread" })) {
  console.log(m.sender, m.subject);
}
```

Command line:

```bash
pip install ./sdks/python
oae login            # saves the server URL and key
oae messages list --agent hello@agents.example.com
oae messages send --agent hello@agents.example.com --to you@yourdomain.com --subject Hello --body "First message"
```

## Connect a coding agent over MCP

The server exposes MCP at `http://127.0.0.1:8000/mcp/rpc` and authenticates with the same bearer key.

Claude Code:

```bash
claude mcp add --transport http open-agent-email http://127.0.0.1:8000/mcp/rpc \
  --header "Authorization: Bearer $OAE_API_KEY"
```

Cursor (`.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "open-agent-email": {
      "url": "http://127.0.0.1:8000/mcp/rpc",
      "headers": { "Authorization": "Bearer ${env:OAE_API_KEY}" }
    }
  }
}
```

## Next

The dashboard (`cd client && npm install && npm run dev`, then sign in with the admin key), custom domains and DNS, webhooks and the review queue are covered in [reference.md](reference.md) and [operations.md](operations.md).
