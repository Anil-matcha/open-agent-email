import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import { constructEvent, signWebhook, WebhookSignatureError } from "../src/index.js";

const NOW = 1_800_000_000;
const BODY = JSON.stringify({ type: "email.received", message_id: "msg_1" });

// Independent reimplementation of the server's scheme (HMAC-SHA256 over "<ts>.<event id>.<body>").
const serverSign = (secret: string, ts: number, id: string, body: string) =>
  "sha256=" + createHmac("sha256", secret).update(Buffer.from(`${ts}.${id}.`)).update(Buffer.from(body)).digest("hex");

function headers(over: Record<string, string> = {}, secret = "whsec_a", ts = NOW, id = "evt_1", body = BODY) {
  return {
    "X-Webhook-Event-Id": id, "X-Webhook-Timestamp": String(ts), "X-Webhook-Event": "email.received",
    "X-Webhook-Signature": serverSign(secret, ts, id, body), ...over,
  };
}

test("signWebhook matches the server scheme", () => {
  assert.equal(signWebhook("whsec_a", NOW, "evt_1", BODY), serverSign("whsec_a", NOW, "evt_1", BODY));
});

test("accepts a valid delivery and parses it", () => {
  const event = constructEvent<{ message_id: string }>(BODY, headers(), "whsec_a", { now: NOW });
  assert.equal(event.id, "evt_1");
  assert.equal(event.type, "email.received");
  assert.equal(event.timestamp, NOW);
  assert.equal(event.payload.message_id, "msg_1");
});

test("accepts raw bytes and a Headers instance", () => {
  const h = new Headers(headers());
  assert.equal(constructEvent(new TextEncoder().encode(BODY), h, "whsec_a", { now: NOW }).id, "evt_1");
});

test("header names are case-insensitive", () => {
  const lower = Object.fromEntries(Object.entries(headers()).map(([k, v]) => [k.toLowerCase(), v]));
  assert.ok(constructEvent(BODY, lower, "whsec_a", { now: NOW }));
});

test("rejects a tampered body", () => {
  assert.throws(() => constructEvent(BODY.replace("msg_1", "msg_2"), headers(), "whsec_a", { now: NOW }), WebhookSignatureError);
});

test("rejects a tampered event id or timestamp", () => {
  assert.throws(() => constructEvent(BODY, headers({ "X-Webhook-Event-Id": "evt_2" }), "whsec_a", { now: NOW }), /does not match/);
  assert.throws(() => constructEvent(BODY, headers({ "X-Webhook-Timestamp": String(NOW + 1) }), "whsec_a", { now: NOW }), /does not match/);
});

test("rejects the wrong secret", () => {
  assert.throws(() => constructEvent(BODY, headers(), "whsec_other", { now: NOW }), /does not match/);
});

test("rejects stale and future timestamps beyond the 5 minute window", () => {
  const old = NOW - 301;
  assert.throws(() => constructEvent(BODY, headers({}, "whsec_a", old), "whsec_a", { now: NOW }), /window/);
  const future = NOW + 301;
  assert.throws(() => constructEvent(BODY, headers({}, "whsec_a", future), "whsec_a", { now: NOW }), /window/);
});

test("accepts a timestamp exactly at the tolerance edge and honours a custom tolerance", () => {
  const edge = NOW - 300;
  assert.ok(constructEvent(BODY, headers({}, "whsec_a", edge), "whsec_a", { now: NOW }));
  const slightly = NOW - 20;
  assert.throws(() => constructEvent(BODY, headers({}, "whsec_a", slightly), "whsec_a", { now: NOW, toleranceSeconds: 10 }), /window/);
});

test("rotation: server sends both signatures; either secret verifies", () => {
  const both = `${serverSign("whsec_new", NOW, "evt_1", BODY)},${serverSign("whsec_old", NOW, "evt_1", BODY)}`;
  const h = headers({ "X-Webhook-Signature": both });
  assert.ok(constructEvent(BODY, h, "whsec_new", { now: NOW }));
  assert.ok(constructEvent(BODY, h, "whsec_old", { now: NOW }));
  assert.ok(constructEvent(BODY, h, ["whsec_unrelated", "whsec_old"], { now: NOW }));
  assert.throws(() => constructEvent(BODY, h, ["whsec_unrelated"], { now: NOW }), /does not match/);
});

test("rotation: a consumer holding only the old secret verifies pre-rotation deliveries", () => {
  assert.ok(constructEvent(BODY, headers({}, "whsec_old"), ["whsec_new", "whsec_old"], { now: NOW }));
});

test("rejects missing headers, bad timestamps, empty secrets and non-JSON bodies", () => {
  const { "X-Webhook-Signature": _drop, ...noSig } = headers();
  assert.throws(() => constructEvent(BODY, noSig, "whsec_a", { now: NOW }), /Missing/);
  assert.throws(() => constructEvent(BODY, headers({ "X-Webhook-Timestamp": "12abc" }), "whsec_a", { now: NOW }), /Invalid webhook timestamp/);
  assert.throws(() => constructEvent(BODY, headers(), [], { now: NOW }), /No signing secret/);
  assert.throws(() => constructEvent(BODY, headers(), "", { now: NOW }), /No signing secret/);
  const bad = "not json";
  assert.throws(() => constructEvent(bad, headers({}, "whsec_a", NOW, "evt_1", bad), "whsec_a", { now: NOW }), /valid JSON/);
});

test("malformed or truncated signature values never match", () => {
  for (const sig of ["", "sha256=", "sha256=abc", "garbage,,", serverSign("whsec_a", NOW, "evt_1", BODY).slice(0, -2)]) {
    assert.throws(() => constructEvent(BODY, headers({ "X-Webhook-Signature": sig }), "whsec_a", { now: NOW }), WebhookSignatureError);
  }
});
