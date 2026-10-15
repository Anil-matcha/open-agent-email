import type { Contact, EmailTemplate, OutboundRetryEvent, RecipientSuppression, WebhookDelivery } from "./types";

// Operator-facing helpers for the event log, starter templates, template validation,
// account-wide views and outbound retry paging. Kept apart from api.ts so the shared
// client stays small.
const API_BASE = "/api/backend";

async function call<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(readError(text) || `Request failed with status ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

// The server reports `{detail, error: {message}}`; show the message rather than raw JSON.
function readError(text: string): string {
  try {
    const parsed = JSON.parse(text);
    const message = parsed?.error?.message ?? parsed?.detail;
    if (typeof message === "string") return message;
    if (Array.isArray(parsed?.detail)) return parsed.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ");
  } catch {
    // not JSON
  }
  return text;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

// --- Event log ---------------------------------------------------------------------

export type EventRecord = WebhookDelivery;

export interface EventFilters {
  agent_email?: string;
  event_type?: string;
  status?: string;
  since?: string;
  until?: string;
}

export const eventsApi = {
  list: (filters: EventFilters, limit = 50, cursor?: string | null) =>
    call<Page<EventRecord>>(`/v1/events${query({ ...filters, limit, cursor })}`),
  get: (id: string) => call<EventRecord>(`/v1/events/${encodeURIComponent(id)}`),
  redeliver: (id: string) => call<unknown>(`/v1/events/${encodeURIComponent(id)}/redeliver`, { method: "POST" }),
};

// --- Starter templates and validation ---------------------------------------------------

export interface StarterTemplateSummary {
  alias: string;
  name: string;
  description: string;
  subject: string;
}

export interface TemplatePreviewResult {
  subject: string;
  body_text: string;
  body_html: string;
  variables: string[];
}

export interface StarterTemplateDetail extends StarterTemplateSummary {
  body_text: string;
  body_html: string;
  sample_data: Record<string, unknown>;
  variables: string[];
  preview: TemplatePreviewResult;
}

export interface TemplateValidation extends TemplatePreviewResult {
  valid: boolean;
}

export const starterApi = {
  list: () => call<{ items: StarterTemplateSummary[] }>("/v1/starter-templates").then((r) => r.items),
  get: (alias: string) => call<StarterTemplateDetail>(`/v1/starter-templates/${encodeURIComponent(alias)}`),
  copyToInbox: (agentEmail: string, alias: string) =>
    call<EmailTemplate>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates/from-starter/${encodeURIComponent(alias)}`, { method: "POST" }),
  validate: (body: { subject: string; body_text: string; body_html: string; data: Record<string, unknown> }) =>
    call<TemplateValidation>("/v1/templates/validate", { method: "POST", body: JSON.stringify(body) }),
};

// --- Account-wide views ---------------------------------------------------------------

export interface AccountSuppression extends Partial<RecipientSuppression> {
  email: string;
  agent_email: string;
}

export const accountApi = {
  suppressions: (address?: string, limit = 50, cursor?: string | null) =>
    call<Page<AccountSuppression>>(`/v1/account/suppressions/page${query({ address, limit, cursor })}`),
  removeSuppression: (address: string) =>
    call<{ address: string; removed: number }>(`/v1/account/suppressions/${encodeURIComponent(address)}`, { method: "DELETE" }),
  contacts: (filters: { agent_email?: string; q?: string }, limit = 50, cursor?: string | null) =>
    call<Page<Contact>>(`/v1/contacts/page${query({ ...filters, limit, cursor })}`),
  exportAll: (limitPerCollection?: number) =>
    call<Record<string, unknown>>(`/v1/account/export${query({ limit_per_collection: limitPerCollection })}`),
};

// --- Outbound retry history ---------------------------------------------------------

export const retryApi = {
  page: (jobId: string, limit = 25, cursor?: string | null) =>
    call<Page<OutboundRetryEvent>>(`/v1/outbox/${encodeURIComponent(jobId)}/retries/page${query({ limit, cursor })}`),
};
