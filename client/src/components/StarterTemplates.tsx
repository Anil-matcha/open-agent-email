"use client";

import { useEffect, useState } from "react";
import { Copy, Sparkles } from "lucide-react";
import { StarterTemplateDetail, StarterTemplateSummary, starterApi } from "@/lib/ops-api";

// Browse the built-in starters and copy one into an inbox. The copy is independent and editable.
export function StarterTemplates({ agentEmail, onCopied }: { agentEmail: string; onCopied: () => void }) {
  const [starters, setStarters] = useState<StarterTemplateSummary[]>([]);
  const [detail, setDetail] = useState<StarterTemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    starterApi.list()
      .then(setStarters)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load starter templates."))
      .finally(() => setLoading(false));
  }, []);

  const show = async (alias: string) => {
    setError("");
    setNotice("");
    setBusy(alias);
    try {
      setDetail(await starterApi.get(alias));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the starter.");
    } finally {
      setBusy("");
    }
  };

  const copy = async (alias: string) => {
    if (!agentEmail) return;
    setError("");
    setNotice("");
    setBusy(alias);
    try {
      const created = await starterApi.copyToInbox(agentEmail, alias);
      setNotice(`Added “${created.name}” to ${agentEmail}. You can edit it below.`);
      onCopied();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not copy the starter.");
    } finally {
      setBusy("");
    }
  };

  return (
    <section aria-label="Starter templates" className="space-y-4 rounded-lg border border-[#30363d] bg-[#161b22] p-5">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-[#e6edf3]"><Sparkles className="h-4 w-4 text-[#d29922]" />Start from an example</h2>
        <span className="text-[11px] text-[#8b949e]">{starters.length} available</span>
      </div>
      {error && <div role="alert" className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-3 py-2 text-xs text-[#ff7b72]">{error}</div>}
      {notice && <div role="status" className="rounded-md border border-[#3fb950]/40 bg-[#3fb950]/10 px-3 py-2 text-xs text-[#3fb950]">{notice}</div>}
      {loading ? <p className="text-xs text-[#8b949e]">Loading examples…</p> : <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {starters.map((starter) => <article key={starter.alias} className="flex flex-col justify-between rounded-md border border-[#30363d] bg-[#0d1117] p-3">
          <div className="min-w-0">
            <p className="text-xs font-medium text-[#e6edf3]">{starter.name}</p>
            <p className="mt-1 text-[11px] text-[#8b949e]">{starter.description}</p>
            <p className="mt-2 truncate font-mono text-[10px] text-[#6e7681]">{starter.subject}</p>
          </div>
          <div className="mt-3 flex gap-2">
            <button disabled={busy === starter.alias} onClick={() => void show(starter.alias)} className="rounded border border-[#30363d] px-2.5 py-1 text-[11px] text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50">Preview</button>
            <button disabled={busy === starter.alias || !agentEmail} onClick={() => void copy(starter.alias)} title={agentEmail ? "" : "Create an inbox first"} className="inline-flex items-center gap-1 rounded bg-[#238636] px-2.5 py-1 text-[11px] text-white hover:bg-[#2ea043] disabled:opacity-50"><Copy className="h-3 w-3" />Use in this inbox</button>
          </div>
        </article>)}
      </div>}
      {detail && <div className="grid gap-3 rounded-md border border-[#30363d] bg-[#0d1117] p-4 text-xs md:grid-cols-2" aria-label={`${detail.name} preview`}>
        <div className="space-y-2">
          <h3 className="font-semibold text-[#e6edf3]">{detail.name}, with sample data</h3>
          <p className="text-[#8b949e]">Subject</p><pre className="whitespace-pre-wrap break-words text-[#c9d1d9]">{detail.preview.subject}</pre>
          <p className="text-[#8b949e]">Plain-text body</p><pre className="whitespace-pre-wrap break-words text-[#c9d1d9]">{detail.preview.body_text || "(empty)"}</pre>
        </div>
        <div className="space-y-2">
          <p className="text-[#8b949e]">Variables you fill in when sending</p>
          <p className="font-mono text-[#c9d1d9]">{detail.variables.length ? detail.variables.join(", ") : "none"}</p>
          <p className="text-[#8b949e]">HTML source</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words text-[#8b949e]">{detail.preview.body_html || "(empty)"}</pre>
        </div>
      </div>}
    </section>
  );
}
