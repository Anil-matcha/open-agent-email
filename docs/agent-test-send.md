# Agent inbox delivery check

`POST /v1/agents/{email}/test` queues a platform-originated test email to the
agent's own email address. The worker sends it through the configured external
SMTP relay, including when that address belongs to an inbox hosted by this
service. This checks the public inbound delivery path instead of creating a
local inbox loopback.

The endpoint accepts no request body. It requires a full-access credential (or
an administrator credential), uses the same attachment scanner boundary as
other queued sends, and accepts an optional
`Idempotency-Key` header. Reusing that key for the same inbox returns the
original result; reusing it for a different request is rejected.

Successful queueing returns HTTP `202` with `message_id` and one of these
statuses:

- `accepted`: durably queued for external SMTP submission. The eventual result
  can be read from the message endpoint or received as an email status event.
- `pending_review`: held by the inbox's human approval policy. It is not sent
  until approved.

The platform sender uses the configured SMTP `from_email`, falling back to
`noreply@open-agent-email.dev`. A `202` response means the queue accepted the
message; it does not confirm final inbox delivery.
