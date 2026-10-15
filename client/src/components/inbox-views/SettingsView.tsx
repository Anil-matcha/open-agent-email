"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Save, Sparkles, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Agent } from "@/lib/types";
import { ProtectionPanel } from "@/components/agents/ProtectionPanel";
import { MetricsPanel } from "@/components/agents/MetricsPanel";
import { TestSendPanel } from "@/components/agents/TestSendPanel";

export function SettingsView({ email }: { email: string }) {
  const router = useRouter();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [error, setError] = useState<string>("");
  const [editAutoReply, setEditAutoReply] = useState<boolean>(false);
  const [editPrompt, setEditPrompt] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [notice, setNotice] = useState<string>("");

  const apply = (ag: Agent) => {
    setAgent(ag);
    setEditAutoReply(ag.auto_reply_enabled);
    setEditPrompt(ag.auto_reply_prompt || "");
  };

  useEffect(() => {
    let active = true;
    api.getAgent(email)
      .then((ag) => { if (active) apply(ag); })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "Could not load this inbox."); });
    return () => { active = false; };
  }, [email]);

  const handleSaveConfig = async () => {
    if (!agent) return;
    try {
      setSaving(true);
      setNotice("");
      const updated = await api.updateAgent(agent.email, {
        auto_reply_enabled: editAutoReply,
        auto_reply_prompt: editPrompt,
      });
      apply(updated);
      setNotice("Auto-reply settings saved.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!agent) return;
    if (!confirm(`Delete inbox ${agent.email}? It stops receiving mail but keeps its messages, and you can restore it from "Deleted inboxes" on the Inboxes page.`)) return;
    try {
      await api.deleteAgent(agent.email);
      router.push("/inboxes");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not delete this inbox.");
    }
  };

  if (error && !agent) {
    return <div role="alert" className="card-simple p-4 text-xs text-[#ff7b72]">{error}</div>;
  }
  if (!agent) {
    return <div className="card-simple p-6 text-xs text-[#8b949e]">Loading inbox settings…</div>;
  }

  return (
    <div className="card-simple p-5 space-y-5">
      <div className="flex items-start justify-between pb-3 border-b border-[#30363d]">
        <div>
          <h3 className="text-sm font-semibold text-[#e6edf3]">{agent.display_name}</h3>
          <div className="text-xs font-mono text-blue-400 mt-0.5">{agent.email}</div>
          <p className="text-xs text-[#8b949e] mt-1">{agent.description}</p>
        </div>
        <button
          onClick={handleDelete}
          className="p-1.5 rounded border border-[#da3633]/30 text-[#f85149] hover:bg-[#da3633]/10 transition-colors text-xs"
          title="Delete Inbox"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {error && <div role="alert" className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72]">{error}</div>}

      <ProtectionPanel key={agent.email} email={agent.email} onSaved={() => { void api.getAgent(email).then(apply).catch(() => undefined); }} />

      <div className="p-3 rounded bg-[#0d1117] border border-[#30363d] space-y-2">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs font-medium text-[#e6edf3] flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-blue-400" />
              Autonomous Auto-Reply
            </div>
            <p className="text-[11px] text-[#8b949e]">
              Agent automatically composes and dispatches in-thread replies to clean emails.
            </p>
          </div>
          <input
            type="checkbox"
            checked={editAutoReply}
            onChange={(e) => setEditAutoReply(e.target.checked)}
            aria-label="Enable auto-reply"
            className="w-4 h-4 accent-[#238636] cursor-pointer"
          />
        </div>
        {editAutoReply && (
          <div className="pt-2 border-t border-[#30363d] space-y-1">
            <label className="text-[11px] text-[#8b949e]">Auto-Reply Directive</label>
            <textarea
              rows={3}
              value={editPrompt}
              onChange={(e) => setEditPrompt(e.target.value)}
              className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500 font-mono"
            />
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-3 pt-2">
        {notice && <span role="status" className="text-[11px] text-[#3fb950]">{notice}</span>}
        <button
          onClick={handleSaveConfig}
          disabled={saving}
          className="px-4 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
        >
          <Save className="w-3.5 h-3.5" />
          <span>{saving ? "Saving..." : "Save auto-reply"}</span>
        </button>
      </div>

      <div className="pt-3 border-t border-[#30363d] space-y-4">
        <MetricsPanel key={`m-${agent.email}`} email={agent.email} />
        <TestSendPanel email={agent.email} />
      </div>
    </div>
  );
}
