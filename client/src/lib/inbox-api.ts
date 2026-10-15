import { Message, MessageAttachment, OutboundAttachmentInput } from "./types";

// Inbox triage helpers: trash, labels, scheduled send, lifecycle and signed links.
// They sit next to the shared client in api.ts so that file stays untouched.

const API_BASE = "/api/backend";

export type TriageMessage = Omit<Message, "status"> & {
  status: Message["status"] | "scheduled" | "canceled";
  labels?: string[];
  deleted_at?: string | null;
  scheduled_for?: string | null;
  scheduled_at?: string | null;
};

export type SignedAttachment = MessageAttachment & { download_url?: string };

export interface LifecycleEntry {
  code: string;
  at: string | null;
  detail: string;
}

export interface MessageLifecycle {
  message_id: string;
  status: string;
  entries: LifecycleEntry[];
}

/** Turn an error body from the API into one readable sentence. */
export function describeError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  try {
    const body = JSON.parse(raw);
    if (typeof body?.error?.message === "string") return body.error.message;
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // Not JSON; use the text as it came.
  }
  return raw || "Request failed.";
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!response.ok) {
    throw new Error((await response.text()) || `Request failed with status ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

const inbox = (email: string) => `/v1/agents/${encodeURIComponent(email)}`;

/** The cursor-paged list has no trash or label filter, so those views use this one. */
export function listFilteredMessages(
  email: string,
  params: { trashed?: boolean; label?: string; direction?: string; status?: string; limit?: number },
): Promise<TriageMessage[]> {
  const query = new URLSearchParams();
  if (params.trashed) query.set("trashed", "true");
  if (params.label) query.set("label", params.label);
  if (params.direction) query.set("direction", params.direction);
  if (params.status) query.set("status", params.status);
  query.set("limit", String(params.limit ?? 200));
  return call<TriageMessage[]>(`${inbox(email)}/messages?${query.toString()}`);
}

export const trashMessage = (email: string, messageId: string) =>
  call<TriageMessage>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}`, { method: "DELETE" });

export const restoreMessage = (email: string, messageId: string) =>
  call<TriageMessage>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}/restore`, { method: "POST" });

export const deleteMessagePermanently = (email: string, messageId: string) =>
  call<unknown>(
    `${inbox(email)}/messages/${encodeURIComponent(messageId)}?permanent=true&confirm=DELETE`,
    { method: "DELETE" },
  );

export const updateMessageLabels = (
  email: string,
  messageId: string,
  change: { add_labels?: string[]; remove_labels?: string[] },
) =>
  call<TriageMessage>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}`, {
    method: "PATCH",
    body: JSON.stringify(change),
  });

export const getMessageLifecycle = (email: string, messageId: string) =>
  call<MessageLifecycle>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}/lifecycle`);

/** The attachment list carries a short-lived signed `download_url` for each file. */
export const getSignedAttachments = (email: string, messageId: string) =>
  call<SignedAttachment[]>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}/attachments`);

/** Absolute form of a signed link, reachable through the dashboard's own origin. */
export function absoluteSignedLink(downloadUrl: string): string {
  return `${window.location.origin}${API_BASE}${downloadUrl}`;
}

export interface ScheduledSendBody {
  recipient: string;
  subject?: string;
  body_text?: string;
  template_id?: string;
  template_data?: Record<string, unknown>;
  attachments?: OutboundAttachmentInput[];
  scheduled_at: string;
}

export const sendScheduledMessage = (email: string, body: ScheduledSendBody) =>
  call<TriageMessage>(`${inbox(email)}/messages`, { method: "POST", body: JSON.stringify(body) });

export const replyScheduled = (
  email: string,
  messageId: string,
  body: { body_text?: string; template_id?: string; template_data?: Record<string, unknown>; scheduled_at: string },
) =>
  call<TriageMessage>(`${inbox(email)}/messages/${encodeURIComponent(messageId)}/reply`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/** Badge colours for a status, including the scheduled and canceled states. */
export function statusClasses(status: string): string {
  if (status === "delivered" || status === "accepted_by_relay") return "bg-[#238636]/10 text-[#2ea043] border-[#2ea043]/30";
  if (status === "pending_review" || status === "sending" || status === "delivery_unknown" || status === "scheduled") {
    return "bg-[#9e6a03]/10 text-[#d29922] border-[#d29922]/30";
  }
  if (status === "canceled") return "bg-[#30363d]/40 text-[#8b949e] border-[#30363d]";
  return "bg-[#da3633]/10 text-[#f85149] border-[#f85149]/30";
}
