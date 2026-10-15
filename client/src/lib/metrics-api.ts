import type { AccountMetrics } from "./types";

// Helpers for the Metrics page. Kept apart from api.ts so the shared client stays small.
const API_BASE = "/api/backend";

export type MetricsRange = "24h" | "7d" | "30d";

export const RANGE_OPTIONS: { value: MetricsRange; label: string; hours: number }[] = [
  { value: "24h", label: "Last 24 hours", hours: 24 },
  { value: "7d", label: "Last 7 days", hours: 24 * 7 },
  { value: "30d", label: "Last 30 days", hours: 24 * 30 },
];

export interface InboxLiveMetrics {
  agent_email: string;
  messages: { inbound: number; outbound: number };
  by_status: { inbound: Record<string, number>; outbound: Record<string, number> };
  unread: number;
  pending_reviews: number;
}

async function call<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, { headers: { "Content-Type": "application/json" } });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(readError(text) || `Request failed with status ${response.status}`);
  }
  return response.json();
}

// The server reports `{detail, error: {message}}`; show the message rather than raw JSON.
function readError(text: string): string {
  try {
    const parsed = JSON.parse(text);
    const message = parsed?.error?.message ?? parsed?.detail;
    if (typeof message === "string") return message;
  } catch {
    // not JSON
  }
  return text;
}

export function getMetricsForRange(range: MetricsRange, now: Date = new Date()): Promise<AccountMetrics> {
  const hours = RANGE_OPTIONS.find((option) => option.value === range)?.hours ?? 24 * 30;
  const end = now;
  const start = new Date(end.getTime() - hours * 3600_000);
  const params = new URLSearchParams({
    start: start.toISOString(),
    end: end.toISOString(),
    bucket: "day",
    group_by: "agent",
  });
  return call<AccountMetrics>(`/v1/metrics?${params.toString()}`);
}

export function getInboxLiveMetrics(email: string): Promise<InboxLiveMetrics> {
  return call<InboxLiveMetrics>(`/v1/agents/${encodeURIComponent(email)}/metrics`);
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString();
}

export function bounces(summary: { bounced_hard: number; bounced_soft: number; bounced_undetermined: number }): number {
  return summary.bounced_hard + summary.bounced_soft + summary.bounced_undetermined;
}
