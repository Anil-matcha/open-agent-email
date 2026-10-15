/** Minimal WebSocket surface used by `listen`, satisfied by the global WebSocket and by `ws`. */
export interface WebSocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}
export type WebSocketCtor = new (url: string) => WebSocketLike;

export interface InboxEvent {
  /** e.g. `email.received`, `email.queued`, `email.sent`. */
  type: string;
  data: Record<string, unknown>;
}

export interface ListenOptions {
  baseUrl: string;
  apiKey: string;
  agentEmail: string;
  /** Inject a WebSocket implementation (defaults to `globalThis.WebSocket`). */
  WebSocket?: WebSocketCtor;
  /** Stop listening and end the iterator. */
  signal?: AbortSignal;
  /** Reconnect delay bounds; doubles up to the max. */
  reconnectMinMs?: number;
  reconnectMaxMs?: number;
  /** Keep-alive ping interval; 0 disables. */
  pingIntervalMs?: number;
  /** Called when a connection drops, before the reconnect delay. */
  onDisconnect?: (reason: string) => void;
}

export function listenUrl(baseUrl: string, agentEmail: string, apiKey: string): string {
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/v1/agents/${encodeURIComponent(agentEmail)}/ws`;
  // Browser and Node WebSocket constructors cannot set an Authorization header.
  url.searchParams.set("api_key", apiKey);
  return url.toString();
}

export async function* listen(opts: ListenOptions): AsyncGenerator<InboxEvent, void, undefined> {
  const Ctor = opts.WebSocket ?? (globalThis as { WebSocket?: WebSocketCtor }).WebSocket;
  if (!Ctor) throw new Error("No WebSocket available: use Node 22+, or pass the `WebSocket` option (e.g. from the `ws` package)");
  const min = opts.reconnectMinMs ?? 1000;
  const max = opts.reconnectMaxMs ?? 30000;
  const pingMs = opts.pingIntervalMs ?? 25000;
  let delay = min;

  while (!opts.signal?.aborted) {
    const queue: InboxEvent[] = [];
    let closed: string | null = null;
    let terminal = false;
    let wake: (() => void) | null = null;
    const notify = () => { wake?.(); wake = null; };

    const socket = new Ctor(listenUrl(opts.baseUrl, opts.agentEmail, opts.apiKey));
    let ping: ReturnType<typeof setInterval> | undefined;
    socket.onopen = () => {
      delay = min;
      if (pingMs > 0) ping = setInterval(() => { try { socket.send("ping"); } catch { /* closing */ } }, pingMs);
    };
    socket.onmessage = (ev) => {
      if (typeof ev.data !== "string" || ev.data === "pong") return;
      try {
        const parsed = JSON.parse(ev.data) as { type?: unknown; data?: unknown };
        if (typeof parsed.type === "string") {
          queue.push({ type: parsed.type, data: (parsed.data && typeof parsed.data === "object" ? parsed.data : {}) as Record<string, unknown> });
          notify();
        }
      } catch { /* ignore non-JSON frames */ }
    };
    socket.onclose = (ev) => {
      if (ev.code === 4000 && ev.reason === "replaced") { closed ??= "replaced"; terminal = true; }
      else if (typeof ev.code === "number" && ev.code >= 4000 && ev.code <= 4999) { closed ??= `terminal:${ev.code}`; terminal = true; }
      else if (ev.code === 1000) { closed ??= "normal"; terminal = true; }
      else closed ??= "closed";
      notify();
    };
    socket.onerror = () => { closed ??= "error"; notify(); };
    const onAbort = () => { closed ??= "aborted"; try { socket.close(); } catch { /* already closed */ } notify(); };
    opts.signal?.addEventListener("abort", onAbort, { once: true });

    try {
      while (true) {
        const next = queue.shift();
        if (next) { yield next; continue; }
        if (closed) break;
        await new Promise<void>((resolve) => { wake = resolve; });
      }
    } finally {
      if (ping) clearInterval(ping);
      opts.signal?.removeEventListener("abort", onAbort);
      try { socket.close(); } catch { /* already closed */ }
    }
    if (opts.signal?.aborted) return;
    opts.onDisconnect?.(closed ?? "closed");
    if (terminal) return;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, delay);
      opts.signal?.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
    });
    delay = Math.min(delay * 2, max);
  }
}
