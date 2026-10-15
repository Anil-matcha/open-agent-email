"use client";

import { useCallback, useEffect, useState } from "react";
import { Ban, Download, Trash2, UserRound, Users } from "lucide-react";
import { AccountMetrics, Agent, Contact } from "@/lib/types";
import { api } from "@/lib/api";
import { AccountSuppression, accountApi } from "@/lib/ops-api";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(1)}%`;
}

function when(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function Tile({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-lg border border-[#30363d] bg-[#161b22] p-4"><p className="text-[11px] text-[#8b949e]">{label}</p><p className="mt-1 text-lg font-semibold text-[#e6edf3]">{value}</p></div>;
}

export default function AccountPage() {
  const [metrics, setMetrics] = useState<AccountMetrics | null>(null);
  const [metricsNote, setMetricsNote] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);

  const [suppressions, setSuppressions] = useState<AccountSuppression[]>([]);
  const [supNext, setSupNext] = useState<string | null>(null);
  const [supAddress, setSupAddress] = useState("");
  const [supLoading, setSupLoading] = useState(true);
  const [supError, setSupError] = useState("");

  const [contacts, setContacts] = useState<Contact[]>([]);
  const [conNext, setConNext] = useState<string | null>(null);
  const [conInbox, setConInbox] = useState("");
  const [conQuery, setConQuery] = useState("");
  const [conLoading, setConLoading] = useState(true);
  const [conError, setConError] = useState("");

  const [exporting, setExporting] = useState(false);
  const [exportNote, setExportNote] = useState("");

  useEffect(() => {
    api.getAgents().then(setAgents).catch(() => setAgents([]));
    api.getAccountMetrics().then(setMetrics).catch(() => setMetricsNote("Account metrics need an administrator credential."));
  }, []);

  const loadSuppressions = useCallback(async (cursor?: string | null) => {
    setSupLoading(true);
    setSupError("");
    try {
      const page = await accountApi.suppressions(supAddress.trim() || undefined, 50, cursor);
      setSuppressions((previous) => (cursor ? [...previous, ...page.items] : page.items));
      setSupNext(page.next_cursor);
    } catch (err) {
      if (!cursor) setSuppressions([]);
      setSupNext(null);
      setSupError(err instanceof Error ? err.message : "Could not load suppressions.");
    } finally {
      setSupLoading(false);
    }
  }, [supAddress]);

  const loadContacts = useCallback(async (cursor?: string | null) => {
    setConLoading(true);
    setConError("");
    try {
      const page = await accountApi.contacts({ agent_email: conInbox || undefined, q: conQuery.trim() || undefined }, 50, cursor);
      setContacts((previous) => (cursor ? [...previous, ...page.items] : page.items));
      setConNext(page.next_cursor);
    } catch (err) {
      if (!cursor) setContacts([]);
      setConNext(null);
      setConError(err instanceof Error ? err.message : "Could not load contacts.");
    } finally {
      setConLoading(false);
    }
  }, [conInbox, conQuery]);

  useEffect(() => { void loadSuppressions(); }, [loadSuppressions]);
  useEffect(() => { void loadContacts(); }, [loadContacts]);

  const removeEverywhere = async (address: string) => {
    if (!window.confirm(`Remove ${address} from the suppression list of every inbox? Future sends to this address will no longer be blocked.`)) return;
    try {
      await accountApi.removeSuppression(address);
      await loadSuppressions();
    } catch (err) {
      setSupError(err instanceof Error ? err.message : "Could not remove the suppression.");
    }
  };

  const downloadExport = async () => {
    setExporting(true);
    setExportNote("");
    try {
      const data = await accountApi.exportAll();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `open-agent-email-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      const truncated = Array.isArray((data as { truncated_collections?: unknown }).truncated_collections) ? (data as { truncated_collections: string[] }).truncated_collections : [];
      setExportNote(truncated.length ? `Downloaded. These collections were cut at the export limit: ${truncated.join(", ")}.` : "Downloaded.");
    } catch (err) {
      setExportNote(err instanceof Error ? err.message : "Could not export the account.");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-8">
      <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-[#e6edf3]"><UserRound className="h-5 w-5 text-[#58a6ff]" />Account</h1>
          <p className="mt-1 text-xs text-[#8b949e]">Everything across all inboxes: delivery numbers, blocked recipients, contacts, and a backup export.</p>
        </div>
        <div className="space-y-1 text-right">
          <button disabled={exporting} onClick={() => void downloadExport()} className="inline-flex items-center gap-2 rounded-md border border-[#30363d] px-3 py-2 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50"><Download className="h-3.5 w-3.5" />{exporting ? "Preparing…" : "Export account data"}</button>
          {exportNote && <p role="status" className="text-[11px] text-[#8b949e]">{exportNote}</p>}
        </div>
      </header>

      <section aria-label="Delivery metrics" className="space-y-3">
        <h2 className="text-sm font-semibold text-[#e6edf3]">Delivery, last 30 days</h2>
        {metrics ? <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Received" value={metrics.summary.received} />
            <Tile label="Delivered" value={metrics.summary.delivered} />
            <Tile label="Delivered rate" value={percent(metrics.rates.delivered_rate)} />
            <Tile label="Bounce rate" value={percent(metrics.rates.bounce_rate)} />
            <Tile label="Complaint rate" value={percent(metrics.rates.complaint_rate)} />
            <Tile label="Blocked by suppression" value={metrics.summary.suppressed} />
            <Tile label="Held for review" value={metrics.summary.review_held} />
            <Tile label="Send failures" value={metrics.summary.send_failed} />
          </div>
          <p className="text-[11px] text-[#6e7681]">{when(metrics.start)} to {when(metrics.end)} · {metrics.messages_in_window} messages across {metrics.agents.length} inbox(es).</p>
        </> : <p className="rounded-lg border border-dashed border-[#30363d] p-6 text-center text-xs text-[#8b949e]">{metricsNote || "Loading metrics…"}</p>}
      </section>

      <section aria-label="Suppressions" className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
        <div className="flex flex-col gap-3 border-b border-[#30363d] px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e6edf3]"><Ban className="h-4 w-4 text-[#8b949e]" />Blocked recipients, all inboxes</h2>
          <input aria-label="Filter by address" className={`${inputClass} sm:w-72`} placeholder="Exact address…" value={supAddress} onChange={(e) => setSupAddress(e.target.value)} />
        </div>
        {supError && <div role="alert" className="border-b border-[#30363d] bg-[#f85149]/10 px-5 py-3 text-xs text-[#ff7b72]">{supError}</div>}
        {supLoading && suppressions.length === 0 ? <p className="p-8 text-center text-xs text-[#8b949e]">Loading…</p>
          : suppressions.length === 0 ? <p className="p-8 text-center text-xs text-[#8b949e]">No blocked recipients.</p>
          : <table className="w-full text-left text-xs"><thead className="text-[11px] text-[#8b949e]"><tr><th className="px-5 py-2 font-medium">Address</th><th className="px-3 py-2 font-medium">Inbox</th><th className="px-3 py-2 font-medium">Reason</th><th className="px-3 py-2 font-medium">Added</th><th className="px-3 py-2" /></tr></thead>
            <tbody className="divide-y divide-[#30363d]">{suppressions.map((row, index) => <tr key={`${row.id ?? row.email}-${index}`}>
              <td className="px-5 py-2.5 font-mono text-[#e6edf3]">{row.email}</td>
              <td className="px-3 py-2.5 text-[#8b949e]">{row.agent_email}</td>
              <td className="px-3 py-2.5 text-[#8b949e]">{row.reason || "—"}</td>
              <td className="px-3 py-2.5 text-[#8b949e]">{when(row.created_at)}</td>
              <td className="px-3 py-2.5 text-right"><button aria-label={`Remove ${row.email} from every inbox`} onClick={() => void removeEverywhere(row.email)} className="rounded p-1.5 text-[#8b949e] hover:bg-[#3d1f1f] hover:text-[#ff7b72]"><Trash2 className="h-3.5 w-3.5" /></button></td>
            </tr>)}</tbody></table>}
        {supNext && <div className="border-t border-[#30363d] p-3 text-center"><button disabled={supLoading} onClick={() => void loadSuppressions(supNext)} className="rounded-md border border-[#30363d] px-3 py-1.5 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50">Load more</button></div>}
        <p className="border-t border-[#30363d] px-5 py-3 text-[11px] text-[#6e7681]">Removing an address clears it from every inbox that blocked it. To block an address in one inbox, use Suppressions.</p>
      </section>

      <section aria-label="Contacts" className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
        <div className="flex flex-col gap-3 border-b border-[#30363d] px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e6edf3]"><Users className="h-4 w-4 text-[#8b949e]" />Contacts, all inboxes</h2>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select aria-label="Filter by inbox" className={`${inputClass} sm:w-64`} value={conInbox} onChange={(e) => setConInbox(e.target.value)}>
              <option value="">All inboxes</option>
              {agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.email}</option>)}
            </select>
            <input aria-label="Search contacts" className={`${inputClass} sm:w-64`} placeholder="Address, name or company…" value={conQuery} onChange={(e) => setConQuery(e.target.value)} />
          </div>
        </div>
        {conError && <div role="alert" className="border-b border-[#30363d] bg-[#f85149]/10 px-5 py-3 text-xs text-[#ff7b72]">{conError}</div>}
        {conLoading && contacts.length === 0 ? <p className="p-8 text-center text-xs text-[#8b949e]">Loading…</p>
          : contacts.length === 0 ? <p className="p-8 text-center text-xs text-[#8b949e]">No contacts found.</p>
          : <table className="w-full text-left text-xs"><thead className="text-[11px] text-[#8b949e]"><tr><th className="px-5 py-2 font-medium">Address</th><th className="px-3 py-2 font-medium">Name</th><th className="px-3 py-2 font-medium">Company</th><th className="px-3 py-2 font-medium">Inbox</th></tr></thead>
            <tbody className="divide-y divide-[#30363d]">{contacts.map((contact) => <tr key={contact.id}>
              <td className="px-5 py-2.5 font-mono text-[#e6edf3]">{contact.email}</td>
              <td className="px-3 py-2.5 text-[#c9d1d9]">{contact.name || "—"}</td>
              <td className="px-3 py-2.5 text-[#8b949e]">{contact.company || "—"}</td>
              <td className="px-3 py-2.5 text-[#8b949e]">{contact.agent_email}</td>
            </tr>)}</tbody></table>}
        {conNext && <div className="border-t border-[#30363d] p-3 text-center"><button disabled={conLoading} onClick={() => void loadContacts(conNext)} className="rounded-md border border-[#30363d] px-3 py-1.5 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50">Load more</button></div>}
        <p className="border-t border-[#30363d] px-5 py-3 text-[11px] text-[#6e7681]">This is a read-only view over each inbox's own contacts. Edit or import them on the Contacts page.</p>
      </section>
    </div>
  );
}
