"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw, RotateCcw } from "lucide-react";
import { Agent } from "@/lib/types";
import { api } from "@/lib/api";
import { EventFilters, EventRecord, eventsApi } from "@/lib/ops-api";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";
const eventTypes = [
  "email.received", "email.review_requested", "email.review_approved", "email.review_rejected",
  "email.flagged", "email.blocked", "email.accepted_by_relay", "email.failed", "email.delivery_unknown",
  "email.delivered", "email.bounced", "email.complained", "email.delivery_delayed", "contact.due", "webhook.test",
];
const statuses = ["pending", "processing", "delivered", "failed"];

function when(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function statusStyle(status: string) {
  if (status === "delivered") return "text-[#3fb950] border-[#3fb950]/40 bg-[#3fb950]/10";
  if (status === "failed") return "text-[#f85149] border-[#f85149]/40 bg-[#f85149]/10";
  if (status === "expired") return "text-[#8b949e] border-[#30363d] bg-[#21262d]";
  return "text-[#d29922] border-[#d29922]/40 bg-[#d29922]/10";
}

// An offset-qualified timestamp is required by the API; a date-time-local value is read in the browser's zone.
function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const parsed = new Date(local);
  return Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString();
}

export default function EventsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentEmail, setAgentEmail] = useState("");
  const [eventType, setEventType] = useState("");
  const [status, setStatus] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [items, setItems] = useState<EventRecord[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<EventRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [redelivering, setRedelivering] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    api.getAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  const filters = useCallback((): EventFilters => ({
    agent_email: agentEmail || undefined,
    event_type: eventType || undefined,
    status: status || undefined,
    since: toIso(since),
    until: toIso(until),
  }), [agentEmail, eventType, status, since, until]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await eventsApi.list(filters());
      setItems(page.items);
      setNextCursor(page.next_cursor);
      setSelected((previous) => (previous ? page.items.find((item) => item.id === previous.id) ?? null : null));
    } catch (err) {
      setItems([]);
      setNextCursor(null);
      setError(err instanceof Error ? err.message : "Could not load events.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { void load(); }, [load]);

  const loadMore = async () => {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const page = await eventsApi.list(filters(), 50, nextCursor);
      setItems((previous) => [...previous, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load more events.");
    } finally {
      setLoadingMore(false);
    }
  };

  const open = async (item: EventRecord) => {
    setSelected(item);
    setNotice("");
    setDetailLoading(true);
    try {
      setSelected(await eventsApi.get(item.id));
    } catch (err) {
      // An expired event keeps its row but its payload is gone (HTTP 410).
      setNotice(err instanceof Error ? err.message : "Could not load the event.");
    } finally {
      setDetailLoading(false);
    }
  };

  const redeliver = async () => {
    if (!selected) return;
    if (!window.confirm("Send this event to its webhook endpoint again? The receiver may see it twice, so it must deduplicate by event ID.")) return;
    setRedelivering(true);
    setNotice("");
    try {
      await eventsApi.redeliver(selected.id);
      setNotice("Queued for redelivery. Refresh to see the new status.");
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not redeliver the event.");
    } finally {
      setRedelivering(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-[#e6edf3]"><Activity className="h-5 w-5 text-[#58a6ff]" />Event log</h1>
          <p className="mt-1 text-xs text-[#8b949e]">Every event the gateway produced for your webhooks, newest first. Inspect the payload and send it again if a receiver missed it.</p>
        </div>
        <button onClick={() => void load()} className="inline-flex items-center gap-2 rounded-md border border-[#30363d] px-3 py-2 text-xs text-[#c9d1d9] hover:bg-[#21262d]"><RefreshCw className="h-3.5 w-3.5" />Refresh</button>
      </header>

      <section aria-label="Filters" className="grid gap-3 rounded-lg border border-[#30363d] bg-[#161b22] p-4 sm:grid-cols-2 lg:grid-cols-5">
        <label className="space-y-1 text-xs text-[#8b949e]">Inbox
          <select className={inputClass} value={agentEmail} onChange={(e) => setAgentEmail(e.target.value)}>
            <option value="">All inboxes</option>
            {agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.email}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-[#8b949e]">Event type
          <select className={inputClass} value={eventType} onChange={(e) => setEventType(e.target.value)}>
            <option value="">All types</option>
            {eventTypes.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-[#8b949e]">Status
          <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Any status</option>
            {statuses.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-[#8b949e]">From
          <input type="datetime-local" className={inputClass} value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
        <label className="space-y-1 text-xs text-[#8b949e]">To
          <input type="datetime-local" className={inputClass} value={until} onChange={(e) => setUntil(e.target.value)} />
        </label>
      </section>

      {error && <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-4 py-3 text-xs text-[#ff7b72]">{error}</div>}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
          <div className="flex items-center justify-between border-b border-[#30363d] px-5 py-3"><h2 className="text-sm font-semibold text-[#e6edf3]">Events</h2><span className="text-[11px] text-[#8b949e]">{items.length}{nextCursor ? "+" : ""} shown</span></div>
          {loading ? <p className="p-8 text-center text-xs text-[#8b949e]">Loading events…</p>
            : items.length === 0 ? <p className="p-10 text-center text-xs text-[#8b949e]">No events match these filters.</p>
            : <ul className="divide-y divide-[#30363d]">
              {items.map((item) => <li key={item.id}>
                <button onClick={() => void open(item)} className={`flex w-full items-center justify-between gap-3 px-5 py-3 text-left hover:bg-[#21262d]/60 ${selected?.id === item.id ? "bg-[#21262d]" : ""}`}>
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-[#e6edf3]">{item.event_type}</p>
                    <p className="mt-0.5 truncate text-[11px] text-[#8b949e]">{item.agent_email || "no inbox"} · {when(item.created_at)}</p>
                  </div>
                  <span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] ${statusStyle(item.status)}`}>{item.status}</span>
                </button>
              </li>)}
            </ul>}
          {nextCursor && <div className="border-t border-[#30363d] p-3 text-center"><button disabled={loadingMore} onClick={() => void loadMore()} className="rounded-md border border-[#30363d] px-3 py-1.5 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50">{loadingMore ? "Loading…" : "Load more"}</button></div>}
        </section>

        <aside className="rounded-lg border border-[#30363d] bg-[#161b22] p-5" aria-label="Event detail">
          {!selected ? <p className="text-xs text-[#8b949e]">Select an event to see its payload and delivery history.</p> : <div className="space-y-3">
            <div className="flex items-start justify-between gap-2"><h2 className="break-all font-mono text-sm text-[#e6edf3]">{selected.event_type}</h2><span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] ${statusStyle(selected.status)}`}>{selected.status}</span></div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
              <dt className="text-[#8b949e]">Event ID</dt><dd className="break-all font-mono text-[#c9d1d9]">{selected.id}</dd>
              <dt className="text-[#8b949e]">Inbox</dt><dd className="break-all text-[#c9d1d9]">{selected.agent_email || "—"}</dd>
              <dt className="text-[#8b949e]">Endpoint</dt><dd className="break-all font-mono text-[#c9d1d9]">{selected.endpoint_id || "—"}</dd>
              <dt className="text-[#8b949e]">Created</dt><dd className="text-[#c9d1d9]">{when(selected.created_at)}</dd>
              <dt className="text-[#8b949e]">Delivered</dt><dd className="text-[#c9d1d9]">{when(selected.delivered_at)}</dd>
              <dt className="text-[#8b949e]">Attempts</dt><dd className="text-[#c9d1d9]">{selected.attempts}</dd>
              <dt className="text-[#8b949e]">Redelivered</dt><dd className="text-[#c9d1d9]">{selected.replay_count ?? 0} time(s)</dd>
            </dl>
            {selected.last_error && <p className="rounded border border-[#f85149]/30 bg-[#f85149]/10 p-2.5 text-[11px] text-[#ff7b72]">{selected.last_error}</p>}
            <div>
              <h3 className="mb-1 text-xs font-semibold text-[#e6edf3]">Payload</h3>
              {detailLoading ? <p className="text-xs text-[#8b949e]">Loading…</p> : <pre className="max-h-72 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-3 text-[11px] text-[#c9d1d9]">{selected.payload ? JSON.stringify(selected.payload, null, 2) : "Payload is no longer stored."}</pre>}
            </div>
            {notice && <p role="status" className="rounded border border-[#30363d] bg-[#0d1117] p-2.5 text-[11px] text-[#c9d1d9]">{notice}</p>}
            <button disabled={redelivering || String(selected.status) === "expired"} onClick={() => void redeliver()} className="flex w-full items-center justify-center gap-2 rounded bg-[#238636] px-3 py-2 text-xs font-medium text-white hover:bg-[#2ea043] disabled:opacity-50"><RotateCcw className="h-3.5 w-3.5" />{redelivering ? "Queueing…" : "Redeliver"}</button>
            <p className="text-[10px] text-[#6e7681]">Redelivering needs an administrator key. Payloads are erased after the retention period.</p>
          </div>}
        </aside>
      </div>
    </div>
  );
}
