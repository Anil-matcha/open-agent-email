export interface Agent {
  id: string;
  email: string;
  display_name: string;
  description: string;
  status: "active" | "paused";
  threat_sensitivity: "off" | "low" | "medium" | "high";
  hitl_enabled: boolean;
  auto_reply_enabled: boolean;
  auto_reply_prompt: string;
  created_at: string;
  updated_at: string;
}

export interface Contact {
  id: string;
  agent_email: string;
  email: string;
  name: string;
  company: string;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface ContactConsentEvent {
  id: number;
  agent_email: string;
  contact_id: string;
  action: "granted" | "revoked";
  source: string;
  evidence: string;
  note: string;
  created_at: string;
}
export interface ContactConsentHistory {
  current: "unknown" | "granted" | "revoked";
  events: ContactConsentEvent[];
}

export interface ContactEngagement {
  id: string;
  agent_email: string;
  contact_id: string;
  contact_email: string;
  contact_name: string;
  stage: "new" | "contacted" | "waiting" | "closed";
  next_due_at: string | null;
  notes: string;
  replied_at: string | null;
  replied: boolean;
  suppressed: boolean;
  created_at: string;
  updated_at: string;
}

export interface EmailTemplate {
  id: string;
  agent_email: string;
  name: string;
  subject: string;
  body_text: string;
  body_html: string;
  created_at: string;
  updated_at: string;
}

export interface EmailTemplatePreview {
  subject: string;
  body_text: string;
  body_html: string;
  variables: string[];
}

export interface ContactImportResult {
  batch_id: string;
  created: number;
  skipped: number;
  results: Array<{ row: number; email: string; status: "created" | "skipped"; reason?: string }>;
  notice: string;
}
export interface ContactImportBatch { batch_id: string; created_at: string; created: number }

export interface RecipientSuppression {
  id: string;
  agent_email: string;
  email: string;
  reason: string | null;
  created_at: string;
}

export interface ThreatDetail {
  threat_type: string;
  severity: "low" | "medium" | "high" | "critical";
  confidence: number;
  description: string;
  matched_pattern?: string;
}

export interface ThreatScanResult {
  threat_score: number;
  verdict: "allow" | "review" | "block";
  detected_threats: ThreatDetail[];
  analysis_summary: string;
}

export interface Message {
  id: string;
  agent_email: string;
  direction: "inbound" | "outbound";
  is_read: boolean;
  sender: string;
  recipient: string;
  subject: string;
  body_text: string;
  body_html?: string;
  thread_id: string;
  conversation_id?: string | null;
  raw_message_id?: string | null;
  in_reply_to?: string;
  references?: string;
  rfc_message_id: string;
  spf_status: "pass" | "fail" | "softfail" | "neutral" | "none" | "permerror" | "temperror" | "unverified";
  dkim_status: "pass" | "fail" | "none" | "temperror" | "unverified";
  dmarc_status: "pass" | "fail" | "none" | "temperror" | "unverified";
  verified_domain?: string;
  delivery_error?: string | null;
  provider_delivery_status?: "delivered" | "bounced" | "complained" | "delayed" | "rejected" | null;
  provider_delivery_error?: string | null;
  status: "delivered" | "accepted_by_relay" | "sending" | "delivery_unknown" | "pending_review" | "rejected" | "blocked" | "failed";
  threat_score: number;
  threat_details: string; // JSON string
  created_at: string;
}

export interface MessagePage {
  items: Message[];
  next_cursor: string | null;
}

export interface MessageAttachment {
  id: string;
  message_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export interface OutboundAttachmentInput {
  filename: string;
  content_type?: string;
  content_base64: string;
}

export interface ReviewItem {
  id: string;
  message_id: string;
  agent_email: string;
  reason: string;
  threat_score: number;
  status: "pending" | "approved" | "rejected";
  reviewer_notes?: string;
  created_at: string;
  resolved_at?: string;
  message?: Message;
}

export interface Domain {
  id: string;
  domain: string;
  status: "verified" | "pending_dns";
  mx_record: string;
  mx_status: "verified" | "pending";
  spf_record: string;
  spf_status: "verified" | "pending";
  dkim_selector: string;
  dkim_record: string;
  dkim_status: "verified" | "pending";
  dmarc_record: string;
  dmarc_status: "verified" | "pending";
  created_at: string;
}

export interface SmtpConfig {
  id: string;
  host: string;
  port: string;
  username: string;
  password_configured: boolean;
  password_encryption_enabled: boolean;
  use_tls: boolean;
  use_ssl: boolean;
  from_email: string;
  enabled: boolean;
  updated_at?: string;
}

export interface ApiKeyItem {
  id: string;
  name: string;
  prefix: string;
  scope: string;
  agent_email?: string | null;
  created_at: string;
  expires_at?: string | null;
}

export interface ApiKeyCreated {
  id: string;
  name: string;
  key: string;
  prefix: string;
  scope: string;
  agent_email: string;
  created_at: string;
  expires_at?: string | null;
}

export type OutboundJobStatus = "pending" | "processing" | "complete" | "failed" | "uncertain" | "blocked";

export interface OutboundJob {
  id: string;
  message_id: string;
  status: OutboundJobStatus;
  attempts: number;
  available_at: string;
  locked_at?: string | null;
  last_error?: string | null;
  retried_by?: string | null;
  retried_at?: string | null;
  created_at: string;
}

export interface OutboundJobPage {
  items: OutboundJob[];
  next_cursor?: string | null;
}

export interface OutboundRetryEvent {
  id: string;
  job_id: string;
  actor: string;
  requested_at: string;
  previous_attempts: number;
  previous_error?: string | null;
  previous_status?: string | null;
}

export type WebhookDeliveryStatus = "pending" | "processing" | "delivered" | "failed";
export type WebhookEventType =
  | "email.received"
  | "email.review_requested"
  | "email.review_approved"
  | "email.review_rejected"
  | "email.flagged"
  | "email.blocked"
  | "email.accepted_by_relay"
  | "email.failed"
  | "email.delivery_unknown"
  | "email.delivered"
  | "email.bounced"
  | "email.complained"
  | "email.delivery_delayed"
  | "contact.due";

export interface WebhookEndpoint {
  id: string;
  agent_email: string;
  url: string;
  enabled: boolean;
  events?: string[];
  created_at: string;
  updated_at: string;
}

export interface WebhookEndpointCreated extends WebhookEndpoint {
  signing_secret: string;
}

export interface WebhookEndpointSecretRotated extends WebhookEndpointCreated {}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  agent_email: string;
  event_type: string;
  payload: Record<string, unknown>;
  status: WebhookDeliveryStatus;
  attempts: number;
  last_error?: string | null;
  created_at: string;
  delivered_at?: string | null;
  replayed_by?: string | null;
  replayed_at?: string | null;
  replay_count: number;
}

export interface WebhookDeliveryPage {
  items: WebhookDelivery[];
  next_cursor: string | null;
}

export interface WebhookTestResult {
  delivery_id: string;
  status: "pending";
}

export interface Stats {
  total_agents: number;
  total_messages: number;
  inbound_count: number;
  outbound_count: number;
  unread_messages: number;
  pending_reviews: number;
  threats_neutralized: number;
  active_websockets: number;
}

export interface AccountMetricsSummary {
  accepted: number;
  submitted: number;
  delivered: number;
  bounced_hard: number;
  bounced_soft: number;
  bounced_undetermined: number;
  complained: number;
  suppressed: number;
  loopback: number;
  send_failed: number;
  received: number;
  dmarc_pass: number;
  dmarc_fail: number;
  dmarc_none: number;
  dmarc_error: number;
  review_held: number;
  review_approved: number;
  review_rejected: number;
  review_expired_approved: number;
  review_expired_rejected: number;
}

export interface AccountMetricsRates {
  delivered_rate: number | null;
  bounce_rate: number | null;
  complaint_rate: number | null;
  suppression_block_rate: number | null;
}

export interface AccountMetricsAgent {
  agent_email: string;
  messages_in_window: number;
  messages_with_lifecycle: number;
  reconstructed_observations: number;
  summary: AccountMetricsSummary;
  rates: AccountMetricsRates;
}

export interface AccountMetricsBucket {
  day: string;
  summary: AccountMetricsSummary;
  rates: AccountMetricsRates;
}

export interface AccountMetrics {
  start: string;
  end: string;
  messages_in_window: number;
  messages_with_lifecycle: number;
  reconstructed_observations: number;
  summary: AccountMetricsSummary;
  rates: AccountMetricsRates;
  agents: AccountMetricsAgent[];
  agents_truncated: boolean;
  buckets: AccountMetricsBucket[];
  webhooks: {
    deliveries: number;
    delivered: number;
    pending: number;
    endpoint_rejected: number;
    no_response: number;
    success_rate: number | null;
  };
}
