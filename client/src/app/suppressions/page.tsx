"use client";

import { useCallback, useEffect, useState } from "react";
import { Agent, RecipientSuppression } from "@/lib/types";
import { api } from "@/lib/api";
import { Ban, Trash2 } from "lucide-react";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";

export default function SuppressionsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentEmail, setAgentEmail] = useState("");
  const [entries, setEntries] = useState<RecipientSuppression[]>([]);
  const [email, setEmail] = useState("");
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getAgents().then((items) => {
      setAgents(items);
      setAgentEmail(items[0]?.email ?? "");
    }).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load inboxes");
      setLoading(false);
    });
  }, []);

  const loadEntries = useCallback(async () => {
    if (!agentEmail) { setEntries([]); setLoading(false); return; }
    try {
      setLoading(true);
      setEntries(await api.getSuppressions(agentEmail));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load suppressions");
    } finally { setLoading(false); }
  }, [agentEmail]);

  useEffect(() => { void loadEntries(); }, [loadEntries]);

  const addEntry = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!agentEmail) return;
    try {
      setSaving(true); setError(null);
      await api.addSuppression(agentEmail, { email, reason: reason.trim() || undefined });
      setEmail(""); setReason("");
      await loadEntries();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not add suppression");
    } finally { setSaving(false); }
  };

  const removeEntry = async (entry: RecipientSuppression) => {
    if (!agentEmail || !window.confirm(`Remove ${entry.email} from this inbox's suppression list? Future sends to this address will no longer be blocked by this entry.`)) return;
    try {
      await api.deleteSuppression(agentEmail, entry.id);
      await loadEntries();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not remove suppression");
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-[#e6edf3]"><Ban className="h-5 w-5 text-[#f0883e]" />Recipient suppressions</h1>
          <p className="mt-1 text-xs text-[#8b949e]">Prevent an inbox from sending to specific recipient addresses.</p>
        </div>
        <label className="text-xs text-[#8b949e]">Inbox
          <select className={`${inputClass} mt-1 sm:w-80`} value={agentEmail} onChange={(event) => setAgentEmail(event.target.value)}>
            {agents.length === 0 && <option value="">No inboxes configured</option>}
            {agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.display_name} · {agent.email}</option>)}
          </select>
        </label>
      </header>

      <div role="note" className="rounded-md border border-[#d29922]/40 bg-[#d29922]/10 px-4 py-3 text-xs leading-5 text-[#e3b341]">
        A suppression blocks outbound sending to this address from the selected inbox. It is an operational sending control; it does not record an unsubscribe request, consent, or legal basis. Keep consent and unsubscribe records in the system of record you use for those purposes.
      </div>

      {error && <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-4 py-3 text-xs text-[#ff7b72]">{error}</div>}

      {agentEmail && <form onSubmit={addEntry} className="space-y-4 rounded-lg border border-[#30363d] bg-[#161b22] p-5">
        <h2 className="text-sm font-semibold text-[#e6edf3]">Suppress a recipient</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-[#8b949e]">Email address<input required type="email" maxLength={320} className={inputClass} value={email} onChange={(event) => setEmail(event.target.value)} placeholder="person@example.com" /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Reason (optional)<input maxLength={500} className={inputClass} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why should sending be blocked?" /></label>
        </div>
        <button disabled={saving} className="rounded-md bg-[#238636] px-4 py-2 text-xs font-semibold text-white hover:bg-[#2ea043] disabled:opacity-50">{saving ? "Adding…" : "Add suppression"}</button>
      </form>}

      <section className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
        <div className="flex items-center justify-between border-b border-[#30363d] px-5 py-4">
          <h2 className="text-sm font-semibold text-[#e6edf3]">Suppressed recipients</h2>
          <span className="text-[11px] text-[#8b949e]">{entries.length} entries</span>
        </div>
        {loading ? <div className="p-6 text-xs text-[#8b949e]">Loading suppressions…</div>
          : !agentEmail ? <div className="p-6 text-xs text-[#8b949e]">Create an inbox to manage suppressions.</div>
          : entries.length === 0 ? <div className="p-6 text-xs text-[#8b949e]">No recipients are suppressed for this inbox.</div>
          : <div className="divide-y divide-[#30363d]">{entries.map((entry) => <div key={entry.id} className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0"><div className="break-all text-xs font-medium text-[#e6edf3]">{entry.email}</div><div className="mt-1 text-[11px] text-[#8b949e]">{entry.reason || "No reason provided"} · Added {new Date(entry.created_at).toLocaleString()}</div></div>
            <button type="button" onClick={() => void removeEntry(entry)} aria-label={`Remove suppression for ${entry.email}`} className="flex shrink-0 items-center gap-1.5 self-start rounded-md border border-[#30363d] px-2.5 py-1.5 text-xs text-[#ff7b72] hover:bg-[#f85149]/10 sm:self-auto"><Trash2 className="h-3.5 w-3.5" />Remove</button>
          </div>)}</div>}
      </section>
    </div>
  );
}
