import assert from "node:assert/strict";
import { test } from "node:test";
import { AgentEmailClient, type FetchLike } from "../src/index.js";

function mock(json: unknown = {}) {
  const calls: Array<{ url: URL; method: string; body: unknown }> = [];
  const fetch: FetchLike = async (input, init) => {
    calls.push({ url: new URL(input), method: String(init?.method), body: init?.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(json), { status: 200 });
  };
  return { client: new AgentEmailClient({ baseUrl: "http://api.test", apiKey: "k", fetch }), calls };
}

test("an account-level webhook is created without an inbox", async () => {
  const { client, calls } = mock({ id: "wh_1" });
  await client.webhooks.createAccount({ url: "https://example.com/h", event_types: ["email.failed"], inboxes: ["a@x.com"] });
  assert.equal(calls[0]!.method, "POST");
  assert.deepEqual(calls[0]!.body, { url: "https://example.com/h", inboxes: ["a@x.com"], events: ["email.failed"] });
});

test("a per-inbox webhook still sends its inbox", async () => {
  const { client, calls } = mock({ id: "wh_1" });
  await client.webhooks.create({ agent_email: "a@x.com", url: "https://example.com/h" });
  assert.deepEqual(calls[0]!.body, { agent_email: "a@x.com", url: "https://example.com/h" });
});

test("list and page filter by account level", async () => {
  const { client, calls } = mock([]);
  await client.webhooks.list(undefined, 10, true);
  assert.equal(calls[0]!.url.searchParams.get("account_level"), "true");
  assert.equal(calls[0]!.url.searchParams.has("agent_email"), false);
  await client.webhooks.page({ account_level: false, limit: 5 });
  assert.equal(calls[1]!.url.searchParams.get("account_level"), "false");
});

test("update can clear the inbox filter with null and leave it alone when omitted", async () => {
  const { client, calls } = mock({ id: "wh_1" });
  await client.webhooks.update("wh_1", { inboxes: null });
  assert.deepEqual(calls[0]!.body, { inboxes: null });
  await client.webhooks.update("wh_1", { enabled: false });
  assert.deepEqual(calls[1]!.body, { enabled: false });
});

test("lifecycle exposes the persisted entries", async () => {
  const { client } = mock({ message_id: "m1", status: "delivered", events: [], entries: [{ code: "created", at: null, detail: "" }] });
  const life = await client.messages.lifecycle("a@x.com", "m1");
  assert.equal(life.entries[0]!.code, "created");
});
