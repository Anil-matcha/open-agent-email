"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bot, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { Agent } from "@/lib/types";
import { agentsApi, InboxMetrics } from "@/lib/agents-api";
import { Dropdown } from "@/components/Dropdown";
import { DeletedInboxes } from "@/components/agents/DeletedInboxes";

const sensitivityOptions = [
  { value: "off", label: "Off (Permissive)", sublabel: "No scanning" },
  { value: "low", label: "Low Sensitivity", sublabel: "Only obvious attacks" },
  { value: "medium", label: "Medium (Standard)", sublabel: "Default threshold" },
  { value: "high", label: "High Sensitivity", sublabel: "Strict review queue" },
];

export function InboxDirectory() {
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [metrics, setMetrics] = useState<Record<string, InboxMetrics>>({});
  const [error, setError] = useState<string>("");
  const [deletedRefresh, setDeletedRefresh] = useState<number>(0);

  const [isCreateOpen, setIsCreateOpen] = useState<boolean>(false);
  const [newEmailSlug, setNewEmailSlug] = useState<string>("");
  const [newDisplayName, setNewDisplayName] = useState<string>("");
  const [newDescription, setNewDescription] = useState<string>("");
  const [newSensitivity, setNewSensitivity] = useState<string>("medium");
  const [newHitl, setNewHitl] = useState<boolean>(true);
  const [creating, setCreating] = useState<boolean>(false);
  const [createError, setCreateError] = useState<string>("");

  const load = async () => {
    try {
      const list = await api.getAgents();
      setAgents(list);
      setError("");
      const entries = await Promise.all(
        list.map((ag) => agentsApi.getMetrics(ag.email).then((m) => [ag.email, m] as const).catch(() => null)),
      );
      setMetrics(Object.fromEntries(entries.filter((e): e is readonly [string, InboxMetrics] => e !== null)));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not load inboxes.");
      setAgents([]);
    }
  };

  useEffect(() => { void load(); }, []);

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newEmailSlug.trim() || !newDisplayName.trim()) return;
    try {
      setCreating(true);
      setCreateError("");
      const fullEmail = newEmailSlug.includes("@") ? newEmailSlug : `${newEmailSlug}@agents.local`;
      const created = await api.createAgent({
        email: fullEmail,
        display_name: newDisplayName,
        description: newDescription,
        threat_sensitivity: newSensitivity as Agent["threat_sensitivity"],
        hitl_enabled: newHitl,
      });
      setIsCreateOpen(false);
      setNewEmailSlug("");
      setNewDisplayName("");
      setNewDescription("");
      router.push(`/inboxes/messages?email=${encodeURIComponent(created.email)}`);
    } catch (err: unknown) {
      setCreateError(err instanceof Error ? err.message : "Could not create the inbox.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="card-simple p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">Inboxes</h2>
          <p className="text-xs text-[#8b949e]">Open an inbox to read mail, manage follow-ups and suppressions, review trash, and change its settings.</p>
        </div>
        <button
          onClick={() => setIsCreateOpen(true)}
          className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New Agent Inbox</span>
        </button>
      </div>

      {error && <div role="alert" className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72]">{error}</div>}

      {agents === null ? (
        <div className="card-simple p-6 text-xs text-[#8b949e]">Loading inboxes…</div>
      ) : agents.length === 0 ? (
        <div className="card-simple p-12 text-center text-xs text-[#8b949e] space-y-3">
          <Bot className="w-8 h-8 text-[#8b949e] mx-auto" />
          <div className="text-sm font-semibold text-[#e6edf3]">No inboxes yet</div>
          <p className="max-w-md mx-auto text-[11px]">Create an inbox to start receiving, reading and sending real two-way email.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {agents.map((ag) => {
            const m = metrics[ag.email];
            return (
              <Link
                key={ag.id}
                href={`/inboxes/messages?email=${encodeURIComponent(ag.email)}`}
                className="card-simple p-4 block hover:bg-[#1c2128] transition-colors"
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-semibold text-[#e6edf3]">{ag.display_name}</span>
                  <span className="text-[10px] font-mono text-[#8b949e] uppercase">{ag.threat_sensitivity}</span>
                </div>
                <div className="font-mono text-[11px] text-blue-400 mb-1 break-all">{ag.email}</div>
                <p className="text-[11px] text-[#8b949e] line-clamp-1">{ag.description}</p>
                <div className="flex items-center gap-3 text-[10px] text-[#8b949e] mt-3 pt-2 border-t border-[#30363d]">
                  <span>{m ? `${m.unread} unread` : "…"}</span>
                  <span>{m ? `${m.pending_reviews} held for review` : ""}</span>
                  <span className="ml-auto">Approval hold: {ag.hitl_enabled ? "On" : "Off"}</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      <DeletedInboxes key={deletedRefresh} onRestored={() => { setDeletedRefresh((n) => n + 1); void load(); }} />

      {isCreateOpen && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg w-full max-w-md p-5 space-y-3 shadow-xl">
            <div className="flex items-center justify-between pb-2 border-b border-[#30363d]">
              <h4 className="text-xs font-semibold text-[#e6edf3]">Create Agent Inbox</h4>
              <button onClick={() => setIsCreateOpen(false)} className="text-[#8b949e] hover:text-[#e6edf3] text-xs" aria-label="Close">✕</button>
            </div>

            <form onSubmit={handleCreateAgent} className="space-y-3 text-xs">
              <div>
                <label className="text-[#8b949e] block mb-1">Email Slug</label>
                <div className="flex items-center">
                  <input
                    type="text"
                    required
                    placeholder="e.g. support or billing"
                    value={newEmailSlug}
                    onChange={(e) => setNewEmailSlug(e.target.value)}
                    className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] font-mono focus:outline-none focus:border-zinc-500"
                  />
                  {!newEmailSlug.includes("@") && <span className="text-xs font-mono text-[#8b949e] pl-2">@agents.local</span>}
                </div>
              </div>
              <div>
                <label className="text-[#8b949e] block mb-1">Display Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Customer Support Agent"
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500"
                />
              </div>
              <div>
                <label className="text-[#8b949e] block mb-1">Description</label>
                <textarea
                  rows={2}
                  placeholder="Purpose of this inbox..."
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500 resize-none font-sans"
                />
              </div>
              <div>
                <Dropdown label="Threat Scan Sensitivity" options={sensitivityOptions} value={newSensitivity} onChange={setNewSensitivity} />
              </div>
              <div className="flex items-center gap-2 pt-1">
                <input type="checkbox" id="hitl-create" checked={newHitl} onChange={(e) => setNewHitl(e.target.checked)} className="w-4 h-4 accent-[#238636]" />
                <label htmlFor="hitl-create" className="text-xs text-[#e6edf3]">Enable Human-in-the-Loop review holds</label>
              </div>
              {createError && <div role="alert" className="text-[11px] text-[#ff7b72]">{createError}</div>}
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={() => setIsCreateOpen(false)} className="px-3 py-1.5 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]">Cancel</button>
                <button type="submit" disabled={creating} className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white font-medium">
                  {creating ? "Creating..." : "Create Inbox"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
