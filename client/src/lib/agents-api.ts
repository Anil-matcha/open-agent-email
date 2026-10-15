// Inbox-management calls used by the Agents page. Kept separate from api.ts so the two can evolve independently.
import type { Agent } from "./types";

const API_BASE = "/api/backend";

/** Turns an error response body into a short message: prefers the structured error, falls back to the raw text. */
function readableError(text: string, status: number): string {
  try {
    const body = JSON.parse(text);
    const message = body?.error?.message ?? (typeof body?.detail === "string" ? body.detail : null);
    const fix = body?.error?.fix;
    if (message) return fix ? `${message} (${fix})` : message;
  } catch {
    /* not JSON */
  }
  return text || `Request failed with status ${status}`;
}

async function call<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error(readableError(await response.text(), response.status));
  if (response.status === 204) return undefined as T;
  return response.json();
}

const enc = encodeURIComponent;

export type Sensitivity = "off" | "low" | "medium" | "high";
export type ExpiryAction = "approve" | "reject";

export interface Protection {
  threat_sensitivity: Sensitivity;
  hitl_enabled: boolean;
}

export interface ReviewSettings {
  review_notify_email: string | null;
  review_expiry_hours: number | null;
  review_expiry_action: ExpiryAction;
}

export interface ProtectionUpdate extends Protection {
  review_notify_email?: string | null;
  review_expiry_hours?: number | null;
  review_expiry_action?: ExpiryAction;
}

export interface InboxMetrics {
  agent_email: string;
  messages: { inbound: number; outbound: number };
  by_status: { inbound: Record<string, number>; outbound: Record<string, number> };
  unread: number;
  pending_reviews: number;
}

export interface TestSendResult {
  status: "accepted" | "pending_review";
  message_id: string;
}

export interface MessageState {
  id: string;
  status: string;
  error?: string | null;
  delivery_error?: string | null;
}

export type DeletedAgent = Agent & { deleted_at: string };

export const agentsApi = {
  getProtection: (email: string) => call<Protection>(`/v1/agents/${enc(email)}/protection`),
  getReviewSettings: (email: string) => call<ReviewSettings>(`/v1/agents/${enc(email)}/protection/review`),
  updateProtection: (email: string, body: ProtectionUpdate) =>
    call<Protection>(`/v1/agents/${enc(email)}/protection`, { method: "PUT", body: JSON.stringify(body) }),

  getMetrics: (email: string) => call<InboxMetrics>(`/v1/agents/${enc(email)}/metrics`),

  sendTest: (email: string) => call<TestSendResult>(`/v1/agents/${enc(email)}/test`, { method: "POST" }),
  getMessage: (email: string, messageId: string) =>
    call<MessageState>(`/v1/agents/${enc(email)}/messages/${enc(messageId)}`),

  listDeleted: async (): Promise<DeletedAgent[]> => {
    const all = await call<Agent[]>("/v1/agents?include_deleted=true");
    return all.filter((a): a is DeletedAgent => Boolean((a as DeletedAgent).deleted_at));
  },
  restore: (email: string) => call<Agent>(`/v1/agents/${enc(email)}/restore`, { method: "POST" }),
  purge: (email: string) => call<void>(`/v1/agents/${enc(email)}?purge=true`, { method: "DELETE" }),
};
