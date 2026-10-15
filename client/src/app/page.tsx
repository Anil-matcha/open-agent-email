"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Mail,
  Send,
  ShieldAlert,
  Bot,
  ArrowRight,
  Plus
} from "lucide-react";
import { api } from "@/lib/api";
import { Stats, Message, Agent, AccountMetrics } from "@/lib/types";
import { FirstRunGuide } from "@/components/FirstRunGuide";

export default function OverviewPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [recentMessages, setRecentMessages] = useState<Message[]>([]);
  const [metrics, setMetrics] = useState<AccountMetrics | null>(null);
  const [metricsUnavailable, setMetricsUnavailable] = useState(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [guideOpen, setGuideOpen] = useState(false);
  const [guideDismissed, setGuideDismissed] = useState(false);

  const fetchData = async () => {
    try {
      const s = await api.getStats();
      setStats(s);

      const ags = await api.getAgents();
      setAgents(ags);

      if (ags.length > 0) {
        const msgs = await api.getMessages(ags[0].email, { limit: 10 });
        setRecentMessages(msgs);
      } else {
        setRecentMessages([]);
      }
    } catch {
      // quiet fallback
    } finally {
      setLoading(false);
    }
  };

  const fetchMetrics = async () => {
    try {
      setMetrics(await api.getAccountMetrics());
      setMetricsUnavailable(false);
    } catch {
      // Account metrics require an administrator credential. Keep the rest of
      // the overview usable when the endpoint is unavailable or unauthorized.
      setMetricsUnavailable(true);
    }
  };

  // Offer the guided first-run flow only when there are no inboxes yet; it then
  // stays open (even after the first inbox exists) until the user finishes it.
  useEffect(() => {
    if (!loading && agents.length === 0 && !guideDismissed) setGuideOpen(true);
  }, [loading, agents.length, guideDismissed]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    fetchMetrics();
    const metricsInterval = setInterval(fetchMetrics, 60000);
    return () => {
      clearInterval(interval);
      clearInterval(metricsInterval);
    };
  }, []);

  const pct = (value: number | null | undefined) =>
    value == null ? "—" : `${(value * 100).toFixed(1)}%`;
  const maxDailyMessages = Math.max(
    1,
    ...(metrics?.buckets ?? []).map((bucket) => bucket.summary.received + bucket.summary.accepted),
  );

  return (
    <div className="space-y-6">
      {guideOpen && (
        <FirstRunGuide
          onCreated={fetchData}
          onDone={() => {
            setGuideOpen(false);
            setGuideDismissed(true);
          }}
        />
      )}

      {/* Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card-simple p-4 space-y-1">
          <div className="text-xs text-[#8b949e]">Active Agents</div>
          <div className="text-2xl font-semibold text-[#e6edf3] font-mono">
            {stats?.total_agents ?? 0}
          </div>
          <div className="text-[11px] text-[#8b949e]">Configured inboxes</div>
        </div>

        <div className="card-simple p-4 space-y-1">
          <div className="text-xs text-[#8b949e]">Total Messages</div>
          <div className="text-2xl font-semibold text-[#e6edf3] font-mono">
            {stats?.total_messages ?? 0}
          </div>
          <div className="text-[11px] text-[#8b949e]">
            {stats?.inbound_count ?? 0} in · {stats?.outbound_count ?? 0} out
          </div>
        </div>

        <div className="card-simple p-4 space-y-1">
          <div className="text-xs text-[#8b949e]">Pending Reviews</div>
          <div className="text-2xl font-semibold text-[#d29922] font-mono">
            {stats?.pending_reviews ?? 0}
          </div>
          <div className="text-[11px] text-[#8b949e]">Awaiting human decision</div>
        </div>

        <div className="card-simple p-4 space-y-1">
          <div className="text-xs text-[#8b949e]">Threats Intercepted</div>
          <div className="text-2xl font-semibold text-[#f85149] font-mono">
            {stats?.threats_neutralized ?? 0}
          </div>
          <div className="text-[11px] text-[#8b949e]">Prompt injections blocked</div>
        </div>
      </div>

      {/* Account metrics are optional so a missing admin key never blocks the overview. */}
      <section className="card-simple p-4 space-y-4" aria-label="Account email metrics">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">
              Account Metrics <span className="normal-case tracking-normal text-[#8b949e]">· last 30 days</span>
            </h2>
            <p className="mt-1 text-[11px] text-[#8b949e]">
              Lifecycle observations are reconstructed from current message state; they are not a persisted event history.
            </p>
          </div>
          {metrics && (
            <span className="text-[10px] font-mono rounded border border-[#9e6a03]/40 bg-[#9e6a03]/10 px-2 py-1 text-[#d29922]">
              {metrics.reconstructed_observations.toLocaleString()} reconstructed observations
            </span>
          )}
        </div>

        {metricsUnavailable ? (
          <p className="rounded border border-[#30363d] bg-[#0d1117] p-3 text-xs text-[#8b949e]">
            Account metrics are unavailable. The overview remains active; configure an administrator API credential to load account-wide data.
          </p>
        ) : metrics ? (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div className="rounded border border-[#30363d] bg-[#0d1117] p-3">
                <div className="text-[10px] text-[#8b949e]">Messages</div>
                <div className="mt-1 font-mono text-lg text-[#e6edf3]">{metrics.messages_in_window.toLocaleString()}</div>
                <div className="text-[10px] text-[#8b949e]">{metrics.summary.received} inbound · {metrics.summary.accepted} outbound</div>
              </div>
              <div className="rounded border border-[#30363d] bg-[#0d1117] p-3">
                <div className="text-[10px] text-[#8b949e]">Delivered rate</div>
                <div className="mt-1 font-mono text-lg text-[#2ea043]">{pct(metrics.rates.delivered_rate)}</div>
                <div className="text-[10px] text-[#8b949e]">{metrics.summary.delivered} provider-confirmed</div>
              </div>
              <div className="rounded border border-[#30363d] bg-[#0d1117] p-3">
                <div className="text-[10px] text-[#8b949e]">Bounce rate</div>
                <div className="mt-1 font-mono text-lg text-[#d29922]">{pct(metrics.rates.bounce_rate)}</div>
                <div className="text-[10px] text-[#8b949e]">{metrics.summary.bounced_hard + metrics.summary.bounced_soft + metrics.summary.bounced_undetermined} observed bounces</div>
              </div>
              <div className="rounded border border-[#30363d] bg-[#0d1117] p-3">
                <div className="text-[10px] text-[#8b949e]">Complaints</div>
                <div className="mt-1 font-mono text-lg text-[#f85149]">{metrics.summary.complained}</div>
                <div className="text-[10px] text-[#8b949e]">Rate {pct(metrics.rates.complaint_rate)}</div>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(220px,1fr)]">
              <div>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-[11px] font-medium text-[#c9d1d9]">Daily message volume</h3>
                  <div className="flex gap-3 text-[10px] text-[#8b949e]">
                    <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-cyan-400" />Inbound</span>
                    <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-blue-400" />Outbound</span>
                  </div>
                </div>
                <div className="flex h-28 items-end gap-1 border-b border-[#30363d] px-1" role="img" aria-label="Daily inbound and outbound message volume for the last 30 days">
                  {metrics.buckets.map((bucket) => {
                    const inbound = bucket.summary.received;
                    const outbound = bucket.summary.accepted;
                    const total = inbound + outbound;
                    const height = Math.max(total ? 4 : 1, (total / maxDailyMessages) * 100);
                    const inboundShare = total ? (inbound / total) * 100 : 0;
                    const outboundShare = total ? (outbound / total) * 100 : 0;
                    const label = new Date(bucket.day).toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" });
                    return (
                      <div key={bucket.day} className="group relative flex h-full flex-1 items-end" title={`${label}: ${inbound} inbound, ${outbound} outbound`}>
                        <div className="flex w-full flex-col justify-end overflow-hidden rounded-t-sm" style={{ height: `${height}%` }}>
                          <div className="bg-blue-400" style={{ height: `${outboundShare}%` }} />
                          <div className="bg-cyan-400" style={{ height: `${inboundShare}%` }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-1 flex justify-between text-[9px] text-[#8b949e]">
                  <span>{metrics.buckets[0] ? new Date(metrics.buckets[0].day).toLocaleDateString([], { month: "short", day: "numeric", timeZone: "UTC" }) : ""}</span>
                  <span>Today</span>
                </div>
              </div>

              <div>
                <h3 className="mb-2 text-[11px] font-medium text-[#c9d1d9]">By agent</h3>
                <div className="max-h-36 space-y-2 overflow-y-auto">
                  {metrics.agents.length === 0 ? (
                    <p className="text-[11px] text-[#8b949e]">No message activity in this window.</p>
                  ) : metrics.agents.slice(0, 6).map((agent) => (
                    <div key={agent.agent_email} className="flex items-center justify-between gap-2 text-[10px]">
                      <span className="min-w-0 truncate font-mono text-[#8b949e]">{agent.agent_email}</span>
                      <span className="shrink-0 font-mono text-[#e6edf3]">{agent.messages_in_window.toLocaleString()}</span>
                    </div>
                  ))}
                  {metrics.agents_truncated && <p className="text-[10px] text-[#8b949e]">Showing busiest agents.</p>}
                </div>
              </div>
            </div>
          </>
        ) : (
          <p className="text-xs text-[#8b949e]">Loading account metrics…</p>
        )}
      </section>

      {/* Main Grid: Messages + Inboxes */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Messages Column */}
        <div className="lg:col-span-2 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">
              Recent Email Activity
            </h2>
            <Link
              href="/inbox"
              className="text-xs text-blue-400 hover:underline flex items-center gap-1"
            >
              Open Inbox <ArrowRight className="w-3 h-3" />
            </Link>
          </div>

          <div className="card-simple divide-y divide-[#30363d] overflow-hidden">
            {recentMessages.length === 0 ? (
              <div className="p-8 text-center text-xs text-[#8b949e] space-y-2">
                <div>No emails found yet.</div>
                <div className="text-[11px]">
                  Use the Simulator to test inbound email or create an agent inbox to get started.
                </div>
                <div className="pt-2 flex justify-center gap-2">
                  <Link
                    href="/agents"
                    className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium"
                  >
                    Create Agent Inbox
                  </Link>
                  <Link
                    href="/simulator"
                    className="px-3 py-1.5 rounded border border-[#30363d] bg-[#21262d] text-xs text-[#e6edf3]"
                  >
                    Test Inbound
                  </Link>
                </div>
              </div>
            ) : (
              recentMessages.map((msg) => (
                <div
                  key={msg.id}
                  className="p-3 hover:bg-[#1c2128] transition-colors flex items-center justify-between gap-3 text-xs"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-[#8b949e] shrink-0">
                      {msg.direction === "inbound" ? (
                        <Mail className="w-4 h-4 text-cyan-400" />
                      ) : (
                        <Send className="w-4 h-4 text-blue-400" />
                      )}
                    </span>
                    <div className="min-w-0">
                      <div className="font-medium text-[#e6edf3] truncate">
                        {msg.subject || "(No Subject)"}
                      </div>
                      <div className="text-[11px] text-[#8b949e] truncate">
                        {msg.direction === "inbound" ? msg.sender : msg.recipient} → {msg.agent_email}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    <span
                      className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                        msg.status === "delivered" || msg.status === "accepted_by_relay"
                          ? "bg-[#238636]/10 text-[#2ea043] border-[#2ea043]/30"
                          : msg.status === "pending_review" || msg.status === "sending" || msg.status === "delivery_unknown"
                          ? "bg-[#9e6a03]/10 text-[#d29922] border-[#d29922]/30"
                          : "bg-[#da3633]/10 text-[#f85149] border-[#f85149]/30"
                      }`}
                    >
                      {msg.status.replace("_", " ")}
                    </span>
                    <span className="text-[10px] font-mono text-[#8b949e]">
                      {new Date(msg.created_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Agents Column */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">
              Agent Inboxes
            </h2>
            <Link
              href="/agents"
              className="text-xs text-blue-400 hover:underline flex items-center gap-1"
            >
              Manage <ArrowRight className="w-3 h-3" />
            </Link>
          </div>

          <div className="card-simple divide-y divide-[#30363d] overflow-hidden">
            {agents.length === 0 ? (
              <div className="p-6 text-center text-xs text-[#8b949e] space-y-2">
                <div>No inboxes configured.</div>
                <Link
                  href="/agents"
                  className="inline-flex items-center gap-1 text-xs text-blue-400 hover:underline"
                >
                  <Plus className="w-3 h-3" /> Add first agent
                </Link>
              </div>
            ) : (
              agents.map((ag) => (
                <div key={ag.id} className="p-3 text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-[#e6edf3]">{ag.display_name}</span>
                    <span className="text-[10px] font-mono text-[#8b949e] uppercase">
                      {ag.threat_sensitivity} scan
                    </span>
                  </div>
                  <div className="font-mono text-[11px] text-blue-400 truncate">
                    {ag.email}
                  </div>
                  <div className="text-[10px] text-[#8b949e] flex items-center justify-between pt-1">
                    <span>HITL: {ag.hitl_enabled ? "Enabled" : "Off"}</span>
                    <span>Auto-reply: {ag.auto_reply_enabled ? "On" : "Off"}</span>
                  </div>
                </div>
              ))
            )}
          </div>

          {/* Quick MCP Info Box */}
          <div className="card-simple p-3 text-xs space-y-1.5">
            <div className="font-semibold text-[#e6edf3] flex items-center gap-1.5">
              <Bot className="w-3.5 h-3.5 text-blue-400" />
              <span>MCP Server Endpoint</span>
            </div>
            <p className="text-[11px] text-[#8b949e]">
              Connect Cursor or Claude Code to let agents interact with inboxes via MCP tools.
            </p>
            <div className="bg-[#0d1117] p-2 rounded border border-[#30363d] font-mono text-[10px] text-[#8b949e] truncate">
              http://127.0.0.1:8000/mcp/rpc
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
