"use client";

import { useCallback, useEffect, useState } from "react";
import { Contact, Agent, ContactConsentHistory, ContactImportBatch, ContactImportResult } from "@/lib/types";
import { api } from "@/lib/api";
import { ContactRound, Pencil, Plus, Trash2, X } from "lucide-react";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";
type ContactFields = Pick<Contact, "email" | "name" | "company" | "notes">;
const emptyFields: ContactFields = { email: "", name: "", company: "", notes: "" };

export default function ContactsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentEmail, setAgentEmail] = useState("");
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [fields, setFields] = useState<ContactFields>(emptyFields);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imports, setImports] = useState<ContactImportBatch[]>([]);
  const [importResult, setImportResult] = useState<ContactImportResult | null>(null);
  const [importing, setImporting] = useState(false);
  const [consent, setConsent] = useState<Record<string, ContactConsentHistory>>({});
  const [consentOpen, setConsentOpen] = useState<string | null>(null);
  const [consentAction, setConsentAction] = useState<"granted" | "revoked">("granted");
  const [consentSource, setConsentSource] = useState("");
  const [consentEvidence, setConsentEvidence] = useState("");
  const [consentNote, setConsentNote] = useState("");

  useEffect(() => {
    api.getAgents().then((items) => {
      setAgents(items);
      setAgentEmail(items[0]?.email ?? "");
    }).catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load inboxes"));
  }, []);

  const loadContacts = useCallback(async () => {
    if (!agentEmail) { setContacts([]); setLoading(false); return; }
    try {
      setLoading(true);
      setContacts(await api.getContacts(agentEmail));
      setImports(await api.getContactImports(agentEmail));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load contacts");
    } finally { setLoading(false); }
  }, [agentEmail]);

  useEffect(() => { void loadContacts(); }, [loadContacts]);

  const resetForm = () => { setFields(emptyFields); setEditingId(null); };
  const editContact = (contact: Contact) => {
    setEditingId(contact.id);
    setFields({ email: contact.email, name: contact.name, company: contact.company, notes: contact.notes });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveContact = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!agentEmail) return;
    try {
      setSaving(true); setError(null);
      if (editingId) await api.updateContact(agentEmail, editingId, fields);
      else await api.createContact(agentEmail, fields);
      resetForm();
      await loadContacts();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not save contact"); }
    finally { setSaving(false); }
  };

  const removeContact = async (contact: Contact) => {
    if (!agentEmail || !window.confirm(`Delete ${contact.name} (${contact.email})?`)) return;
    try { await api.deleteContact(agentEmail, contact.id); await loadContacts(); }
    catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not delete contact"); }
  };

  const importCsv = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !agentEmail) return;
    try {
      setImporting(true); setError(null); setImportResult(null);
      const result = await api.importContactsCsv(agentEmail, await file.text());
      setImportResult(result);
      await loadContacts();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not import contacts"); }
    finally { setImporting(false); }
  };

  const reverseImport = async (batchId: string) => {
    if (!agentEmail || !window.confirm("Reverse this import? Contacts edited after import will be kept.")) return;
    try {
      const result = await api.reverseContactImport(agentEmail, batchId);
      setImportResult(null);
      setError(`Import reversed: ${result.deleted} removed, ${result.retained_after_edits} edited contact(s) kept.`);
      await loadContacts();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not reverse import"); }
  };

  const toggleConsent = async (contactId: string) => {
    if (!agentEmail) return;
    if (consentOpen === contactId) { setConsentOpen(null); return; }
    setConsentOpen(contactId);
    try {
      const history = await api.getContactConsent(agentEmail, contactId);
      setConsent((previous) => ({ ...previous, [contactId]: history }));
    }
    catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not load consent history"); }
  };

  const saveConsent = async (event: React.FormEvent, contactId: string) => {
    event.preventDefault();
    if (!agentEmail || !consentSource.trim()) return;
    try {
      const result = await api.recordContactConsent(agentEmail, contactId, { action: consentAction, source: consentSource, evidence: consentEvidence, note: consentNote });
      setConsent((previous) => ({ ...previous, [contactId]: result }));
      setConsentSource(""); setConsentEvidence(""); setConsentNote(""); setError(null);
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not record consent change"); }
  };

  const visibleContacts = contacts.filter((contact) =>
    `${contact.name} ${contact.email} ${contact.company}`.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-[#e6edf3]">Contacts</h1>
          <p className="mt-1 text-xs text-[#8b949e]">Keep a simple address book for each agent inbox.</p>
        </div>
        <label className="text-xs text-[#8b949e]">Inbox
          <select className={`${inputClass} mt-1 sm:w-80`} value={agentEmail} onChange={(event) => { setAgentEmail(event.target.value); resetForm(); }}>
            {agents.length === 0 && <option value="">No inboxes configured</option>}
            {agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.display_name} · {agent.email}</option>)}
          </select>
        </label>
      </header>

      {error && <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-4 py-3 text-xs text-[#ff7b72]">{error}</div>}

      {agentEmail && <form onSubmit={saveContact} className="space-y-4 rounded-lg border border-[#30363d] bg-[#161b22] p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#e6edf3]">{editingId ? "Edit contact" : "Add a contact"}</h2>
          {editingId && <button type="button" onClick={resetForm} className="flex items-center gap-1 text-xs text-[#8b949e] hover:text-white"><X className="h-3.5 w-3.5"/>Cancel edit</button>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-[#8b949e]">Name<input required maxLength={255} className={inputClass} value={fields.name} onChange={(e) => setFields({ ...fields, name: e.target.value })} /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Email<input required type="email" maxLength={255} className={inputClass} value={fields.email} onChange={(e) => setFields({ ...fields, email: e.target.value })} /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Company<input maxLength={255} className={inputClass} value={fields.company} onChange={(e) => setFields({ ...fields, company: e.target.value })} /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Notes<textarea maxLength={4000} rows={2} className={inputClass} value={fields.notes} onChange={(e) => setFields({ ...fields, notes: e.target.value })} /></label>
        </div>
        <button disabled={saving} className="inline-flex items-center gap-2 rounded-md bg-[#238636] px-3 py-2 text-xs font-medium text-white hover:bg-[#2ea043] disabled:opacity-50"><Plus className="h-3.5 w-3.5"/>{saving ? "Saving…" : editingId ? "Save contact" : "Add contact"}</button>
      </form>}

      {agentEmail && <section className="space-y-3 rounded-lg border border-[#30363d] bg-[#161b22] p-5">
        <div><h2 className="text-sm font-semibold text-[#e6edf3]">Import contacts from CSV</h2><p className="mt-1 text-xs text-[#8b949e]">Headers: email, name, company, notes. Up to 1,000 rows and 1 MB. Duplicate and suppressed addresses are skipped and reported.</p></div>
        <input aria-label="Choose contacts CSV" type="file" accept=".csv,text/csv" disabled={importing} onChange={(event) => void importCsv(event)} className="text-xs text-[#8b949e] file:mr-3 file:rounded file:border-0 file:bg-[#21262d] file:px-3 file:py-2 file:text-xs file:text-white" />
        {importing && <p className="text-xs text-[#8b949e]">Importing…</p>}
        {importResult && <div className="space-y-2 text-xs text-[#c9d1d9]"><p>Created {importResult.created}; skipped {importResult.skipped}. {importResult.notice}</p>{importResult.results.filter((row) => row.status === "skipped").slice(0, 15).map((row) => <p key={`${row.row}-${row.email}`} className="text-[#8b949e]">Row {row.row}: {row.email || "(blank email)"} — {row.reason}</p>)}</div>}
        {imports.length > 0 && <div className="space-y-2 border-t border-[#30363d] pt-3"><p className="text-xs font-medium text-[#c9d1d9]">Recent import batches</p>{imports.map((batch) => <div key={batch.batch_id} className="flex items-center justify-between gap-3 text-xs"><span className="text-[#8b949e]">{new Date(batch.created_at).toLocaleString()} · {batch.created} created · {batch.batch_id}</span><button onClick={() => void reverseImport(batch.batch_id)} className="text-[#ff7b72] hover:underline">Reverse</button></div>)}</div>}
      </section>}

      <section className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
        <div className="flex flex-col justify-between gap-3 border-b border-[#30363d] p-4 sm:flex-row sm:items-center">
          <h2 className="text-sm font-semibold text-[#e6edf3]">Address book <span className="ml-1 text-xs font-normal text-[#8b949e]">{contacts.length}</span></h2>
          <input aria-label="Search contacts" placeholder="Search name, email, company" className={`${inputClass} sm:w-72`} value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {loading ? <p className="p-8 text-center text-xs text-[#8b949e]">Loading contacts…</p>
          : visibleContacts.length === 0 ? <div className="p-10 text-center"><ContactRound className="mx-auto mb-2 h-6 w-6 text-[#8b949e]"/><p className="text-xs text-[#8b949e]">{contacts.length ? "No contacts match your search." : "No contacts saved for this inbox yet."}</p></div>
          : <div className="divide-y divide-[#30363d]">{visibleContacts.map((contact) => <div key={contact.id}>
          <article className="flex items-start justify-between gap-4 px-4 py-3">
            <div className="min-w-0"><p className="truncate text-sm font-medium text-[#e6edf3]">{contact.name}</p><a className="mt-0.5 block truncate text-xs text-[#58a6ff] hover:underline" href={`mailto:${contact.email}`}>{contact.email}</a>{contact.company && <p className="mt-1 text-xs text-[#8b949e]">{contact.company}</p>}{contact.notes && <p className="mt-1 whitespace-pre-wrap text-xs text-[#8b949e]">{contact.notes}</p>}</div>
            <div className="flex shrink-0 gap-1"><button aria-label={`Consent for ${contact.name}`} onClick={() => void toggleConsent(contact.id)} className="rounded px-2 py-1 text-xs text-[#8b949e] hover:bg-[#21262d] hover:text-white">Consent{consent[contact.id] && <span className="ml-1 text-[#c9d1d9]">· {consent[contact.id].current}</span>}</button><button aria-label={`Edit ${contact.name}`} onClick={() => editContact(contact)} className="rounded p-2 text-[#8b949e] hover:bg-[#21262d] hover:text-white"><Pencil className="h-3.5 w-3.5"/></button><button aria-label={`Delete ${contact.name}`} onClick={() => void removeContact(contact)} className="rounded p-2 text-[#8b949e] hover:bg-[#3d1f1f] hover:text-[#ff7b72]"><Trash2 className="h-3.5 w-3.5"/></button></div>
          </article>
          {consentOpen === contact.id && <div className="space-y-3 border-t border-[#30363d] bg-[#0d1117] px-4 py-4">
            <p className="text-xs text-[#8b949e]">Recorded state: <strong className="text-[#c9d1d9]">{consent[contact.id]?.current ?? "loading"}</strong>. This record does not enable or block sending.</p>
            <form onSubmit={(event) => void saveConsent(event, contact.id)} className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs text-[#8b949e]">Change<select className={`${inputClass} mt-1`} value={consentAction} onChange={(event) => setConsentAction(event.target.value as "granted" | "revoked")}><option value="granted">Record grant</option><option value="revoked">Record revocation</option></select></label>
              <label className="text-xs text-[#8b949e]">Source (required)<input required maxLength={255} placeholder="e.g. verbal confirmation" className={`${inputClass} mt-1`} value={consentSource} onChange={(event) => setConsentSource(event.target.value)}/></label>
              <label className="text-xs text-[#8b949e]">Evidence reference<input maxLength={1000} placeholder="Optional URL or reference" className={`${inputClass} mt-1`} value={consentEvidence} onChange={(event) => setConsentEvidence(event.target.value)}/></label>
              <label className="text-xs text-[#8b949e]">Note<input maxLength={1000} className={`${inputClass} mt-1`} value={consentNote} onChange={(event) => setConsentNote(event.target.value)}/></label>
              <button className="w-fit rounded bg-[#238636] px-3 py-2 text-xs text-white">Append consent record</button>
            </form>
            {(consent[contact.id]?.events ?? []).map((item) => <p key={item.id} className="text-xs text-[#8b949e]">{item.action} · {new Date(item.created_at).toLocaleString()} · {item.source}{item.evidence && ` · ${item.evidence}`}{item.note && ` · ${item.note}`}</p>)}
          </div>}
          </div>)}</div>}
      </section>
      <p className="text-[11px] text-[#8b949e]">Contacts and consent history are stored per inbox. Consent entries are operator-entered records only; they do not authorize, block, or trigger email.</p>
    </div>
  );
}
