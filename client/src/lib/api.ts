import {
  Agent,
  Contact,
  ContactConsentHistory,
  ContactEngagement,
  ContactImportResult,
  ContactImportBatch,
  EmailTemplate,
  EmailTemplatePreview,
  RecipientSuppression,
  Message,
  MessagePage,
  MessageAttachment,
  OutboundAttachmentInput,
  ReviewItem,
  Stats,
  AccountMetrics,
  Domain,
  SmtpConfig,
  ApiKeyItem,
  ApiKeyCreated,
  OutboundJob,
  OutboundJobPage,
  OutboundJobStatus,
  OutboundRetryEvent,
  WebhookEndpoint,
  WebhookEndpointCreated,
  WebhookEndpointSecretRotated,
  WebhookDelivery,
  WebhookDeliveryStatus,
  WebhookDeliveryPage,
  WebhookTestResult
} from "./types";

const API_BASE = "/api/backend";
type MessageStatus = "delivered" | "accepted_by_relay" | "sending" | "delivery_unknown" | "pending_review" | "rejected" | "blocked" | "failed";

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `Request failed with status ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  // Stats
  getStats: () => request<Stats>("/v1/stats"),
  getAccountMetrics: () =>
    request<AccountMetrics>("/v1/metrics?bucket=day&group_by=agent"),

  // Agents
  getAgents: () => request<Agent[]>("/v1/agents"),
  getAgent: (email: string) => request<Agent>(`/v1/agents/${encodeURIComponent(email)}`),
  createAgent: (data: Partial<Agent>) =>
    request<Agent>("/v1/agents", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateAgent: (email: string, data: Partial<Agent>) =>
    request<Agent>(`/v1/agents/${encodeURIComponent(email)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteAgent: (email: string) =>
    request<void>(`/v1/agents/${encodeURIComponent(email)}`, {
      method: "DELETE",
    }),

  // Inbox-scoped contacts
  getContacts: (agentEmail: string, limit = 500) =>
    request<Contact[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts?limit=${limit}`),
  createContact: (agentEmail: string, data: Pick<Contact, "email" | "name"> & Partial<Pick<Contact, "company" | "notes">>) =>
    request<Contact>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateContact: (agentEmail: string, contactId: string, data: Partial<Pick<Contact, "email" | "name" | "company" | "notes">>) =>
    request<Contact>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/${encodeURIComponent(contactId)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteContact: (agentEmail: string, contactId: string) =>
    request<void>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/${encodeURIComponent(contactId)}`, {
      method: "DELETE",
    }),
  getContactConsent: (agentEmail: string, contactId: string) =>
    request<ContactConsentHistory>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/${encodeURIComponent(contactId)}/consent`),
  recordContactConsent: (agentEmail: string, contactId: string, data: { action: "granted" | "revoked"; source: string; evidence?: string; note?: string }) =>
    request<ContactConsentHistory>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/${encodeURIComponent(contactId)}/consent`, {
      method: "POST", body: JSON.stringify(data),
    }),
  importContactsCsv: (agentEmail: string, csv: string) =>
    request<ContactImportResult>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/imports`, {
      method: "POST",
      headers: { "Content-Type": "text/csv; charset=utf-8" },
      body: csv,
    }),
  getContactImports: (agentEmail: string) =>
    request<ContactImportBatch[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/contacts/imports`),
  reverseContactImport: (agentEmail: string, batchId: string) =>
    request<{ batch_id: string; deleted: number; retained_after_edits: number }>(
      `/v1/agents/${encodeURIComponent(agentEmail)}/contacts/imports/${encodeURIComponent(batchId)}?confirm=DELETE`,
      { method: "DELETE" },
    ),

  // Inbox-scoped human follow-up records. Due items are surfaced only; these endpoints never send mail.
  getEngagements: (agentEmail: string, due = false) =>
    request<ContactEngagement[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/engagements?due=${due}&limit=500`),
  createEngagement: (agentEmail: string, data: { contact_id: string; stage?: string; next_due_at?: string | null; notes?: string }) =>
    request<ContactEngagement>(`/v1/agents/${encodeURIComponent(agentEmail)}/engagements`, { method: "POST", body: JSON.stringify(data) }),
  updateEngagement: (agentEmail: string, engagementId: string, data: { stage?: string; next_due_at?: string | null; notes?: string }) =>
    request<ContactEngagement>(`/v1/agents/${encodeURIComponent(agentEmail)}/engagements/${encodeURIComponent(engagementId)}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteEngagement: (agentEmail: string, engagementId: string) =>
    request<void>(`/v1/agents/${encodeURIComponent(agentEmail)}/engagements/${encodeURIComponent(engagementId)}`, { method: "DELETE" }),

  // Inbox-scoped reusable email templates
  getEmailTemplates: (agentEmail: string, limit = 100) =>
    request<EmailTemplate[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates?limit=${limit}`),
  createEmailTemplate: (agentEmail: string, data: Pick<EmailTemplate, "name" | "subject" | "body_text" | "body_html">) =>
    request<EmailTemplate>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateEmailTemplate: (agentEmail: string, templateId: string, data: Partial<Pick<EmailTemplate, "name" | "subject" | "body_text" | "body_html">>) =>
    request<EmailTemplate>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates/${encodeURIComponent(templateId)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteEmailTemplate: (agentEmail: string, templateId: string) =>
    request<void>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates/${encodeURIComponent(templateId)}`, { method: "DELETE" }),
  previewEmailTemplate: (agentEmail: string, data: { subject: string; body_text: string; body_html: string; data: Record<string, unknown> }) =>
    request<EmailTemplatePreview>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates/preview`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  previewSavedEmailTemplate: (agentEmail: string, templateId: string, data: Record<string, unknown>) =>
    request<EmailTemplatePreview>(`/v1/agents/${encodeURIComponent(agentEmail)}/templates/${encodeURIComponent(templateId)}/preview`, {
      method: "POST",
      body: JSON.stringify({ data }),
    }),

  // Inbox-scoped outbound recipient suppressions
  getSuppressions: (agentEmail: string, limit = 500) =>
    request<RecipientSuppression[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/suppressions?limit=${limit}`),
  addSuppression: (agentEmail: string, data: { email: string; reason?: string }) =>
    request<RecipientSuppression>(`/v1/agents/${encodeURIComponent(agentEmail)}/suppressions`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  deleteSuppression: (agentEmail: string, suppressionId: string) =>
    request<void>(`/v1/agents/${encodeURIComponent(agentEmail)}/suppressions/${encodeURIComponent(suppressionId)}`, { method: "DELETE" }),

  // Messages
  getMessages: (
    agentEmail: string,
    filters?: { direction?: string; status?: string; thread_id?: string; conversation_id?: string; limit?: number | string }
  ) => {
    const params = new URLSearchParams();
    if (filters?.direction) params.append("direction", filters.direction);
    if (filters?.status) params.append("status", filters.status);
    if (filters?.thread_id) params.append("thread_id", filters.thread_id);
    if (filters?.conversation_id) params.append("conversation_id", filters.conversation_id);
    if (filters?.limit) params.append("limit", String(filters.limit));
    const qs = params.toString() ? `?${params.toString()}` : "";
    return request<Message[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages${qs}`);
  },

  getMessagePage: (
    agentEmail: string,
    filters?: { q?: string; direction?: "inbound" | "outbound"; read_status?: "unread" | "read" | "all"; status?: MessageStatus; limit?: number; cursor?: string }
  ) => {
    const params = new URLSearchParams();
    if (filters?.q) params.set("q", filters.q);
    if (filters?.direction) params.set("direction", filters.direction);
    if (filters?.read_status) params.set("read_status", filters.read_status);
    if (filters?.status) params.set("status", filters.status);
    if (filters?.limit) params.set("limit", String(filters.limit));
    if (filters?.cursor) params.set("cursor", filters.cursor);
    return request<MessagePage>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/page?${params.toString()}`);
  },

  getMessage: (agentEmail: string, messageId: string) =>
    request<Message>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}`),

  markMessageRead: (agentEmail: string, messageId: string) =>
    request<Message>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}/read`, {
      method: "POST",
    }),

  getMessageAttachments: (agentEmail: string, messageId: string) =>
    request<MessageAttachment[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}/attachments`),

  sendMessage: (
    agentEmail: string,
    data: { recipient: string; thread_id?: string; conversation_id?: string; attachments?: OutboundAttachmentInput[] } & (
      { subject: string; body_text: string; body_html?: string; template_id?: never; template_data?: never } |
      { template_id: string; template_data?: Record<string, unknown>; subject?: never; body_text?: never; body_html?: never }
    ),
    idempotencyKey?: string,
  ) =>
    request<Message>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages`, {
      method: "POST",
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      body: JSON.stringify(data),
    }),

  replyMessage: (
    agentEmail: string,
    messageId: string,
    data: { conversation_id?: string; attachments?: OutboundAttachmentInput[] } & (
      { body_text: string; body_html?: string; template_id?: never; template_data?: never } |
      { template_id: string; template_data?: Record<string, unknown>; body_text?: never; body_html?: never }
    ),
    idempotencyKey?: string,
  ) =>
    request<Message>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}/reply`, {
      method: "POST",
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
      body: JSON.stringify(data),
    }),

  forwardMessage: (agentEmail: string, messageId: string, data: { recipient: string; note?: string }) =>
    request<Message>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}/forward`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getThread: (agentEmail: string, threadId: string) =>
    request<Message[]>(`/v1/agents/${encodeURIComponent(agentEmail)}/messages/thread/${encodeURIComponent(threadId)}`),

  // Reviews (HITL)
  getReviews: (status?: string) => {
    const qs = status ? `?status=${status}` : "";
    return request<ReviewItem[]>(`/v1/reviews${qs}`);
  },
  getReview: (reviewId: string) => request<ReviewItem>(`/v1/reviews/${reviewId}`),
  approveReview: (reviewId: string, notes?: string) =>
    request<ReviewItem>(`/v1/reviews/${reviewId}/approve`, {
      method: "POST",
      body: JSON.stringify({ action: "approve", notes }),
    }),
  rejectReview: (reviewId: string, notes?: string) =>
    request<ReviewItem>(`/v1/reviews/${reviewId}/reject`, {
      method: "POST",
      body: JSON.stringify({ action: "reject", notes }),
    }),

  // Domains & DNS
  getDomains: () => request<Domain[]>("/v1/domains"),
  createDomain: (domain: string) =>
    request<Domain>("/v1/domains", {
      method: "POST",
      body: JSON.stringify({ domain }),
    }),
  verifyDomain: (domain: string) =>
    request<Domain>(`/v1/domains/${encodeURIComponent(domain)}/verify`, {
      method: "POST",
    }),
  deleteDomain: (domain: string) =>
    request<void>(`/v1/domains/${encodeURIComponent(domain)}`, {
      method: "DELETE",
    }),

  // Upstream SMTP Relay Settings
  getSmtpConfig: () => request<SmtpConfig>("/v1/settings/smtp"),
  updateSmtpConfig: (data: Partial<SmtpConfig>) =>
    request<SmtpConfig>("/v1/settings/smtp", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  testSmtpConnection: (test_recipient: string) =>
    request<{ success: boolean; message: string }>("/v1/settings/smtp/test", {
      method: "POST",
      body: JSON.stringify({ test_recipient }),
    }),

  // API Keys
  getApiKeys: () => request<ApiKeyItem[]>("/v1/api-keys"),
  createApiKey: (name: string, agent_email: string, scope: string = "full_access", expires_in_days: number | null = 90) =>
    request<ApiKeyCreated>("/v1/api-keys", {
      method: "POST",
      body: JSON.stringify({ name, agent_email, scope, expires_in_days }),
    }),
  deleteApiKey: (id: string) =>
    request<void>(`/v1/api-keys/${id}`, {
      method: "DELETE",
    }),

  // Outbound delivery queue
  getOutboundJobs: (status?: OutboundJobStatus, limit = 100) => {
    const params = new URLSearchParams({ limit: String(Math.min(Math.max(limit, 1), 200)) });
    if (status) params.set("status", status);
    return request<OutboundJob[]>(`/v1/outbox?${params.toString()}`);
  },
  getOutboundJobPage: (status?: OutboundJobStatus, limit = 50, cursor?: string) => {
    const params = new URLSearchParams({ limit: String(Math.min(Math.max(limit, 1), 100)) });
    if (status) params.set("status", status);
    if (cursor) params.set("cursor", cursor);
    return request<OutboundJobPage>(`/v1/outbox/page?${params.toString()}`);
  },
  retryOutboundJob: (jobId: string, confirmDuplicateRisk = false) => {
    const qs = confirmDuplicateRisk ? "?confirm_duplicate_risk=true" : "";
    return request<OutboundJob>(`/v1/outbox/${encodeURIComponent(jobId)}/retry${qs}`, { method: "POST" });
  },
  getOutboundRetryEvents: (jobId: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(Math.min(Math.max(limit, 1), 200)) });
    return request<OutboundRetryEvent[]>(`/v1/outbox/${encodeURIComponent(jobId)}/retries?${params.toString()}`);
  },

  // Inbound email webhooks (admin)
  getWebhooks: (agentEmail?: string) => {
    const params = new URLSearchParams({ limit: "200" });
    if (agentEmail) params.set("agent_email", agentEmail);
    return request<WebhookEndpoint[]>(`/v1/webhooks?${params.toString()}`);
  },
  createWebhook: (data: { agent_email: string; url: string; enabled?: boolean; events: string[] }) =>
    request<WebhookEndpointCreated>("/v1/webhooks", { method: "POST", body: JSON.stringify(data) }),
  rotateWebhookSecret: (id: string) =>
    request<WebhookEndpointSecretRotated>(`/v1/webhooks/${encodeURIComponent(id)}/rotate-secret`, { method: "POST" }),
  updateWebhook: (id: string, data: { url?: string; enabled?: boolean; events?: string[] }) =>
    request<WebhookEndpoint>(`/v1/webhooks/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(data) }),
  testWebhook: (id: string) =>
    request<WebhookTestResult>(`/v1/webhooks/${encodeURIComponent(id)}/test`, { method: "POST" }),
  deleteWebhook: (id: string) =>
    request<void>(`/v1/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getWebhookDeliveries: (filters?: { endpoint_id?: string; status?: WebhookDeliveryStatus; event_type?: string; limit?: number }) => {
    const params = new URLSearchParams({ limit: String(Math.min(Math.max(filters?.limit ?? 100, 1), 200)) });
    if (filters?.endpoint_id) params.set("endpoint_id", filters.endpoint_id);
    if (filters?.status) params.set("status", filters.status);
    if (filters?.event_type) params.set("event_type", filters.event_type);
    return request<WebhookDelivery[]>(`/v1/webhooks/deliveries?${params.toString()}`);
  },
  getWebhookDeliveryPage: (filters?: { endpoint_id?: string; status?: WebhookDeliveryStatus; event_type?: string; limit?: number; cursor?: string }) => {
    const params = new URLSearchParams({ limit: String(Math.min(Math.max(filters?.limit ?? 100, 1), 200)) });
    if (filters?.endpoint_id) params.set("endpoint_id", filters.endpoint_id);
    if (filters?.status) params.set("status", filters.status);
    if (filters?.event_type) params.set("event_type", filters.event_type);
    if (filters?.cursor) params.set("cursor", filters.cursor);
    return request<WebhookDeliveryPage>(`/v1/webhooks/deliveries/page?${params.toString()}`);
  },
  replayWebhookDelivery: (deliveryId: string) =>
    request<{ id: string; status: "pending"; replayed_at: string }>(`/v1/webhooks/deliveries/${encodeURIComponent(deliveryId)}/replay`, { method: "POST" }),

  // MCP Info
  getMcpInfo: () => request<Record<string, unknown>>("/mcp/info"),
};
