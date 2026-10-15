import { AgentEmailError, errorFromResponse } from "./errors.js";
import { listen as listenInbox, type ListenOptions, type InboxEvent } from "./listen.js";
import type * as T from "./types.js";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ClientOptions {
  baseUrl: string;
  /** Admin key or an inbox-scoped API key. */
  apiKey: string;
  /** Override for tests or custom transports. Defaults to global fetch. */
  fetch?: FetchLike;
  timeoutMs?: number;
}

type Query = Record<string, string | number | boolean | undefined | null>;
interface Req { query?: Query; body?: unknown; raw?: { body: string; contentType: string }; headers?: Record<string, string>; signal?: AbortSignal }

const seg = (value: string) => encodeURIComponent(value);
const iso = (value: string | Date | undefined) => {
  if (value === undefined) return undefined;
  if (value instanceof Date) return value.toISOString();
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) || Number.isNaN(Date.parse(value))) {
    throw new TypeError("scheduled_at must be a valid ISO-8601 datetime with a timezone offset");
  }
  return value;
};
const metricTime = (value: string | Date | undefined) => {
  if (value === undefined) return undefined;
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) throw new TypeError("metrics start/end must be valid RFC 3339 datetimes");
    return value.toISOString();
  }
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) || Number.isNaN(Date.parse(value))) {
    throw new TypeError("metrics start/end must be valid RFC 3339 datetimes with a timezone offset");
  }
  return value;
};
const scheduledAt = (opts: { scheduled_at?: string | Date; send_at?: string | Date }) => {
  const canonical = iso(opts.scheduled_at);
  const legacy = iso(opts.send_at);
  if (canonical !== undefined && legacy !== undefined && canonical !== legacy) {
    throw new TypeError("scheduled_at and send_at must match when both are provided");
  }
  return canonical ?? legacy;
};

/** Encode bytes as an attachment payload accepted by send and reply. */
export function attachmentFromBytes(filename: string, content: Uint8Array, contentType = "application/octet-stream"): T.Attachment {
  return { filename, content_type: contentType, content_base64: Buffer.from(content).toString("base64") };
}

/** Walk a cursor-paginated endpoint, yielding every item. */
export async function* paginate<I>(fetchPage: (cursor?: string) => Promise<T.Page<I>>): AsyncGenerator<I, void, undefined> {
  let cursor: string | undefined;
  do {
    const page = await fetchPage(cursor);
    for (const item of page.items) yield item;
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
}

export class AgentEmailClient {
  readonly baseUrl: string;
  readonly #apiKey: string;
  readonly #fetch: FetchLike;
  readonly #timeoutMs: number;

  constructor(opts: ClientOptions) {
    if (!/^https?:\/\//i.test(opts.baseUrl)) throw new TypeError("baseUrl must start with http:// or https://");
    if (!opts.apiKey) throw new TypeError("apiKey is required");
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.#apiKey = opts.apiKey;
    this.#fetch = opts.fetch ?? ((input, init) => fetch(input, init));
    this.#timeoutMs = opts.timeoutMs ?? 15000;
  }

  async request<R = unknown>(method: string, path: string, req: Req = {}): Promise<R> {
    const url = new URL(this.baseUrl + path);
    for (const [key, value] of Object.entries(req.query ?? {})) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    const headers: Record<string, string> = { Authorization: `Bearer ${this.#apiKey}`, Accept: "application/json", ...req.headers };
    let body: string | undefined;
    if (req.raw) {
      body = req.raw.body;
      headers["Content-Type"] = req.raw.contentType;
    } else if (req.body !== undefined) {
      body = JSON.stringify(req.body);
      headers["Content-Type"] = "application/json";
    }
    const timeout = AbortSignal.timeout(this.#timeoutMs);
    const signal = req.signal ? AbortSignal.any([req.signal, timeout]) : timeout;
    let response: Response;
    try {
      response = await this.#fetch(url.toString(), { method, headers, body, signal, redirect: "manual" });
    } catch (err) {
      throw new AgentEmailError(`Request to ${method} ${path} failed: ${err instanceof Error ? err.message : String(err)}`);
    }
    if (response.status >= 300 && response.status < 400) {
      throw new AgentEmailError("Refusing to follow a redirect with credentials attached", { status: response.status });
    }
    const text = await response.text();
    let data: unknown = undefined;
    if (text) {
      try { data = JSON.parse(text); } catch { data = text; }
    }
    if (!response.ok) {
      throw errorFromResponse(`${method} ${path}`, response.status, data, response.headers.get("retry-after"));
    }
    return data as R;
  }

  #idem(opts?: T.RequestOptions): Req {
    return { headers: opts?.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : {}, signal: opts?.signal };
  }

  // ---- discovery -------------------------------------------------------
  info = () => this.request<T.ServerInfo>("GET", "/v1/info");
  whoami = () => this.request<T.WhoAmI>("GET", "/v1/whoami");

  // ---- agents ----------------------------------------------------------
  readonly agents = {
    list: () => this.request<T.Agent[]>("GET", "/v1/agents"),
    page: (params: { include_deleted?: boolean; limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Agent>>("GET", "/v1/agents/page", { query: { ...params } }),
    iterate: (params: Omit<{ include_deleted?: boolean; limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Agent>((cursor) => this.agents.page({ ...params, cursor })),
    get: (email: string) => this.request<T.Agent>("GET", `/v1/agents/${seg(email)}`),
    create: (input: T.AgentInput) => this.request<T.Agent>("POST", "/v1/agents", { body: input }),
    update: (email: string, patch: Partial<Omit<T.AgentInput, "email">> & { status?: string }) =>
      this.request<T.Agent>("PATCH", `/v1/agents/${seg(email)}`, { body: patch }),
    delete: (email: string) => this.request<void>("DELETE", `/v1/agents/${seg(email)}`),
    test: (email: string, ro?: T.RequestOptions) =>
      this.request<T.AgentTestSendResult>("POST", `/v1/agents/${seg(email)}/test`, this.#idem(ro)),
    metrics: (email: string) => this.request<T.AgentMetrics>("GET", `/v1/agents/${seg(email)}/metrics`),
    protection: (email: string) => this.request<T.Protection>("GET", `/v1/agents/${seg(email)}/protection`),
    setProtection: (email: string, protection: T.Protection) =>
      this.request<T.Protection>("PUT", `/v1/agents/${seg(email)}/protection`, { body: protection }),
  };

  /** Account-wide metrics require an account/admin bearer credential. */
  readonly account = {
    metrics: (params: T.AccountMetricsParams = {}) =>
      this.request<T.AccountMetrics>("GET", "/v1/metrics", { query: {
        start: metricTime(params.start), end: metricTime(params.end),
        bucket: params.bucket, group_by: params.group_by,
      } }),
  };

  // ---- messages --------------------------------------------------------
  readonly messages = {
    list: (inbox: string, params: T.MessageListParams = {}) =>
      this.request<T.Message[]>("GET", `/v1/agents/${seg(inbox)}/messages`, { query: { ...params } }),
    page: (inbox: string, params: T.MessagePageParams = {}) =>
      this.request<T.Page<T.Message>>("GET", `/v1/agents/${seg(inbox)}/messages/page`, { query: { ...params } }),
    /** Iterate every message matching the filters across all pages. */
    iterate: (inbox: string, params: Omit<T.MessagePageParams, "cursor"> = {}) =>
      paginate<T.Message>((cursor) => this.messages.page(inbox, { ...params, cursor })),
    get: (inbox: string, id: string) => this.request<T.Message>("GET", `/v1/agents/${seg(inbox)}/messages/${seg(id)}`),
    markRead: (inbox: string, id: string) => this.request<T.Message>("POST", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/read`),
    thread: (inbox: string, threadId: string) =>
      this.request<T.Message[]>("GET", `/v1/agents/${seg(inbox)}/messages/thread/${seg(threadId)}`),
    send: (inbox: string, opts: T.SendOptions, ro?: T.RequestOptions) =>
      this.request<T.Message>("POST", `/v1/agents/${seg(inbox)}/messages`, {
        ...this.#idem(ro), body: { ...opts, scheduled_at: scheduledAt(opts), send_at: undefined },
      }),
    reply: (inbox: string, id: string, opts: T.ReplyOptions, ro?: T.RequestOptions) =>
      this.request<T.Message>("POST", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/reply`, {
        ...this.#idem(ro), body: { ...opts, scheduled_at: scheduledAt(opts), send_at: undefined },
      }),
    forward: (inbox: string, id: string, recipient: string, note?: string) =>
      this.request<T.Message>("POST", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/forward`, { body: { recipient, note } }),
    setLabels: (inbox: string, id: string, labels: string[]) =>
      this.request<T.Message>("PATCH", `/v1/agents/${seg(inbox)}/messages/${seg(id)}`, { body: { labels } }),
    /** Move to trash; cancels a scheduled send that has not been submitted. */
    trash: (inbox: string, id: string) => this.request<T.Message>("DELETE", `/v1/agents/${seg(inbox)}/messages/${seg(id)}`),
    restore: (inbox: string, id: string) => this.request<T.Message>("POST", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/restore`),
    lifecycle: (inbox: string, id: string) => this.request<T.Lifecycle>("GET", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/lifecycle`),
    attachments: (inbox: string, id: string) =>
      this.request<Array<{ id: string; filename: string; content_type: string; size_bytes: number }>>(
        "GET", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/attachments`),
    attachmentByIndex: (inbox: string, id: string, index: number, inline = false) => {
      if (!Number.isInteger(index) || index < 0) throw new TypeError("attachment index must be a non-negative integer");
      return this.request<T.AttachmentByIndex>("GET", `/v1/agents/${seg(inbox)}/messages/${seg(id)}/attachments/${index}`, { query: { inline: inline || undefined } });
    },
    /** Download attachment bytes. */
    downloadAttachment: async (inbox: string, id: string, attachmentId: string): Promise<Uint8Array> => {
      const url = `${this.baseUrl}/v1/agents/${seg(inbox)}/messages/${seg(id)}/attachments/${seg(attachmentId)}`;
      const response = await this.#fetch(url, { headers: { Authorization: `Bearer ${this.#apiKey}` }, redirect: "manual" });
      if (!response.ok) throw new AgentEmailError(`Attachment download failed with ${response.status}`, { status: response.status });
      return new Uint8Array(await response.arrayBuffer());
    },
    /** Find a recent 4-8 digit verification code; server waits up to `waitSeconds` (max 10). */
    otp: (inbox: string, opts: { sinceSeconds?: number; waitSeconds?: number } = {}) =>
      this.request<T.OtpResult>("GET", `/v1/agents/${seg(inbox)}/messages/otp`, {
        query: { since_seconds: opts.sinceSeconds, wait_seconds: opts.waitSeconds },
      }),
  };

  readonly conversations = {
    list: (inbox: string, limit?: number) =>
      this.request<{ items: T.Conversation[] }>("GET", `/v1/agents/${seg(inbox)}/conversations`, { query: { limit } }),
    page: (inbox: string, params: { limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Conversation>>("GET", `/v1/agents/${seg(inbox)}/conversations/page`, { query: { ...params } }),
    iterate: (inbox: string, params: Omit<{ limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Conversation>((cursor) => this.conversations.page(inbox, { ...params, cursor })),
    get: (inbox: string, id: string) => this.request<T.ConversationDetail>("GET", `/v1/agents/${seg(inbox)}/conversations/${seg(id)}`),
  };

  // ---- contacts, consent, engagements, suppressions ---------------------
  readonly contacts = {
    list: (inbox: string, limit?: number) => this.request<T.Contact[]>("GET", `/v1/agents/${seg(inbox)}/contacts`, { query: { limit } }),
    page: (inbox: string, params: { limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Contact>>("GET", `/v1/agents/${seg(inbox)}/contacts/page`, { query: { ...params } }),
    iterate: (inbox: string, params: Omit<{ limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Contact>((cursor) => this.contacts.page(inbox, { ...params, cursor })),
    create: (inbox: string, input: T.ContactInput) => this.request<T.Contact>("POST", `/v1/agents/${seg(inbox)}/contacts`, { body: input }),
    update: (inbox: string, id: string, patch: Partial<T.ContactInput>) =>
      this.request<T.Contact>("PATCH", `/v1/agents/${seg(inbox)}/contacts/${seg(id)}`, { body: patch }),
    delete: (inbox: string, id: string) => this.request<void>("DELETE", `/v1/agents/${seg(inbox)}/contacts/${seg(id)}`),
    importCsv: (inbox: string, csv: string) =>
      this.request<Record<string, unknown>>("POST", `/v1/agents/${seg(inbox)}/contacts/imports`, { raw: { body: csv, contentType: "text/csv; charset=utf-8" } }),
    importStructured: (agentEmail: string, contacts: T.ContactImportRow[], opts: { onConflict?: "merge" | "skip" } & T.RequestOptions = {}) =>
      this.request<T.ContactImportResult>("POST", "/v1/contacts/import", {
        ...this.#idem(opts), body: { agent_email: agentEmail, contacts, on_conflict: opts.onConflict ?? "merge" },
      }),
    listImports: (inbox: string) => this.request<Array<Record<string, unknown>>>("GET", `/v1/agents/${seg(inbox)}/contacts/imports`),
    reverseImport: (inbox: string, batchId: string) =>
      this.request<Record<string, unknown>>("DELETE", `/v1/agents/${seg(inbox)}/contacts/imports/${seg(batchId)}`, { query: { confirm: "DELETE" } }),
    consent: (inbox: string, id: string) => this.request<T.ConsentHistory>("GET", `/v1/agents/${seg(inbox)}/contacts/${seg(id)}/consent`),
    recordConsent: (inbox: string, id: string, entry: { action: "granted" | "revoked"; source: string; evidence?: string; note?: string }) =>
      this.request<T.ConsentHistory>("POST", `/v1/agents/${seg(inbox)}/contacts/${seg(id)}/consent`, { body: entry }),
  };

  readonly engagements = {
    list: (inbox: string, params: { due?: boolean; limit?: number } = {}) =>
      this.request<T.Engagement[]>("GET", `/v1/agents/${seg(inbox)}/engagements`, { query: { ...params } }),
    page: (inbox: string, params: { due?: boolean; limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Engagement>>("GET", `/v1/agents/${seg(inbox)}/engagements/page`, { query: { ...params } }),
    iterate: (inbox: string, params: Omit<{ due?: boolean; limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Engagement>((cursor) => this.engagements.page(inbox, { ...params, cursor })),
    create: (inbox: string, input: { contact_id: string; stage?: T.EngagementStage; next_due_at?: string | Date; notes?: string }) =>
      this.request<T.Engagement>("POST", `/v1/agents/${seg(inbox)}/engagements`, { body: { ...input, next_due_at: iso(input.next_due_at) } }),
    update: (inbox: string, id: string, patch: { stage?: T.EngagementStage; next_due_at?: string | Date | null; notes?: string }) =>
      this.request<T.Engagement>("PATCH", `/v1/agents/${seg(inbox)}/engagements/${seg(id)}`, {
        body: { ...patch, next_due_at: patch.next_due_at === null ? null : iso(patch.next_due_at ?? undefined) },
      }),
    delete: (inbox: string, id: string) => this.request<void>("DELETE", `/v1/agents/${seg(inbox)}/engagements/${seg(id)}`),
  };

  readonly suppressions = {
    list: (inbox: string, limit?: number) => this.request<T.Suppression[]>("GET", `/v1/agents/${seg(inbox)}/suppressions`, { query: { limit } }),
    page: (inbox: string, params: { limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Suppression>>("GET", `/v1/agents/${seg(inbox)}/suppressions/page`, { query: { ...params } }),
    iterate: (inbox: string, params: Omit<{ limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Suppression>((cursor) => this.suppressions.page(inbox, { ...params, cursor })),
    add: (inbox: string, email: string, reason?: string) =>
      this.request<T.Suppression>("POST", `/v1/agents/${seg(inbox)}/suppressions`, { body: { email, reason } }),
    remove: (inbox: string, id: string) => this.request<void>("DELETE", `/v1/agents/${seg(inbox)}/suppressions/${seg(id)}`),
  };

  // ---- templates -------------------------------------------------------
  readonly templates = {
    list: (inbox: string, limit?: number) => this.request<T.EmailTemplate[]>("GET", `/v1/agents/${seg(inbox)}/templates`, { query: { limit } }),
    page: (inbox: string, params: { limit?: number; cursor?: string } = {}) => this.request<T.Page<T.EmailTemplate>>("GET", `/v1/agents/${seg(inbox)}/templates/page`, { query: { ...params } }),
    iterate: (inbox: string, params: Omit<{ limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.EmailTemplate>((cursor) => this.templates.page(inbox, { ...params, cursor })),
    get: (inbox: string, id: string) => this.request<T.EmailTemplate>("GET", `/v1/agents/${seg(inbox)}/templates/${seg(id)}`),
    create: (inbox: string, input: { name: string; subject: string; body_text?: string; body_html?: string }) =>
      this.request<T.EmailTemplate>("POST", `/v1/agents/${seg(inbox)}/templates`, { body: input }),
    update: (inbox: string, id: string, patch: { name?: string; subject?: string; body_text?: string; body_html?: string }) =>
      this.request<T.EmailTemplate>("PATCH", `/v1/agents/${seg(inbox)}/templates/${seg(id)}`, { body: patch }),
    delete: (inbox: string, id: string) => this.request<void>("DELETE", `/v1/agents/${seg(inbox)}/templates/${seg(id)}`),
    preview: (inbox: string, input: { subject: string; body_text?: string; body_html?: string; data?: Record<string, unknown> }) =>
      this.request<T.TemplatePreview>("POST", `/v1/agents/${seg(inbox)}/templates/preview`, { body: input }),
    previewSaved: (inbox: string, id: string, data: Record<string, unknown> = {}) =>
      this.request<T.TemplatePreview>("POST", `/v1/agents/${seg(inbox)}/templates/${seg(id)}/preview`, { body: { data } }),
  };

  // ---- reviews ---------------------------------------------------------
  readonly reviews = {
    list: (params: { status?: "pending" | "approved" | "rejected"; agent_email?: string; limit?: number } = {}) =>
      this.request<T.Review[]>("GET", "/v1/reviews", { query: { ...params } }),
    page: (params: { status?: "pending" | "approved" | "rejected"; agent_email?: string; limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Review>>("GET", "/v1/reviews/page", { query: { ...params } }),
    iterate: (params: Omit<{ status?: "pending" | "approved" | "rejected"; agent_email?: string; limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Review>((cursor) => this.reviews.page({ ...params, cursor })),
    get: (id: string) => this.request<T.Review>("GET", `/v1/reviews/${seg(id)}`),
    approve: (id: string, notes?: string) => this.request<T.Review>("POST", `/v1/reviews/${seg(id)}/approve`, { body: { action: "approve", notes } }),
    reject: (id: string, notes?: string) => this.request<T.Review>("POST", `/v1/reviews/${seg(id)}/reject`, { body: { action: "reject", notes } }),
  };

  // ---- outbox ----------------------------------------------------------
  readonly outbox = {
    list: (params: { status?: string; limit?: number } = {}) => this.request<T.OutboundJob[]>("GET", "/v1/outbox", { query: { ...params } }),
    page: (params: { status?: string; limit?: number; cursor?: string } = {}) =>
      this.request<T.Page<T.OutboundJob>>("GET", "/v1/outbox/page", { query: { ...params } }),
    iterate: (params: { status?: string; limit?: number } = {}) =>
      paginate<T.OutboundJob>((cursor) => this.outbox.page({ ...params, cursor })),
    retries: (jobId: string, limit?: number) =>
      this.request<Array<Record<string, unknown>>>("GET", `/v1/outbox/${seg(jobId)}/retries`, { query: { limit } }),
    /** Retry a terminal job. For uncertain jobs pass `confirmDuplicateRisk: true` to accept a possible duplicate send. */
    retry: (jobId: string, opts: { confirmDuplicateRisk?: boolean } = {}) =>
      this.request<T.OutboundJob>("POST", `/v1/outbox/${seg(jobId)}/retry`, { query: { confirm_duplicate_risk: opts.confirmDuplicateRisk } }),
  };

  // ---- webhooks and events ---------------------------------------------
  /**
   * Resource-style access: `client.inboxes.messages.send(inbox, { to, subject, text })`. These are thin wrappers
   * over `agents`, `messages` and `conversations`, which keep working unchanged.
   */
  readonly inboxes = {
    list: (params: T.ShortPageParams & { include_deleted?: boolean } = {}) =>
      this.agents.page({ include_deleted: params.include_deleted, limit: params.limit, cursor: params.pageToken }),
    iterate: (params: { include_deleted?: boolean; limit?: number } = {}) => this.agents.iterate(params),
    get: (inbox: string) => this.agents.get(inbox),
    create: (input: Omit<T.AgentInput, "display_name"> & { displayName?: string; display_name?: string }) => {
      const { displayName, display_name, ...rest } = input;
      return this.agents.create({ ...rest, display_name: display_name ?? displayName });
    },
    messages: {
      send: (inbox: string, opts: T.ShortSendOptions, ro?: T.RequestOptions) => {
        const { to, text, html, ...rest } = opts;
        return this.messages.send(inbox, { ...rest, recipient: to, body_text: text, body_html: html }, ro);
      },
      list: (inbox: string, params: T.ShortMessageListParams = {}) => {
        const { pageToken, query, ...rest } = params;
        return this.messages.page(inbox, { ...rest, q: query, cursor: pageToken });
      },
      iterate: (inbox: string, params: Omit<T.ShortMessageListParams, "pageToken"> = {}) => {
        const { query, ...rest } = params;
        return this.messages.iterate(inbox, { ...rest, q: query });
      },
      get: (inbox: string, id: string) => this.messages.get(inbox, id),
      reply: (inbox: string, id: string, opts: T.ShortReplyOptions, ro?: T.RequestOptions) => {
        const { text, html, replyAll, ...rest } = opts;
        if (replyAll) throw new TypeError("replyAll is not supported: messages have a single recipient");
        return this.messages.reply(inbox, id, { ...rest, body_text: text, body_html: html }, ro);
      },
      forward: (inbox: string, id: string, opts: { to: string; note?: string }) => this.messages.forward(inbox, id, opts.to, opts.note),
      update: (inbox: string, id: string, changes: T.LabelChanges) => {
        if (changes.labels !== undefined) {
          if (changes.addLabels !== undefined || changes.removeLabels !== undefined) throw new TypeError("labels cannot be combined with addLabels or removeLabels");
          return this.messages.setLabels(inbox, id, changes.labels);
        }
        if (changes.addLabels === undefined && changes.removeLabels === undefined) throw new TypeError("provide labels, or addLabels and/or removeLabels");
        return this.request<T.Message>("PATCH", `/v1/agents/${seg(inbox)}/messages/${seg(id)}`, {
          body: { add_labels: changes.addLabels, remove_labels: changes.removeLabels },
        });
      },
      trash: (inbox: string, id: string) => this.messages.trash(inbox, id),
      restore: (inbox: string, id: string) => this.messages.restore(inbox, id),
      markRead: (inbox: string, id: string) => this.messages.markRead(inbox, id),
    },
    threads: {
      list: (inbox: string, params: T.ShortPageParams = {}) => this.conversations.page(inbox, { limit: params.limit, cursor: params.pageToken }),
      iterate: (inbox: string, params: { limit?: number } = {}) => this.conversations.iterate(inbox, params),
      get: (inbox: string, id: string) => this.conversations.get(inbox, id),
    },
  };

  readonly webhooks = {
    list: (agentEmail?: string, limit?: number) => this.request<T.Webhook[]>("GET", "/v1/webhooks", { query: { agent_email: agentEmail, limit } }),
    page: (params: { agent_email?: string; limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Webhook>>("GET", "/v1/webhooks/page", { query: { ...params } }),
    iterate: (params: Omit<{ agent_email?: string; limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Webhook>((cursor) => this.webhooks.page({ ...params, cursor })),
    get: (id: string) => this.request<T.Webhook>("GET", `/v1/webhooks/${seg(id)}`),
    /** `event_types` is accepted as an alias for `events`. */
    create: (input: { agent_email: string; url: string; enabled?: boolean; events?: string[]; event_types?: string[] }) => {
      const { event_types, ...rest } = input;
      return this.request<T.WebhookCreated>("POST", "/v1/webhooks", { body: { ...rest, events: rest.events ?? event_types } });
    },
    update: (id: string, patch: { url?: string; enabled?: boolean; events?: string[] }) =>
      this.request<T.Webhook>("PATCH", `/v1/webhooks/${seg(id)}`, { body: patch }),
    delete: (id: string) => this.request<void>("DELETE", `/v1/webhooks/${seg(id)}`),
    /** Returns the new secret; the previous one stays valid for 24 hours. */
    rotateSecret: (id: string) => this.request<T.WebhookCreated>("POST", `/v1/webhooks/${seg(id)}/rotate-secret`),
    test: (id: string) => this.request<Record<string, unknown>>("POST", `/v1/webhooks/${seg(id)}/test`),
    deliveries: (id: string, params: { status?: string; limit?: number } = {}) =>
      this.request<T.WebhookDelivery[]>("GET", `/v1/webhooks/${seg(id)}/deliveries`, { query: { ...params } }),
    allDeliveries: (params: { endpoint_id?: string; status?: string; event_type?: string; limit?: number } = {}) =>
      this.request<T.WebhookDelivery[]>("GET", "/v1/webhooks/deliveries", { query: { ...params } }),
    deliveriesPage: (params: { endpoint_id?: string; status?: string; event_type?: string; limit?: number; cursor?: string } = {}) =>
      this.request<T.Page<T.WebhookDelivery>>("GET", "/v1/webhooks/deliveries/page", { query: { ...params } }),
    iterateDeliveries: (params: { endpoint_id?: string; status?: string; event_type?: string; limit?: number } = {}) =>
      paginate<T.WebhookDelivery>((cursor) => this.webhooks.deliveriesPage({ ...params, cursor })),
    replay: (deliveryId: string) => this.request<{ id: string; status: string }>("POST", `/v1/webhooks/deliveries/${seg(deliveryId)}/replay`),
  };

  readonly events = {
    page: (params: { agent_email?: string; event_type?: string; status?: string; since?: string; until?: string; limit?: number; cursor?: string } = {}) =>
      this.request<T.Page<T.WebhookDelivery>>("GET", "/v1/events", { query: { ...params } }),
    iterate: (params: { agent_email?: string; event_type?: string; status?: string; since?: string; until?: string; limit?: number } = {}) =>
      paginate<T.WebhookDelivery>((cursor) => this.events.page({ ...params, cursor })),
    get: (id: string) => this.request<T.WebhookDelivery>("GET", `/v1/events/${seg(id)}`),
    redeliver: (id: string) => this.request<{ id: string; status: string }>("POST", `/v1/events/${seg(id)}/redeliver`),
  };

  // ---- keys and domains ------------------------------------------------
  readonly apiKeys = {
    list: () => this.request<T.ApiKey[]>("GET", "/v1/api-keys"),
    /** The returned `key` is shown once; store it immediately. */
    create: (input: { name: string; agent_email: string; scope?: T.Scope; expires_in_days?: number | null }) =>
      this.request<T.ApiKeyCreated>("POST", "/v1/api-keys", { body: input }),
    revoke: (id: string) => this.request<void>("DELETE", `/v1/api-keys/${seg(id)}`),
    accountList: (params: { limit?: number; cursor?: string } = {}) =>
      this.request<T.AccountApiKeyPage>("GET", "/v1/account/api-keys", { query: { limit: params.limit, cursor: params.cursor } }),
    accountCreate: (input: { name: string; scope: T.AccountKeyScope; agent_email?: string | null; expires_in_days?: number | null }, ro?: T.RequestOptions) =>
      this.request<T.AccountApiKeyCreated>("POST", "/v1/account/api-keys", { ...this.#idem(ro), body: input }),
    accountRevoke: (id: string) => this.request<{ deleted: boolean; id: string }>("DELETE", `/v1/account/api-keys/${seg(id)}`, { query: { confirm: "DELETE" } }),
  };

  readonly domains = {
    list: () => this.request<T.Domain[]>("GET", "/v1/domains"),
    page: (params: { limit?: number; cursor?: string } = {}) => this.request<T.Page<T.Domain>>("GET", "/v1/domains/page", { query: { ...params } }),
    iterate: (params: Omit<{ limit?: number; cursor?: string }, "cursor"> = {}) => paginate<T.Domain>((cursor) => this.domains.page({ ...params, cursor })),
    register: (domain: string) => this.request<T.Domain>("POST", "/v1/domains", { body: { domain } }),
    verify: (domain: string) => this.request<T.Domain>("POST", `/v1/domains/${seg(domain)}/verify`),
    delete: (domain: string) => this.request<void>("DELETE", `/v1/domains/${seg(domain)}`),
  };

  // ---- real-time -------------------------------------------------------
  /** Stream live inbox notifications over WebSocket (no public URL needed). Reconnects automatically. */
  listen(agentEmail: string, opts: Omit<ListenOptions, "baseUrl" | "apiKey" | "agentEmail"> = {}): AsyncGenerator<InboxEvent, void, undefined> {
    return listenInbox({ ...opts, baseUrl: this.baseUrl, apiKey: this.#apiKey, agentEmail });
  }
}
