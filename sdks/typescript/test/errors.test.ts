import assert from "node:assert/strict";
import { test } from "node:test";
import { AgentEmailClient, AgentEmailError, IdempotencyKeyReuseError, RateLimitError, type FetchLike } from "../src/index.js";

function clientFor(status: number, body: unknown, headers: Record<string, string> = {}) {
  const fetch: FetchLike = async () =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });
  return new AgentEmailClient({ baseUrl: "http://api.test", apiKey: "secret-key", fetch });
}

async function failure(client: AgentEmailClient): Promise<AgentEmailError> {
  try {
    await client.whoami();
  } catch (err) {
    assert.ok(err instanceof AgentEmailError);
    return err;
  }
  assert.fail("expected the request to fail");
}

test("structured errors expose code and details and keep detail", async () => {
  const err = await failure(clientFor(403, {
    detail: "Token is not authorized for this agent inbox",
    error: { code: "forbidden_inbox", message: "Token is not authorized for this agent inbox", details: null },
  }));
  assert.equal(err.status, 403);
  assert.equal(err.code, "forbidden_inbox");
  assert.equal(err.detail, "Token is not authorized for this agent inbox");
  assert.equal(err.details, undefined);
  assert.match(err.message, /\[forbidden_inbox\]/);
  assert.ok(!(err instanceof RateLimitError));
  assert.ok(!err.message.includes("secret-key"));
});

test("validation errors surface the field list in details", async () => {
  const fields = [{ path: "body.email", message: "Field required", type: "missing" }];
  const err = await failure(clientFor(422, {
    detail: [{ loc: ["body", "email"], msg: "Field required", type: "missing" }],
    error: { code: "validation_error", message: "Request validation failed", details: fields },
  }));
  assert.equal(err.code, "validation_error");
  assert.deepEqual(err.details, fields);
  assert.ok(Array.isArray(err.detail));
});

test("rate limits become RateLimitError with retryAfterSeconds from the body", async () => {
  const err = await failure(clientFor(429, {
    detail: "Rate limit exceeded; try again later.",
    error: { code: "rate_limited", message: "Rate limit exceeded; try again later.", details: { retry_after_seconds: 12 } },
  }, { "Retry-After": "30" }));
  assert.ok(err instanceof RateLimitError);
  assert.equal(err.name, "RateLimitError");
  assert.equal(err.retryAfterSeconds, 12);
  assert.equal(err.code, "rate_limited");
});

test("a 429 from an older server falls back to the Retry-After header", async () => {
  const err = await failure(clientFor(429, { detail: "Rate limit exceeded; try again later." }, { "Retry-After": "7" }));
  assert.ok(err instanceof RateLimitError);
  assert.equal(err.retryAfterSeconds, 7);
  assert.equal(err.code, undefined);
  assert.equal(err.detail, "Rate limit exceeded; try again later.");
});

test("idempotency key reuse has its own subclass", async () => {
  const err = await failure(clientFor(409, {
    detail: "Idempotency-Key was already used with a different request",
    error: { code: "idempotency_key_reuse", message: "Idempotency-Key was already used with a different request", details: null },
  }));
  assert.ok(err instanceof IdempotencyKeyReuseError);
  assert.equal(err.status, 409);
});

test("responses without the structured object still work (detail only)", async () => {
  const err = await failure(clientFor(404, { detail: "Agent not found" }));
  assert.equal(err.code, undefined);
  assert.equal(err.detail, "Agent not found");
  assert.match(err.message, /failed with 404: Agent not found/);
});

test("non-JSON error bodies are tolerated", async () => {
  const err = await failure(clientFor(502, "Bad gateway"));
  assert.equal(err.status, 502);
  assert.equal(err.detail, "Bad gateway");
  assert.equal(err.code, undefined);
});
