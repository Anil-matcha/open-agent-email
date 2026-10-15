import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { constructEvent, signWebhook, signWebhookV1, WebhookSignatureError } from "../src/index.js";

interface Case {
  name: string;
  timestamp: string;
  event_id: string;
  event_type: string;
  secrets: string[];
  body: string;
  expected_headers: Record<string, string>;
}

// Shared with the server and Python tests; compiled tests run from dist/test.
const fixturePath = fileURLToPath(new URL("../../../../fixtures/webhook_signatures.json", import.meta.url));
const cases: Case[] = JSON.parse(readFileSync(fixturePath, "utf8")).cases;
const NOW = (c: Case) => Number(c.timestamp);

for (const c of cases) {
  test(`golden: signing helpers reproduce the fixture (${c.name})`, () => {
    const v1 = c.secrets.map((s) => `v1=${signWebhookV1(s, c.timestamp, c.body)}`).join(",");
    assert.equal(`t=${c.timestamp},${v1}`, (c.expected_headers["Webhook-Signature"] as string));
    const legacy = c.secrets.map((s) => signWebhook(s, c.timestamp, c.event_id, c.body)).join(",");
    assert.equal(legacy, (c.expected_headers["X-Webhook-Signature"] as string));
  });

  test(`golden: independent HMAC agrees with the fixture (${c.name})`, () => {
    const mac = createHmac("sha256", c.secrets[0] as string).update(`${c.timestamp}.${c.body}`).digest("hex");
    assert.ok((c.expected_headers["Webhook-Signature"] as string).includes(`v1=${mac}`));
  });

  test(`golden: constructEvent verifies the full header set for every secret (${c.name})`, () => {
    for (const secret of c.secrets) {
      const event = constructEvent<{ id: string }>(c.body, c.expected_headers, secret, { now: NOW(c) });
      assert.equal(event.id, c.event_id);
      assert.equal(event.type, c.event_type);
      assert.equal(event.timestamp, NOW(c));
    }
  });

  test(`golden: either header set alone is enough (${c.name})`, () => {
    const legacyOnly = Object.fromEntries(Object.entries(c.expected_headers).filter(([k]) => !k.startsWith("Webhook-")));
    const currentOnly = { "Webhook-Signature": (c.expected_headers["Webhook-Signature"] as string) };
    for (const headers of [legacyOnly, currentOnly]) {
      assert.equal(constructEvent(c.body, headers, c.secrets[0] as string, { now: NOW(c) }).id, c.event_id);
    }
  });
}

const c0 = cases[0] as Case;
const SECRET0 = c0.secrets[0] as string;
const SIG0 = c0.expected_headers["Webhook-Signature"] as string;
const BODY = c0.body;
const headersFor = (over: Record<string, string> = {}) => ({ ...c0.expected_headers, ...over });

test("the current header is preferred: a bad legacy header does not matter when it verifies", () => {
  const h = headersFor({ "X-Webhook-Signature": "sha256=" + "0".repeat(64) });
  assert.ok(constructEvent(BODY, h, SECRET0, { now: NOW(c0) }));
});

test("a present but invalid current header is an error even when the legacy header is valid", () => {
  const h = headersFor({ "Webhook-Signature": `t=${c0.timestamp},v1=${"0".repeat(64)}` });
  assert.throws(() => constructEvent(BODY, h, SECRET0, { now: NOW(c0) }), /does not match/);
});

test("current scheme: tampered body, wrong secret and tampered timestamp are rejected", () => {
  const only = { "Webhook-Signature": SIG0 };
  assert.throws(() => constructEvent(BODY.replace("msg_abc", "msg_xyz"), only, SECRET0, { now: NOW(c0) }), WebhookSignatureError);
  assert.throws(() => constructEvent(BODY, only, "whsec_other", { now: NOW(c0) }), /does not match/);
  const moved = { "Webhook-Signature": SIG0.replace(`t=${c0.timestamp}`, `t=${NOW(c0) + 1}`) };
  assert.throws(() => constructEvent(BODY, moved, SECRET0, { now: NOW(c0) }), /does not match/);
});

test("current scheme: five minute window, edge inclusive", () => {
  const only = { "Webhook-Signature": SIG0 };
  assert.ok(constructEvent(BODY, only, SECRET0, { now: NOW(c0) + 300 }));
  assert.throws(() => constructEvent(BODY, only, SECRET0, { now: NOW(c0) + 301 }), /window/);
  assert.throws(() => constructEvent(BODY, only, SECRET0, { now: NOW(c0) - 301 }), /window/);
});

test("current scheme: dual-secret rotation verifies with either secret and a list", () => {
  const both = cases.find((c) => c.secrets.length === 2)!;
  const only = { "Webhook-Signature": (both.expected_headers["Webhook-Signature"] as string) };
  for (const secret of both.secrets) assert.ok(constructEvent(both.body, only, secret, { now: NOW(both) }));
  assert.ok(constructEvent(both.body, only, ["whsec_unrelated", both.secrets[1] as string], { now: NOW(both) }));
  assert.throws(() => constructEvent(both.body, only, ["whsec_unrelated"], { now: NOW(both) }), /does not match/);
});

test("current scheme: malformed headers are rejected", () => {
  for (const value of ["", "garbage", "t=abc,v1=00", `v1=${"a".repeat(64)}`, `t=${c0.timestamp}`, `t=${c0.timestamp},v2=${"a".repeat(64)}`]) {
    assert.throws(() => constructEvent(BODY, { "Webhook-Signature": value }, SECRET0, { now: NOW(c0) }), WebhookSignatureError, value);
  }
});

test("current scheme: unknown keys in the header are ignored", () => {
  const v1 = SIG0.split(",")[1] as string;
  const h = { "Webhook-Signature": `t=${c0.timestamp},x=1,${v1},future=ok` };
  assert.ok(constructEvent(BODY, h, SECRET0, { now: NOW(c0) }));
});

test("current scheme works with a Headers instance and falls back to the body for id and type", () => {
  const h = new Headers({ "Webhook-Signature": SIG0 });
  const event = constructEvent(BODY, h, SECRET0, { now: NOW(c0) });
  assert.equal(event.id, c0.event_id);
  assert.equal(event.type, "email.received");
});
