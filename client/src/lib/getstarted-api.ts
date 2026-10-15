import type { Agent, ApiKeyCreated, Domain, Message } from "./types";

const BASE = "/api/backend";

/** Pull a readable sentence out of the server's error JSON, falling back to the raw text. */
function readableError(text: string, status: number): string {
  try {
    const body = JSON.parse(text) as { error?: { message?: string }; detail?: unknown };
    if (body.error?.message) return body.error.message;
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : ""))
        .filter(Boolean)
        .join("; ");
    }
  } catch {
    // Not JSON; use the text below.
  }
  return text || `Request failed with status ${status}`;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!response.ok) throw new Error(readableError(await response.text(), response.status));
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export interface DomainView extends Domain {
  status_detail?: Record<"mx" | "spf" | "dkim" | "dmarc", "verified" | "missing" | "failed">;
  capabilities?: { receiving: boolean; sending: boolean };
  last_checked_at?: string | null;
}

export interface ReadinessItem {
  id: string;
  label: string;
  ok: boolean;
  severity: "required" | "recommended" | "optional";
  detail: string;
  fix: string | null;
  link: string | null;
}

export interface CreatedInbox {
  email: string;
  key: string;
  keyScope: string;
}

export type KeyScope = "full_access" | "read_only";

export const getstarted = {
  /** Public discovery call; reports the shared domain new inboxes can use. */
  health: () => call<{ shared_domain: string; version: string }>("/health"),

  /** Admin only. Callers should treat a failure as "unknown", not as "not ready". */
  readiness: () => call<{ items: ReadinessItem[]; ready: boolean }>("/v1/settings/readiness"),

  agents: () => call<Agent[]>("/v1/agents"),

  /** Creates the inbox, then an API key bound to it. If the key step fails the inbox still exists. */
  async createInbox(email: string, holdForApproval: boolean, scope: KeyScope): Promise<CreatedInbox> {
    const agent = await call<Agent>("/v1/agents", {
      method: "POST",
      body: JSON.stringify({ email, hitl_enabled: holdForApproval }),
    });
    try {
      const key = await call<ApiKeyCreated>("/v1/api-keys", {
        method: "POST",
        body: JSON.stringify({ name: "get-started", agent_email: agent.email, scope, expires_in_days: 90 }),
      });
      return { email: agent.email, key: key.key, keyScope: scope };
    } catch (e) {
      const reason = e instanceof Error ? e.message : "unknown error";
      throw new Error(`The inbox ${agent.email} was created, but its API key could not be: ${reason}. Create a key on the API Keys page.`);
    }
  },

  domains: () => call<DomainView[]>("/v1/domains"),
  createDomain: (domain: string) => call<DomainView>("/v1/domains", { method: "POST", body: JSON.stringify({ domain }) }),
  verifyDomain: (domain: string) => call<DomainView>(`/v1/domains/${encodeURIComponent(domain)}/verify`, { method: "POST" }),

  inbound: (inbox: string) =>
    call<Message[]>(`/v1/agents/${encodeURIComponent(inbox)}/messages?direction=inbound&limit=1`),

  sendTest: (inbox: string, recipient: string) =>
    call<Message>(`/v1/agents/${encodeURIComponent(inbox)}/messages`, {
      method: "POST",
      body: JSON.stringify({
        recipient,
        subject: "Hello from Open Agent Email",
        body_text: "This is a test message from your new inbox.",
      }),
    }),

  message: (inbox: string, id: string) =>
    call<Message>(`/v1/agents/${encodeURIComponent(inbox)}/messages/${encodeURIComponent(id)}`),
};
