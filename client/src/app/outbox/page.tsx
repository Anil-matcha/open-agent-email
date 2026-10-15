"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Clock3, RefreshCw, RotateCcw, Send } from "lucide-react";
import { api } from "@/lib/api";
import { OutboundJob, OutboundJobStatus, OutboundRetryEvent } from "@/lib/types";

const FILTERS: { label: string; value: OutboundJobStatus | "" }[] = [
  { label: "All", value: "" },
  { label: "Pending", value: "pending" },
  { label: "Processing", value: "processing" },
  { label: "Complete", value: "complete" },
  { label: "Failed", value: "failed" },
  { label: "Uncertain", value: "uncertain" },
  { label: "Blocked", value: "blocked" },
];

const statusColor: Record<OutboundJobStatus, string> = {
  pending: "text-[#d29922] border-[#d29922]/40 bg-[#d29922]/10",
  processing: "text-blue-400 border-blue-400/40 bg-blue-400/10",
  complete: "text-[#3fb950] border-[#3fb950]/40 bg-[#3fb950]/10",
  failed: "text-[#f85149] border-[#f85149]/40 bg-[#f85149]/10",
  uncertain: "text-[#d29922] border-[#d29922]/40 bg-[#d29922]/10",
  blocked: "text-[#f85149] border-[#f85149]/40 bg-[#f85149]/10",
};

function formatDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function OutboxPage() {
  const [jobs, setJobs] = useState<OutboundJob[]>([]);
  const [selectedJob, setSelectedJob] = useState<OutboundJob | null>(null);
  const [retryEvents, setRetryEvents] = useState<OutboundRetryEvent[]>([]);
  const [status, setStatus] = useState<OutboundJobStatus | "">("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [previousCursors, setPreviousCursors] = useState<(string | undefined)[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [actionError, setActionError] = useState("");

  const loadJobs = useCallback(async (preserveSelection = true) => {
    setLoading(true);
    setError("");
    try {
      const page = await api.getOutboundJobPage(status || undefined, 50, cursor);
      setJobs(page.items);
      setNextCursor(page.next_cursor || null);
      setSelectedJob((previous) => {
        if (preserveSelection && previous) return page.items.find((job) => job.id === previous.id) || page.items[0] || null;
        return page.items[0] || null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the outbound queue.");
      setJobs([]);
      setNextCursor(null);
      setSelectedJob(null);
    } finally {
      setLoading(false);
    }
  }, [status, cursor]);

  useEffect(() => { void loadJobs(false); }, [loadJobs]);

  useEffect(() => {
    if (!selectedJob) {
      setRetryEvents([]);
      setHistoryError("");
      return;
    }
    let active = true;
    setHistoryLoading(true);
    setHistoryError("");
    api.getOutboundRetryEvents(selectedJob.id, 100)
      .then((events) => { if (active) setRetryEvents(events); })
      .catch((err) => { if (active) setHistoryError(err instanceof Error ? err.message : "Could not load retry history."); })
      .finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [selectedJob?.id]);

  const retryJob = async () => {
    if (!selectedJob || (selectedJob.status !== "failed" && selectedJob.status !== "uncertain")) return;
    const confirmDuplicateRisk = selectedJob.status === "uncertain";
    if (confirmDuplicateRisk && !window.confirm("The relay may already have accepted this email. Retrying can send a duplicate. Continue?")) return;
    setRetrying(true);
    setActionError("");
    try {
      const updated = await api.retryOutboundJob(selectedJob.id, confirmDuplicateRisk);
      setSelectedJob(updated);
      try {
        setRetryEvents(await api.getOutboundRetryEvents(updated.id, 100));
      } catch (err) {
        setHistoryError(err instanceof Error ? err.message : "Could not load retry history.");
      }
      await loadJobs(true);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not retry this job.");
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="space-y-4">
      <section className="card-simple p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">Outbound delivery queue</h2>
          <p className="text-xs text-[#8b949e] mt-1">Inspect recent email delivery jobs, retry failed sends, and review operator retry history.</p>
        </div>
        <div className="flex flex-wrap items-center gap-1 rounded border border-[#30363d] bg-[#0d1117] p-1 text-xs">
          {FILTERS.map((filter) => (
            <button key={filter.label} onClick={() => { setPreviousCursors([]); setCursor(undefined); setStatus(filter.value); }} className={`rounded px-2.5 py-1 transition-colors ${status === filter.value ? "bg-[#21262d] text-[#e6edf3]" : "text-[#8b949e] hover:text-[#e6edf3]"}`}>
              {filter.label}
            </button>
          ))}
        </div>
      </section>

      {error && <div role="alert" className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72] flex items-start gap-2"><AlertCircle className="w-4 h-4 shrink-0" /><span>{error}</span></div>}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 min-h-[560px]">
        <section className="xl:col-span-7 card-simple flex flex-col overflow-hidden">
          <div className="p-3 border-b border-[#30363d] flex items-center justify-between text-xs">
            <span className="font-medium text-[#e6edf3]">Jobs <span className="text-[#8b949e]">({jobs.length}{nextCursor ? "+" : ""})</span></span>
            <button onClick={() => void loadJobs()} disabled={loading} className="flex items-center gap-1 text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-50"><RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} /> Refresh</button>
          </div>
          <div className="flex-1 overflow-auto divide-y divide-[#30363d]">
            {loading ? <div className="p-10 text-center text-xs text-[#8b949e]">Loading outbound jobs…</div> : jobs.length === 0 ? <div className="p-10 text-center text-xs text-[#8b949e]">No outbound jobs found.</div> : jobs.map((job) => (
              <button key={job.id} onClick={() => { setSelectedJob(job); setActionError(""); }} className={`block w-full text-left p-3 transition-colors ${selectedJob?.id === job.id ? "bg-[#21262d] border-l-2 border-[#58a6ff]" : "hover:bg-[#1c2128]"}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-[11px] text-[#e6edf3] truncate">{job.id}</span>
                  <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[10px] uppercase font-mono ${statusColor[job.status] || "text-[#8b949e] border-[#30363d]"}`}>{job.status}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#8b949e]">
                  <span>Attempts: <span className="text-[#e6edf3]">{job.attempts}</span></span>
                  <span>Created: {formatDate(job.created_at)}</span>
                </div>
                {job.last_error && <p className="mt-2 text-[11px] text-[#f85149] line-clamp-2">{job.last_error}</p>}
              </button>
            ))}
          </div>
          {!loading && (previousCursors.length > 0 || nextCursor) && <div className="px-3 py-2 border-t border-[#30363d] flex items-center justify-between text-[10px] text-[#8b949e]">
            <span>Page {previousCursors.length + 1}</span>
            <div className="flex gap-2">
              <button disabled={loading || previousCursors.length === 0} onClick={() => { const previous = [...previousCursors]; const target = previous.pop(); setPreviousCursors(previous); setCursor(target); }} className="rounded border border-[#30363d] px-2 py-1 hover:text-[#e6edf3] disabled:opacity-40">Newer</button>
              <button disabled={loading || !nextCursor} onClick={() => { if (!nextCursor) return; setPreviousCursors((previous) => [...previous, cursor]); setCursor(nextCursor); }} className="rounded border border-[#30363d] px-2 py-1 hover:text-[#e6edf3] disabled:opacity-40">Older</button>
            </div>
          </div>}
        </section>

        <section className="xl:col-span-5 card-simple flex flex-col overflow-hidden">
          {selectedJob ? <>
            <div className="p-4 border-b border-[#30363d] space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="text-[10px] text-[#8b949e]">Outbound job</div><h3 className="font-mono text-xs text-[#e6edf3] break-all mt-1">{selectedJob.id}</h3></div>
                <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[10px] uppercase font-mono ${statusColor[selectedJob.status]}`}>{selectedJob.status}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-[11px]">
                <div className="rounded border border-[#30363d] bg-[#0d1117] p-2"><div className="text-[#8b949e]">Message ID</div><div className="mt-1 text-[#e6edf3] font-mono break-all">{selectedJob.message_id}</div></div>
                <div className="rounded border border-[#30363d] bg-[#0d1117] p-2"><div className="text-[#8b949e]">Attempts</div><div className="mt-1 text-[#e6edf3]">{selectedJob.attempts}</div></div>
                <div className="rounded border border-[#30363d] bg-[#0d1117] p-2"><div className="text-[#8b949e]">Available at</div><div className="mt-1 text-[#e6edf3]">{formatDate(selectedJob.available_at)}</div></div>
                <div className="rounded border border-[#30363d] bg-[#0d1117] p-2"><div className="text-[#8b949e]">Created at</div><div className="mt-1 text-[#e6edf3]">{formatDate(selectedJob.created_at)}</div></div>
              </div>
              {selectedJob.last_error && <div className="rounded border border-[#f85149]/30 bg-[#f85149]/10 p-2.5 text-[11px] text-[#ff7b72]"><div className="font-medium mb-1">Last delivery error</div><div className="whitespace-pre-wrap break-words">{selectedJob.last_error}</div></div>}
              {selectedJob.retried_at && <p className="text-[10px] text-[#8b949e]">Last manually retried {formatDate(selectedJob.retried_at)} by <span className="font-mono text-[#e6edf3]">{selectedJob.retried_by || "unknown"}</span></p>}
              {selectedJob.status === "uncertain" && <p className="rounded border border-[#d29922]/30 bg-[#d29922]/10 p-2.5 text-[11px] text-[#d29922]">Delivery could not be confirmed. Retrying may send a duplicate.</p>}
              {(selectedJob.status === "failed" || selectedJob.status === "uncertain") && <button onClick={retryJob} disabled={retrying} className="w-full rounded bg-[#238636] px-3 py-2 text-xs font-medium text-white hover:bg-[#2ea043] disabled:opacity-50 flex items-center justify-center gap-2"><RotateCcw className="w-3.5 h-3.5" />{retrying ? "Queueing retry…" : selectedJob.status === "uncertain" ? "Retry with duplicate risk" : "Retry failed job"}</button>}
              {actionError && <p role="alert" className="text-[11px] text-[#ff7b72]">{actionError}</p>}
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <div className="flex items-center gap-2 mb-3"><Clock3 className="w-3.5 h-3.5 text-[#8b949e]" /><h4 className="text-xs font-semibold text-[#e6edf3]">Retry history</h4><span className="text-[10px] text-[#8b949e]">({retryEvents.length})</span></div>
              {historyLoading ? <div className="text-xs text-[#8b949e]">Loading retry history…</div> : historyError ? <div role="alert" className="text-xs text-[#ff7b72]">{historyError}</div> : retryEvents.length === 0 ? <div className="rounded border border-dashed border-[#30363d] p-4 text-center text-xs text-[#8b949e]">No manual retry events recorded.</div> : <div className="space-y-2">
                {retryEvents.map((event) => <article key={event.id} className="rounded border border-[#30363d] bg-[#0d1117] p-3">
                  <div className="flex items-start justify-between gap-2"><div className="flex items-center gap-1.5 text-xs text-[#e6edf3]"><Send className="w-3 h-3 text-[#8b949e]" />Retry requested</div><time className="text-[10px] text-[#8b949e]">{formatDate(event.requested_at)}</time></div>
                  <div className="mt-2 text-[11px] text-[#8b949e]">By <span className="font-mono text-[#c9d1d9]">{event.actor}</span> · retry from {event.previous_status || "unknown"} · {event.previous_attempts} prior attempt{event.previous_attempts === 1 ? "" : "s"}</div>
                  {event.previous_error && <div className="mt-2 border-l-2 border-[#f85149]/50 pl-2 text-[10px] text-[#ff7b72] whitespace-pre-wrap break-words">{event.previous_error}</div>}
                </article>)}
              </div>}
              {!historyLoading && retryEvents.length === 100 && <p className="mt-2 text-[10px] text-[#8b949e]">Showing the latest 100 retry events.</p>}
            </div>
          </> : <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-xs text-[#8b949e]"><Send className="w-8 h-8 mb-2 text-[#8b949e]" />Select an outbound job to inspect.</div>}
        </section>
      </div>
    </div>
  );
}
