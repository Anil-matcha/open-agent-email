"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { AccountMetrics } from "@/lib/types";
import { RANGE_OPTIONS, bounces, count, getMetricsForRange, percent, type MetricsRange } from "@/lib/metrics-api";
import { DailyChart } from "@/components/metrics/DailyChart";
import { InboxBreakdown } from "@/components/metrics/InboxBreakdown";

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border border-[#30363d] bg-[#161b22] px-4 py-3">
      <div className="text-[11px] text-[#8b949e]">{label}</div>
      <div className="mt-1 font-mono text-xl font-semibold text-[#e6edf3]">{value}</div>
      {note && <div className="mt-0.5 text-[10px] text-[#6e7681]">{note}</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between border-t border-[#21262d] py-1.5 text-xs first:border-t-0">
      <dt className="text-[#8b949e]">{label}</dt>
      <dd className="font-mono text-[#e6edf3]">{value}</dd>
    </div>
  );
}

export default function MetricsPage() {
  const [range, setRange] = useState<MetricsRange>("7d");
  const [metrics, setMetrics] = useState<AccountMetrics | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getMetricsForRange(range);
        if (cancelled) return;
        setMetrics(data);
        setError("");
      } catch (failure) {
        if (cancelled) return;
        setMetrics(null);
        setError(failure instanceof Error ? failure.message : "Could not load metrics");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [range, reloadKey]);

  const choose = (next: MetricsRange) => {
    if (next === range) return;
    setLoading(true);
    setRange(next);
  };
  const reload = () => {
    setLoading(true);
    setReloadKey((key) => key + 1);
  };

  const summary = metrics?.summary;

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#e6edf3]">Metrics</h2>
          <p className="text-xs text-[#8b949e]">Delivery, authentication and review numbers across all inboxes.</p>
        </div>
        <div className="flex items-center gap-2">
          <div role="group" aria-label="Time range" className="flex overflow-hidden rounded-md border border-[#30363d] text-xs">
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                aria-pressed={range === option.value}
                onClick={() => choose(option.value)}
                className={`px-3 py-1.5 ${range === option.value ? "bg-[#21262d] text-white" : "text-[#8b949e] hover:text-[#e6edf3]"}`}
              >
                {option.value}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={reload}
            disabled={loading}
            aria-label="Refresh metrics"
            className="rounded-md border border-[#30363d] p-1.5 text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-3 py-2 text-xs text-[#f85149]">
          {error}
        </div>
      )}

      {!metrics && loading && <p className="py-10 text-center text-xs text-[#8b949e]">Loading metrics…</p>}

      {metrics && summary && (
        <>
          <p className="text-[11px] text-[#6e7681]">
            {new Date(metrics.start).toLocaleString()} to {new Date(metrics.end).toLocaleString()} ·{" "}
            {count(metrics.messages_in_window)} messages
            {metrics.reconstructed_observations > 0 && " · some outcomes are reconstructed from each message's current state"}
          </p>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Tile label="Received" value={count(summary.received)} />
            <Tile label="Delivered" value={count(summary.delivered)} />
            <Tile label="Delivered rate" value={percent(metrics.rates.delivered_rate)} note="of mail accepted for outside recipients" />
            <Tile label="Bounce rate" value={percent(metrics.rates.bounce_rate)} />
            <Tile label="Complaint rate" value={percent(metrics.rates.complaint_rate)} />
            <Tile label="Blocked recipients" value={count(summary.suppressed)} note="stopped by the block list" />
            <Tile label="Held for review" value={count(summary.review_held)} />
            <Tile label="Send failures" value={count(summary.send_failed)} />
          </div>

          <DailyChart buckets={metrics.buckets} />

          <InboxBreakdown agents={metrics.agents} truncated={metrics.agents_truncated} />

          <div className="grid gap-4 md:grid-cols-3">
            <section aria-labelledby="m-webhooks" className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
              <h2 id="m-webhooks" className="mb-2 text-sm font-semibold text-[#e6edf3]">Webhook deliveries</h2>
              <dl>
                <Stat label="Attempted" value={count(metrics.webhooks.deliveries)} />
                <Stat label="Delivered" value={count(metrics.webhooks.delivered)} />
                <Stat label="Still pending" value={count(metrics.webhooks.pending)} />
                <Stat label="Rejected endpoint" value={count(metrics.webhooks.endpoint_rejected)} />
                <Stat label="No response" value={count(metrics.webhooks.no_response)} />
                <Stat label="Success rate" value={percent(metrics.webhooks.success_rate)} />
              </dl>
            </section>

            <section aria-labelledby="m-review" className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
              <h2 id="m-review" className="mb-2 text-sm font-semibold text-[#e6edf3]">Review queue</h2>
              <dl>
                <Stat label="Held" value={count(summary.review_held)} />
                <Stat label="Approved" value={count(summary.review_approved)} />
                <Stat label="Rejected" value={count(summary.review_rejected)} />
                <Stat label="Approved on expiry" value={count(summary.review_expired_approved)} />
                <Stat label="Rejected on expiry" value={count(summary.review_expired_rejected)} />
              </dl>
            </section>

            <section aria-labelledby="m-auth" className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
              <h2 id="m-auth" className="mb-2 text-sm font-semibold text-[#e6edf3]">Sender authentication</h2>
              <p className="mb-1 text-[11px] text-[#6e7681]">DMARC result on mail received</p>
              <dl>
                <Stat label="Pass" value={count(summary.dmarc_pass)} />
                <Stat label="Fail" value={count(summary.dmarc_fail)} />
                <Stat label="No policy" value={count(summary.dmarc_none)} />
                <Stat label="Could not check" value={count(summary.dmarc_error)} />
              </dl>
            </section>
          </div>

          <section aria-labelledby="m-outcomes" className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
            <h2 id="m-outcomes" className="mb-2 text-sm font-semibold text-[#e6edf3]">Sending outcomes</h2>
            <dl className="grid gap-x-8 md:grid-cols-2">
              <Stat label="Accepted by the provider" value={count(summary.accepted)} />
              <Stat label="Submitted" value={count(summary.submitted)} />
              <Stat label="Delivered" value={count(summary.delivered)} />
              <Stat label="Bounced (all kinds)" value={count(bounces(summary))} />
              <Stat label="Hard bounces" value={count(summary.bounced_hard)} />
              <Stat label="Soft bounces" value={count(summary.bounced_soft)} />
              <Stat label="Complaints" value={count(summary.complained)} />
              <Stat label="Delivered inside this server" value={count(summary.loopback)} />
            </dl>
          </section>
        </>
      )}
    </div>
  );
}
