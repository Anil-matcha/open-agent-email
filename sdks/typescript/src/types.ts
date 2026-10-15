export type MessageStatus =
  | "delivered" | "accepted_by_relay" | "sending" | "scheduled" | "canceled" | "delivery_unknown"
  | "pending_review" | "rejected" | "blocked" | "failed";
export type Scope = "read_only" | "full_access" | "reviewer";
export type AccountKeyScope = "account" | "agent";

export interface Attachment { filename: string; content_type: string; content_base64: string }

export interface Message {
  id: string; agent_email: string; direction: "inbound" | "outbound"; is_read: boolean;
  sender: string; recipient: string; subject: string; body_text: string; body_html: string | null;
  thread_id: string; conversation_id?: string | null; rfc_message_id: string; in_reply_to: string | null; references: string | null;
  spf_status: string; dkim_status: string; dmarc_status: string; verified_domain: string | null;
  auth_evidence?: Record<string, unknown> | null;
  status: MessageStatus; provider_delivery_status?: string | null; provider_delivery_error?: string | null;
  delivery_error?: string | null; threat_score: number; threat_details: string;
  labels: string[]; scheduled_for: string | null; scheduled_at: string | null; deleted_at: string | null; created_at: string;
}
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface OtpResult { found: boolean; code?: string; message_id?: string; received_at?: string; sender?: string; subject?: string }

export interface SendOptions {
  recipient: string; subject?: string; body_text?: string; body_html?: string;
  template_id?: string; template_data?: Record<string, unknown>; thread_id?: string; conversation_id?: string;
  attachments?: Attachment[];
  /** Defer submission (offset-qualified ISO string or Date, at most 90 days ahead). */
  scheduled_at?: string | Date;
  /** @deprecated Use scheduled_at. */
  send_at?: string | Date;
}
export interface ReplyOptions {
  body_text?: string; body_html?: string; template_id?: string; template_data?: Record<string, unknown>;
  conversation_id?: string; attachments?: Attachment[]; scheduled_at?: string | Date;
  /** @deprecated Use scheduled_at. */ send_at?: string | Date;
}
/** Short-name form of {@link SendOptions}: `to`, `text` and `html` instead of `recipient`, `body_text` and `body_html`. */
export type ShortSendOptions = Omit<SendOptions, "recipient" | "body_text" | "body_html"> & { to: string; text?: string; html?: string };
/** Short-name form of {@link ReplyOptions}. Messages have a single recipient, so `replyAll` is rejected. */
export type ShortReplyOptions = Omit<ReplyOptions, "body_text" | "body_html"> & { text?: string; html?: string; replyAll?: boolean };
export interface ShortPageParams { limit?: number; pageToken?: string }
export type ShortMessageListParams = Omit<MessagePageParams, "cursor" | "limit" | "q"> & { limit?: number; pageToken?: string; query?: string };
export interface LabelChanges { addLabels?: string[]; removeLabels?: string[]; labels?: string[] }
export interface RequestOptions { idempotencyKey?: string; signal?: AbortSignal }

export interface MessageListParams {
  direction?: "inbound" | "outbound"; status?: MessageStatus; thread_id?: string; conversation_id?: string; label?: string;
  trashed?: boolean; limit?: number;
}
export interface MessagePageParams {
  limit?: number; direction?: "inbound" | "outbound"; read_status?: "unread" | "read" | "all";
  status?: MessageStatus; q?: string; cursor?: string;
}

export interface Contact { id: string; agent_email: string; email: string; name: string; company: string; notes: string; metadata: Record<string, string | number | boolean | null>; created_at: string; updated_at: string }
export interface ContactInput { email: string; name: string; company?: string; notes?: string; metadata?: Record<string, string | number | boolean | null> }
export interface ContactImportRow { email: string; display_name?: string; company?: string; notes?: string; metadata?: Record<string, string | number | boolean | null>; suppressed?: boolean; stage?: EngagementStage }
export interface ContactImportResult { batch_id: string; created: number; updated: number; skipped: number; failed: number; results: Array<{ row: number; email?: string | null; status: "created" | "updated" | "skipped" | "failed"; reason?: string; contact_id?: string; suppressed?: boolean }>; notice: string }
export interface ConsentEvent { id: number; agent_email: string; contact_id: string; action: "granted" | "revoked"; source: string; evidence: string; note: string; created_at: string }
export interface ConsentHistory { current: "granted" | "revoked" | "unknown"; events: ConsentEvent[] }
export type EngagementStage = "new" | "contacted" | "waiting" | "closed";
export interface Engagement { id: string; agent_email: string; contact_id: string; contact_email: string; contact_name: string; stage: EngagementStage; next_due_at: string | null; notes: string; replied_at: string | null; replied: boolean; suppressed: boolean; created_at: string; updated_at: string }
export interface Suppression { id: string; agent_email: string; email: string; reason: string | null; created_at: string }
export interface EmailTemplate { id: string; agent_email: string; name: string; subject: string; body_text: string; body_html: string; created_at: string; updated_at: string }
export interface TemplatePreview { subject: string; body_text: string; body_html: string; variables: string[] }
export interface Conversation { id: string; subject: string; message_count: number; unread_count: number; participants: string[]; labels: string[]; last_message_at: string; last_message_id: string }
export interface ConversationDetail extends Conversation { messages: Message[] }
export interface LifecycleEvent { type: string; at: string | null; detail: string; [extra: string]: unknown }
export interface Lifecycle { message_id: string; status: string; events: LifecycleEvent[] }
export interface Review { id: string; message_id: string; agent_email: string; reason: string; threat_score: number; status: "pending" | "approved" | "rejected"; reviewer_notes: string | null; reviewed_by?: string | null; created_at: string; resolved_at: string | null; message?: Message }
export interface OutboundJob { id: string; message_id: string; status: string; attempts: number; available_at: string; last_error: string | null; created_at: string; [extra: string]: unknown }
export interface Webhook { id: string; agent_email: string; url: string; enabled: boolean; events: string[]; created_at: string; updated_at: string }
export interface WebhookCreated extends Webhook { signing_secret: string }
export interface WebhookDelivery { id: string; endpoint_id: string; agent_email: string; event_type: string; payload: Record<string, unknown>; status: string; attempts: number; last_error: string | null; created_at: string; delivered_at: string | null; replayed_by?: string | null; replayed_at?: string | null; replay_count?: number }
export interface Agent { id: string; email: string; display_name: string; description: string; status: string; threat_sensitivity: string; hitl_enabled: boolean; auto_reply_enabled: boolean; auto_reply_prompt: string; created_at: string; updated_at: string }
export interface AgentTestSendResult { status: "accepted" | "pending_review"; message_id: string }
export interface AgentInput { email: string; display_name?: string; description?: string; threat_sensitivity?: string; hitl_enabled?: boolean; auto_reply_enabled?: boolean; auto_reply_prompt?: string }
export interface ApiKey { id: string; name: string; prefix: string; scope: Scope; agent_email: string; created_at: string; expires_at: string | null }
export interface ApiKeyCreated extends ApiKey { key: string }
export interface AccountApiKey { id: string; name: string; key_prefix: string; scope: AccountKeyScope; agent_email: string | null; created_at: string; expires_at: string | null }
export interface AccountApiKeyCreated extends AccountApiKey { key: string }
export interface AccountApiKeyPage { items: AccountApiKey[]; next_cursor: string | null }
export interface AttachmentByIndex { index: number; filename: string; content_type: string; size_bytes: number; download_url: string; expires_at: string; data?: string }
export interface Domain { id: string; domain: string; status: string; [extra: string]: unknown }
export interface Protection { threat_sensitivity: "off" | "low" | "medium" | "high"; hitl_enabled: boolean }
export interface AgentMetrics { agent_email: string; messages: { inbound: number; outbound: number }; by_status: Record<string, Record<string, number>>; unread: number; pending_reviews: number }
export interface AccountMetricsParams {
  /** Inclusive RFC 3339 cohort-window start; defaults to 30 days before end. */
  start?: string | Date;
  /** Exclusive RFC 3339 cohort-window end; defaults to now. */
  end?: string | Date;
  bucket?: "day";
  group_by?: "agent";
}
export interface MetricsCounter {
  reason_code: string; stage: string; outcome: string; retryable: boolean; observations: number; messages: number;
}
export interface MetricsRates {
  delivered_rate: number | null; bounce_rate: number | null; complaint_rate: number | null; suppression_block_rate: number | null;
}
export interface MetricsSummary {
  accepted: number; submitted: number; delivered: number; bounced_hard: number; bounced_soft: number;
  bounced_undetermined: number; complained: number; suppressed: number; loopback: number; send_failed: number;
  received: number; dmarc_pass: number; dmarc_fail: number; dmarc_none: number; dmarc_error: number;
  review_held: number; review_approved: number; review_rejected: number;
  review_expired_approved: number; review_expired_rejected: number;
}
export interface AgentMetricsGroup {
  agent_email: string; messages_in_window: number; messages_with_lifecycle: number;
  reconstructed_observations: number; summary: MetricsSummary; rates: MetricsRates; counters: MetricsCounter[];
}
export interface MetricsBucket { day: string; summary: MetricsSummary; rates: MetricsRates }
export interface WebhookEndpointMetrics {
  webhook_id: string; url_host: string; deliveries: number; delivered: number; pending: number;
  endpoint_rejected: number; no_response: number; success_rate: number | null; last_status_code: number | null;
  enabled: boolean; auto_disabled_at: string | null; auto_disable_reason: string;
}
export interface WebhookMetrics {
  deliveries: number; delivered: number; pending: number; endpoint_rejected: number; no_response: number;
  success_rate: number | null; window_exceeds_retention: boolean; endpoints_auto_disabled: number;
  endpoints: WebhookEndpointMetrics[]; endpoints_truncated: boolean;
}
export interface AccountMetrics {
  start: string; end: string; messages_in_window: number; messages_with_lifecycle: number;
  reconstructed_observations: number; summary: MetricsSummary; rates: MetricsRates; counters: MetricsCounter[];
  agents: AgentMetricsGroup[]; agents_truncated: boolean; buckets: MetricsBucket[]; webhooks: WebhookMetrics;
}
export interface ServerInfo { name: string; version: string; api_version: string; shared_domain: string; mcp_endpoint: string; features: string[] }
export interface WhoAmI { kind: "admin" | "agent_key"; scope: string; agent_email: string | null; principal_id: string }
