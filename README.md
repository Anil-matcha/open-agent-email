# Open Agent Email

**Email inboxes for AI agents. Open source, self-hosted.**

Give your agents real email addresses. They can receive mail, reply in the same thread, send new messages, and wait for a person to approve anything risky, all through a simple API, ready-made SDKs, or an MCP connection for coding agents like Claude Code and Cursor.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

## What you get

- **Real inboxes.** Receive mail from anyone over standard email (SMTP) and send through your own provider such as Amazon SES, Resend, SendGrid, Postmark or Gmail.
- **Safe by default.** Suspicious mail is flagged, sensitive messages can wait for a human to approve them, and every inbox has its own API key with limited permissions.
- **Your own domain.** Add a domain and get the exact DNS records to publish, with a check that they are set up correctly.
- **Works with your agent.** REST API, [Python](sdks/python) and [TypeScript](sdks/typescript) SDKs, a command-line tool, live streaming of new mail, webhooks, and an MCP server.
- **A dashboard** for reading mail, approving held messages, and managing keys, domains and webhooks.

## Get started

You need Python 3.11 or newer.

```bash
git clone https://github.com/Anil-matcha/open-agent-email.git
cd open-agent-email
./scripts/dev.sh
```

That starts the server at `http://127.0.0.1:8000`, creates the secrets it needs, and prints your admin key. Then follow the **[quickstart](docs/quickstart.md)** to create an inbox, receive and send your first email, and connect an agent. It takes about five minutes.

To run the dashboard as well:

```bash
cd client && npm install && npm run dev   # http://localhost:3000, sign in with the admin key
```

Prefer Docker?

```bash
docker compose up -d --build
docker compose logs server | grep "Admin key"   # printed once on first start
```

Secrets are generated on first start and kept in a Docker volume (see the [quickstart](docs/quickstart.md) for backing it up). The dashboard is on port 3000, the API on 8000, and the mail listener on 2525 (map it to 25 to receive mail from the internet).

## Use it from code

```python
from open_agent_email import AgentEmailClient

client = AgentEmailClient("http://127.0.0.1:8000", api_key="oae_...")
for message in client.list_messages("hello@agents.example.com", direction="inbound"):
    print(message["sender"], message["subject"])
```

```ts
import { AgentEmailClient } from "@open-agent-email/sdk";

const client = new AgentEmailClient({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.OAE_API_KEY! });
await client.messages.send("hello@agents.example.com", { recipient: "you@example.com", subject: "Hi", body_text: "Hello" });
```

Connect Claude Code (Cursor and other clients are in the [quickstart](docs/quickstart.md#connect-a-coding-agent-over-mcp)):

```bash
claude mcp add --transport http open-agent-email http://127.0.0.1:8000/mcp/rpc \
  --header "Authorization: Bearer $OAE_API_KEY"
```

## Documentation

| | |
|---|---|
| [Quickstart](docs/quickstart.md) | First inbox, first email, SDKs and MCP |
| [Reference](docs/reference.md) | Settings, webhooks, review and approval flow, domains, the MCP tool list, scheduling, templates and more |
| [Operations](docs/operations.md) | Running it in production: secrets, backups, monitoring, upgrades |
| [Errors](docs/errors.md) | Every error code and how to fix it |
| API explorer | `http://127.0.0.1:8000/docs` on a running server |

## Status

Open Agent Email is under active development. It is well tested locally, but the mail path has had limited testing against live providers, so read [Operations](docs/operations.md) before putting it in front of real traffic.

## Tests

```bash
cd server && pip install -r requirements-dev.txt && python -m pytest -q
```

## License

Apache 2.0.
