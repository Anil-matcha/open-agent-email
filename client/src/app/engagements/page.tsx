"use client";

import { useCallback, useEffect, useState } from "react";
import { Agent, Contact, ContactEngagement } from "@/lib/types";
import { api } from "@/lib/api";
import { CalendarClock, Plus, Trash2 } from "lucide-react";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";
const stages = ["new", "contacted", "waiting", "closed"] as const;
const localInput = (iso: string | null) => {
  if (!iso) return "";
  const date = new Date(iso);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
};

export default function EngagementsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentEmail, setAgentEmail] = useState("");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [items, setItems] = useState<ContactEngagement[]>([]);
  const [contactId, setContactId] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [notes, setNotes] = useState("");
  const [stage, setStage] = useState<(typeof stages)[number]>("new");
  const [dueOnly, setDueOnly] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.getAgents().then((rows) => { setAgents(rows); setAgentEmail(rows[0]?.email ?? ""); }).catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not load inboxes")); }, []);
  const load = useCallback(async () => {
    if (!agentEmail) { setItems([]); setContacts([]); return; }
    try {
      const [engagements, addressBook] = await Promise.all([api.getEngagements(agentEmail, dueOnly), api.getContacts(agentEmail)]);
      setItems(engagements); setContacts(addressBook); setError("");
      if (!contactId && addressBook[0]) setContactId(addressBook[0].id);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Could not load follow-ups"); }
  }, [agentEmail, dueOnly, contactId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!agentEmail || !dueOnly) return;
    const timer = window.setInterval(() => { void load(); }, 60_000);
    return () => window.clearInterval(timer);
  }, [agentEmail, dueOnly, load]);

  const add = async (event: React.FormEvent) => {
    event.preventDefault(); if (!agentEmail || !contactId) return;
    try {
      setBusy(true); setError("");
      await api.createEngagement(agentEmail, { contact_id: contactId, stage, next_due_at: dueAt ? new Date(dueAt).toISOString() : null, notes });
      setDueAt(""); setNotes(""); await load();
    } catch (e: unknown) { setError(e instanceof Error ? e.message : "Could not save follow-up"); }
    finally { setBusy(false); }
  };
  const update = async (item: ContactEngagement, changes: { stage?: string; next_due_at?: string | null; notes?: string }) => {
    try { setError(""); await api.updateEngagement(agentEmail, item.id, changes); await load(); }
    catch (e: unknown) { setError(e instanceof Error ? e.message : "Could not update follow-up"); }
  };
  const dateValue = (value: string) => value ? new Date(value).toISOString() : null;

  return <div className="mx-auto max-w-6xl space-y-6 p-8">
    <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
      <div><h1 className="text-xl font-bold text-[#e6edf3]">Human follow-ups</h1><p className="mt-1 text-xs text-[#8b949e]">Track a contact stage and the next time a person should follow up. Due dates only appear here; they never send email.</p></div>
      <label className="text-xs text-[#8b949e]">Inbox<select className={`${inputClass} mt-1 sm:w-80`} value={agentEmail} onChange={(e) => setAgentEmail(e.target.value)}>{agents.map((a) => <option key={a.id} value={a.email}>{a.display_name} · {a.email}</option>)}</select></label>
    </header>
    {error && <div role="alert" className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72]">{error}</div>}
    {agentEmail && <form onSubmit={add} className="grid gap-3 rounded-lg border border-[#30363d] bg-[#161b22] p-5 sm:grid-cols-2">
      <h2 className="sm:col-span-2 text-sm font-semibold text-[#e6edf3]">Create follow-up</h2>
      <label className="text-xs text-[#8b949e]">Contact<select required className={`${inputClass} mt-1`} value={contactId} onChange={(e) => setContactId(e.target.value)}><option value="">Choose a contact</option>{contacts.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.email}</option>)}</select></label>
      <label className="text-xs text-[#8b949e]">Stage<select className={`${inputClass} mt-1`} value={stage} onChange={(e) => setStage(e.target.value as (typeof stages)[number])}>{stages.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
      <label className="text-xs text-[#8b949e]">Next due time<input type="datetime-local" className={`${inputClass} mt-1`} value={dueAt} onChange={(e) => setDueAt(e.target.value)} /></label>
      <label className="text-xs text-[#8b949e]">Notes<textarea maxLength={4000} rows={2} className={`${inputClass} mt-1`} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
      <button disabled={busy || contacts.length === 0} className="inline-flex w-fit items-center gap-2 rounded bg-[#238636] px-3 py-2 text-xs text-white disabled:opacity-50"><Plus className="h-3.5 w-3.5"/>Save follow-up</button>
    </form>}
    <section className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
      <div className="flex items-center justify-between border-b border-[#30363d] p-4"><h2 className="text-sm font-semibold text-[#e6edf3]">Follow-up records</h2><label className="flex items-center gap-2 text-xs text-[#8b949e]"><input type="checkbox" checked={dueOnly} onChange={(e) => setDueOnly(e.target.checked)} />Due now</label></div>
      {items.length === 0 ? <div className="p-10 text-center text-xs text-[#8b949e]"><CalendarClock className="mx-auto mb-2 h-6 w-6"/>{dueOnly ? "No unsuppressed follow-ups are due." : "No follow-ups yet."}</div> : <div className="divide-y divide-[#30363d]">{items.map((item) => <article key={item.id} className="grid gap-3 px-4 py-4 sm:grid-cols-[1fr_9rem_13rem] sm:items-start">
        <div className="min-w-0"><p className="text-sm font-medium text-[#e6edf3]">{item.contact_name} <span className="font-normal text-[#8b949e]">· {item.contact_email}</span></p>{item.suppressed && <p className="mt-1 text-xs text-[#ff7b72]">Suppressed: follow-up scheduling is disabled.</p>}{item.replied_at && <p className="mt-1 text-xs text-[#3fb950]">Authenticated reply received {new Date(item.replied_at).toLocaleString()}</p>}<textarea aria-label={`Notes for ${item.contact_name}`} maxLength={4000} defaultValue={item.notes} onBlur={(e) => e.target.value !== item.notes && void update(item, { notes: e.target.value })} className={`${inputClass} mt-2`} rows={2}/></div>
        <select aria-label="Stage" className={inputClass} value={item.stage} onChange={(e) => void update(item, { stage: e.target.value })}><option value="new">new</option><option value="contacted">contacted</option><option value="waiting">waiting</option><option value="closed">closed</option></select>
        <div><label className="text-xs text-[#8b949e]">Next due<input type="datetime-local" disabled={item.suppressed || item.replied || item.stage === "closed"} className={`${inputClass} mt-1`} value={localInput(item.next_due_at)} onChange={(e) => void update(item, { next_due_at: dateValue(e.target.value) })}/>{item.next_due_at && <span className="mt-1 block">{new Date(item.next_due_at).toLocaleString()}</span>}</label><button aria-label={`Delete follow-up for ${item.contact_name}`} onClick={async () => { try { await api.deleteEngagement(agentEmail, item.id); await load(); } catch (e: unknown) { setError(e instanceof Error ? e.message : "Could not delete follow-up"); } }} className="mt-2 inline-flex items-center gap-1 text-xs text-[#ff7b72]"><Trash2 className="h-3.5 w-3.5"/>Delete</button></div>
      </article>)}</div>}
    </section>
    <p className="text-[11px] text-[#8b949e]">An authenticated inbound reply marks the contact as replied. Suppressed contacts are excluded from the due list and cannot be scheduled. No automated outreach is performed.</p>
  </div>;
}
