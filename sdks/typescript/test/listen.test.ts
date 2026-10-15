import assert from "node:assert/strict";
import { test } from "node:test";
import { AgentEmailClient, listen, listenUrl, type WebSocketLike } from "../src/index.js";

class FakeSocket implements WebSocketLike {
  static instances: FakeSocket[] = [];
  onopen: WebSocketLike["onopen"] = null;
  onmessage: WebSocketLike["onmessage"] = null;
  onclose: WebSocketLike["onclose"] = null;
  onerror: WebSocketLike["onerror"] = null;
  sent: string[] = [];
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.({}));
  }
  send(data: string) { this.sent.push(data); }
  close() { this.closed = true; }
  emit(data: unknown) { this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) }); }
  drop() { this.onclose?.({}); }
}
const tick = () => new Promise((r) => setTimeout(r, 5));

test("listenUrl converts scheme, encodes the inbox and passes the key as a query param", () => {
  assert.equal(listenUrl("https://mail.test/", "a+b@x.com", "k 1"), "wss://mail.test/v1/agents/a%2Bb%40x.com/ws?api_key=k+1");
  assert.equal(listenUrl("http://localhost:8000", "a@x.com", "k"), "ws://localhost:8000/v1/agents/a%40x.com/ws?api_key=k");
});

test("yields typed events, ignores pong and malformed frames", async () => {
  FakeSocket.instances = [];
  const ac = new AbortController();
  const iterator = listen({ baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com", WebSocket: FakeSocket, signal: ac.signal, pingIntervalMs: 0 });
  const first = iterator.next();
  await tick();
  const socket = FakeSocket.instances[0]!;
  socket.emit("pong");
  socket.emit("not json");
  socket.emit({ nope: true });
  socket.emit({ type: "email.received", data: { id: "m1", subject: "Hi" } });
  socket.emit({ type: "email.sent" });
  assert.deepEqual((await first).value, { type: "email.received", data: { id: "m1", subject: "Hi" } });
  assert.deepEqual((await iterator.next()).value, { type: "email.sent", data: {} });
  ac.abort();
  assert.equal((await iterator.next()).done, true);
  assert.equal(socket.closed, true);
});

test("reconnects after a drop and reports the disconnect", async () => {
  FakeSocket.instances = [];
  const ac = new AbortController();
  const reasons: string[] = [];
  const iterator = listen({
    baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com", WebSocket: FakeSocket, signal: ac.signal,
    reconnectMinMs: 1, reconnectMaxMs: 2, pingIntervalMs: 0, onDisconnect: (r) => reasons.push(r),
  });
  const first = iterator.next();
  await tick();
  FakeSocket.instances[0]!.emit({ type: "email.received", data: { id: "1" } });
  assert.equal((await first).value?.data.id, "1");
  const second = iterator.next();
  await tick();
  FakeSocket.instances[0]!.drop();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(FakeSocket.instances.length, 2);
  FakeSocket.instances[1]!.emit({ type: "email.received", data: { id: "2" } });
  assert.equal((await second).value?.data.id, "2");
  assert.deepEqual(reasons, ["closed"]);
  ac.abort();
  await iterator.next();
});

test("does not reconnect after a newer socket replaces this connection", async () => {
  FakeSocket.instances = [];
  const reasons: string[] = [];
  const iterator = listen({
    baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com", WebSocket: FakeSocket,
    pingIntervalMs: 0, onDisconnect: (reason) => reasons.push(reason),
  });
  const pending = iterator.next();
  await tick();
  FakeSocket.instances[0]!.onclose?.({ code: 4000, reason: "replaced" });
  assert.equal((await pending).done, true);
  assert.equal(FakeSocket.instances.length, 1);
  assert.deepEqual(reasons, ["replaced"]);
});

test("sends keep-alive pings while connected", async () => {
  FakeSocket.instances = [];
  const ac = new AbortController();
  const iterator = listen({ baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com", WebSocket: FakeSocket, signal: ac.signal, pingIntervalMs: 5 });
  const pending = iterator.next();
  await new Promise((r) => setTimeout(r, 30));
  assert.ok(FakeSocket.instances[0]!.sent.filter((m) => m === "ping").length >= 2);
  ac.abort();
  await pending;
});

test("aborting before connect ends immediately", async () => {
  const ac = new AbortController();
  ac.abort();
  const iterator = listen({ baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com", WebSocket: FakeSocket, signal: ac.signal });
  assert.equal((await iterator.next()).done, true);
});

test("fails clearly when no WebSocket implementation exists", async () => {
  const original = (globalThis as { WebSocket?: unknown }).WebSocket;
  (globalThis as { WebSocket?: unknown }).WebSocket = undefined;
  try {
    await assert.rejects(listen({ baseUrl: "http://h", apiKey: "k", agentEmail: "a@x.com" }).next(), /No WebSocket available/);
  } finally {
    (globalThis as { WebSocket?: unknown }).WebSocket = original;
  }
});

test("client.listen wires base URL, key and inbox", async () => {
  FakeSocket.instances = [];
  const ac = new AbortController();
  const client = new AgentEmailClient({ baseUrl: "https://mail.test", apiKey: "key-1" });
  const pending = client.listen("a@x.com", { WebSocket: FakeSocket, signal: ac.signal, pingIntervalMs: 0 }).next();
  await tick();
  assert.equal(FakeSocket.instances[0]!.url, "wss://mail.test/v1/agents/a%40x.com/ws?api_key=key-1");
  ac.abort();
  await pending;
});
