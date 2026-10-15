"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { agentsApi, InboxMetrics } from "@/lib/agents-api";

function Tile({ label, value }: { label: string; value: number }) {
  return (
    <div className="p-2 rounded bg-[#0d1117] border border-[#30363d]">
      <div className="text-[10px] text-[#8b949e] uppercase tracking-wide">{label}</div>
      <div className="text-lg font-semibold text-[#e6edf3]">{value}</div>
    </div>
  );
}

function StatusChips({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  return (
    <div className="text-[11px] text-[#8b949e]">
      <span className="mr-2">{title}:</span>
      {entries.length === 0 ? (
        <span>none yet</span>
      ) : (
        entries.map(([status, count]) => (
          <span key={status} className="inline-block mr-2 px-1.5 py-0.5 rounded bg-[#21262d] text-[#e6edf3]">
            {status.replace(/_/g, " ")} {count}
          </span>
        ))
      )}
    </div>
  );
}

export function MetricsPanel({ email }: { email: string }) {
  const [metrics, setMetrics] = useState<InboxMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setMetrics(await agentsApi.getMetrics(email));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [email]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-[#e6edf3]">Inbox activity</div>
        <button
          onClick={load}
          disabled={loading}
          className="text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      {error && <div className="text-[11px] text-[#f85149]">Could not load metrics: {error}</div>}
      {metrics && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Tile label="Received" value={metrics.messages.inbound} />
            <Tile label="Sent" value={metrics.messages.outbound} />
            <Tile label="Unread" value={metrics.unread} />
            <Tile label="Awaiting review" value={metrics.pending_reviews} />
          </div>
          <StatusChips title="Received by status" counts={metrics.by_status.inbound} />
          <StatusChips title="Sent by status" counts={metrics.by_status.outbound} />
          <p className="text-[10px] text-[#8b949e]">Trashed messages are not counted.</p>
        </>
      )}
    </div>
  );
}
