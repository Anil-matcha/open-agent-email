"use client";

import { useCallback, useEffect, useState } from "react";
import { Agent, EmailTemplate, EmailTemplatePreview } from "@/lib/types";
import { api } from "@/lib/api";
import { FileText, Pencil, Plus, Trash2, X } from "lucide-react";

const inputClass = "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-xs text-[#e6edf3] outline-none focus:border-[#58a6ff]";
type TemplateFields = Pick<EmailTemplate, "name" | "subject" | "body_text" | "body_html">;
const emptyFields: TemplateFields = { name: "", subject: "", body_text: "", body_html: "" };
const exampleData = '{\n  "name": "Alex",\n  "company": "Acme"\n}';

export default function TemplatesPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentEmail, setAgentEmail] = useState("");
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [fields, setFields] = useState<TemplateFields>(emptyFields);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState(exampleData);
  const [preview, setPreview] = useState<EmailTemplatePreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getAgents().then((items) => {
      setAgents(items);
      setAgentEmail(items[0]?.email ?? "");
    }).catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load inboxes"));
  }, []);

  const loadTemplates = useCallback(async () => {
    if (!agentEmail) { setTemplates([]); setLoading(false); return; }
    try {
      setLoading(true);
      setTemplates(await api.getEmailTemplates(agentEmail));
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load templates");
    } finally { setLoading(false); }
  }, [agentEmail]);

  useEffect(() => { void loadTemplates(); }, [loadTemplates]);

  const resetForm = () => { setFields(emptyFields); setEditingId(null); setPreview(null); };
  const editTemplate = (template: EmailTemplate) => {
    setEditingId(template.id);
    setFields({ name: template.name, subject: template.subject, body_text: template.body_text, body_html: template.body_html });
    setPreview(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveTemplate = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!agentEmail) return;
    try {
      setSaving(true); setError(null);
      if (editingId) await api.updateEmailTemplate(agentEmail, editingId, fields);
      else await api.createEmailTemplate(agentEmail, fields);
      resetForm();
      await loadTemplates();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not save template"); }
    finally { setSaving(false); }
  };

  const removeTemplate = async (template: EmailTemplate) => {
    if (!agentEmail || !window.confirm(`Delete the “${template.name}” template?`)) return;
    try {
      await api.deleteEmailTemplate(agentEmail, template.id);
      if (editingId === template.id) resetForm();
      await loadTemplates();
    } catch (err: unknown) { setError(err instanceof Error ? err.message : "Could not delete template"); }
  };

  const runPreview = async () => {
    if (!agentEmail) return;
    try {
      setPreviewing(true); setError(null); setPreview(null);
      const data: unknown = JSON.parse(previewData);
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("Preview data must be a JSON object.");
      setPreview(await api.previewEmailTemplate(agentEmail, { ...fields, data: data as Record<string, unknown> }));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not preview template");
    } finally { setPreviewing(false); }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-col justify-between gap-4 border-b border-[#30363d] pb-6 sm:flex-row sm:items-center">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold tracking-tight text-[#e6edf3]"><FileText className="h-5 w-5 text-[#58a6ff]" />Email templates</h1>
          <p className="mt-1 text-xs text-[#8b949e]">Create reusable, inbox-scoped messages with simple {"{{variable}}"} placeholders.</p>
        </div>
        <label className="text-xs text-[#8b949e]">Inbox
          <select className={`${inputClass} mt-1 sm:w-80`} value={agentEmail} onChange={(event) => { setAgentEmail(event.target.value); resetForm(); }}>
            {agents.length === 0 && <option value="">No inboxes configured</option>}
            {agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.display_name} · {agent.email}</option>)}
          </select>
        </label>
      </header>

      {error && <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-4 py-3 text-xs text-[#ff7b72]">{error}</div>}

      {agentEmail && <form onSubmit={saveTemplate} className="space-y-4 rounded-lg border border-[#30363d] bg-[#161b22] p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-[#e6edf3]">{editingId ? "Edit template" : "Create a template"}</h2>
          {editingId && <button type="button" onClick={resetForm} className="flex items-center gap-1 text-xs text-[#8b949e] hover:text-white"><X className="h-3.5 w-3.5"/>Cancel edit</button>}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-[#8b949e]">Name<input required maxLength={255} className={inputClass} value={fields.name} onChange={(e) => setFields({ ...fields, name: e.target.value })} placeholder="Follow-up" /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Subject<input required maxLength={512} className={inputClass} value={fields.subject} onChange={(e) => setFields({ ...fields, subject: e.target.value })} placeholder="Thanks for meeting, {{name}}" /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">Plain-text body<textarea maxLength={100000} rows={8} className={inputClass} value={fields.body_text} onChange={(e) => setFields({ ...fields, body_text: e.target.value })} placeholder={"Hi {{name}},\n\nIt was great speaking with you."} /></label>
          <label className="space-y-1 text-xs text-[#8b949e]">HTML body<textarea maxLength={100000} rows={8} className={inputClass} value={fields.body_html} onChange={(e) => setFields({ ...fields, body_html: e.target.value })} placeholder={'<p>Hi {{name}},</p>'} /></label>
        </div>
        <div className="flex flex-wrap gap-2">
          <button disabled={saving} className="inline-flex items-center gap-2 rounded-md bg-[#238636] px-3 py-2 text-xs font-medium text-white hover:bg-[#2ea043] disabled:opacity-50"><Plus className="h-3.5 w-3.5"/>{saving ? "Saving…" : editingId ? "Save template" : "Create template"}</button>
          <button type="button" disabled={previewing} onClick={() => void runPreview()} className="rounded-md border border-[#30363d] px-3 py-2 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50">{previewing ? "Previewing…" : "Preview current fields"}</button>
        </div>
        <div className="grid gap-3 border-t border-[#30363d] pt-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <label className="space-y-1 text-xs text-[#8b949e]">Preview data (JSON object)<textarea rows={5} className={`${inputClass} font-mono`} value={previewData} onChange={(e) => { setPreviewData(e.target.value); setPreview(null); }} /></label>
          <div className="space-y-1 text-xs text-[#8b949e]"><p>Use placeholders such as <code className="text-[#c9d1d9]">{`{{name}}`}</code> or <code className="text-[#c9d1d9]">{`{{company.city}}`}</code>. HTML placeholder values are escaped. Preview is shown as text and is never executed.</p>{preview && <p className="text-[#3fb950]">Variables used: {preview.variables.length ? preview.variables.join(", ") : "none"}</p>}</div>
        </div>
        {preview && <section aria-label="Template preview" className="grid gap-3 rounded-md border border-[#30363d] bg-[#0d1117] p-4 md:grid-cols-2">
          <div className="space-y-2 text-xs"><h3 className="font-semibold text-[#e6edf3]">Rendered subject</h3><pre className="whitespace-pre-wrap break-words text-[#c9d1d9]">{preview.subject}</pre><h3 className="pt-2 font-semibold text-[#e6edf3]">Plain-text body</h3><pre className="whitespace-pre-wrap break-words text-[#c9d1d9]">{preview.body_text || "(empty)"}</pre></div>
          <div className="space-y-2 text-xs"><h3 className="font-semibold text-[#e6edf3]">HTML source preview</h3><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words text-[#8b949e]">{preview.body_html || "(empty)"}</pre></div>
        </section>}
      </form>}

      <section className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
        <div className="flex items-center justify-between border-b border-[#30363d] px-5 py-4"><h2 className="text-sm font-semibold text-[#e6edf3]">Saved templates</h2><span className="text-[11px] text-[#8b949e]">{templates.length}</span></div>
        {loading ? <p className="p-8 text-center text-xs text-[#8b949e]">Loading templates…</p>
          : !agentEmail ? <p className="p-6 text-xs text-[#8b949e]">Create an inbox to manage templates.</p>
          : templates.length === 0 ? <div className="p-10 text-center"><FileText className="mx-auto mb-2 h-6 w-6 text-[#8b949e]"/><p className="text-xs text-[#8b949e]">No templates saved for this inbox yet.</p></div>
          : <div className="divide-y divide-[#30363d]">{templates.map((template) => <article key={template.id} className="flex items-start justify-between gap-4 px-5 py-4">
            <div className="min-w-0"><p className="truncate text-sm font-medium text-[#e6edf3]">{template.name}</p><p className="mt-1 truncate text-xs text-[#8b949e]">Subject: {template.subject}</p><p className="mt-1 text-[11px] text-[#6e7681]">Updated {new Date(template.updated_at).toLocaleString()}</p></div>
            <div className="flex shrink-0 gap-1"><button aria-label={`Edit ${template.name}`} onClick={() => editTemplate(template)} className="rounded p-2 text-[#8b949e] hover:bg-[#21262d] hover:text-white"><Pencil className="h-3.5 w-3.5"/></button><button aria-label={`Delete ${template.name}`} onClick={() => void removeTemplate(template)} className="rounded p-2 text-[#8b949e] hover:bg-[#3d1f1f] hover:text-[#ff7b72]"><Trash2 className="h-3.5 w-3.5"/></button></div>
          </article>)}</div>}
      </section>
      <p className="text-[11px] text-[#8b949e]">Templates are saved per inbox. Saving or previewing a template does not send email.</p>
    </div>
  );
}
