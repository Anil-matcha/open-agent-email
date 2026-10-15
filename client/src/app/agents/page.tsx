"use client";

import { useEffect, useState } from "react";
import {
  Bot,
  Plus,
  Trash2,
  Save,
  Sparkles
} from "lucide-react";
import { api } from "@/lib/api";
import { Agent } from "@/lib/types";
import { Dropdown } from "@/components/Dropdown";
import { ProtectionPanel } from "@/components/agents/ProtectionPanel";
import { MetricsPanel } from "@/components/agents/MetricsPanel";
import { TestSendPanel } from "@/components/agents/TestSendPanel";
import { DeletedInboxes } from "@/components/agents/DeletedInboxes";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  // Create Agent Modal
  const [isCreateOpen, setIsCreateOpen] = useState<boolean>(false);
  const [newEmailSlug, setNewEmailSlug] = useState<string>("");
  const [newDisplayName, setNewDisplayName] = useState<string>("");
  const [newDescription, setNewDescription] = useState<string>("");
  const [newSensitivity, setNewSensitivity] = useState<string>("medium");
  const [newHitl, setNewHitl] = useState<boolean>(true);
  const [creating, setCreating] = useState<boolean>(false);

  // Edit State (protection settings live in ProtectionPanel)
  const [editAutoReply, setEditAutoReply] = useState<boolean>(false);
  const [editPrompt, setEditPrompt] = useState<string>("");
  const [saving, setSaving] = useState<boolean>(false);
  const [deletedRefresh, setDeletedRefresh] = useState<number>(0);

  const fetchAgents = async () => {
    try {
      setLoading(true);
      const ags = await api.getAgents();
      setAgents(ags);
      if (ags.length > 0) {
        selectAgent(ags[0]);
      } else {
        setSelectedAgent(null);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // Reload the list without changing which inbox is open.
  const refreshList = async () => {
    try {
      const ags = await api.getAgents();
      setAgents(ags);
      setSelectedAgent((current) => ags.find((a) => a.id === current?.id) ?? current);
    } catch (err) {
      console.error(err);
    }
  };

  const selectAgent = (ag: Agent) => {
    setSelectedAgent(ag);
    setEditAutoReply(ag.auto_reply_enabled);
    setEditPrompt(ag.auto_reply_prompt || "");
  };

  useEffect(() => {
    fetchAgents();
  }, []);

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newEmailSlug.trim() || !newDisplayName.trim()) return;
    try {
      setCreating(true);
      const fullEmail = newEmailSlug.includes("@") ? newEmailSlug : `${newEmailSlug}@agents.local`;
      const created = await api.createAgent({
        email: fullEmail,
        display_name: newDisplayName,
        description: newDescription,
        threat_sensitivity: newSensitivity as any,
        hitl_enabled: newHitl,
      });
      setIsCreateOpen(false);
      setNewEmailSlug("");
      setNewDisplayName("");
      setNewDescription("");
      await fetchAgents();
      selectAgent(created);
    } catch (err: any) {
      alert(`Failed to create agent: ${err.message}`);
    } finally {
      setCreating(false);
    }
  };

  const handleSaveConfig = async () => {
    if (!selectedAgent) return;
    try {
      setSaving(true);
      const updated = await api.updateAgent(selectedAgent.email, {
        auto_reply_enabled: editAutoReply,
        auto_reply_prompt: editPrompt,
      });
      await fetchAgents();
      selectAgent(updated);
      alert("Configuration updated successfully!");
    } catch (err: any) {
      alert(`Save error: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (email: string) => {
    if (!confirm(`Delete inbox ${email}? It stops receiving mail but keeps its messages, and you can restore it from "Deleted inboxes" below.`)) return;
    try {
      await api.deleteAgent(email);
      await fetchAgents();
      setDeletedRefresh((n) => n + 1);
    } catch (err: any) {
      alert(`Delete error: ${err.message}`);
    }
  };

  const sensitivityOptions = [
    { value: "off", label: "Off (Permissive)", sublabel: "No scanning" },
    { value: "low", label: "Low Sensitivity", sublabel: "Only obvious attacks" },
    { value: "medium", label: "Medium (Standard)", sublabel: "Default threshold" },
    { value: "high", label: "High Sensitivity", sublabel: "Strict review queue" },
  ];

  return (
    <div className="space-y-4">
      {/* Top Banner */}
      <div className="card-simple p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">
            Agent Inboxes Directory
          </h2>
          <p className="text-xs text-[#8b949e]">
            Manage agent email identities, threat scanning sensitivity, and automation policies.
          </p>
        </div>

        <button
          onClick={() => setIsCreateOpen(true)}
          className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>New Agent Inbox</span>
        </button>
      </div>

      {/* Main Grid: List + Edit */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-[580px]">
        {/* Left: Agent List */}
        <div className="lg:col-span-5 card-simple flex flex-col overflow-hidden">
          <div className="p-3 border-b border-[#30363d] text-xs font-medium text-[#e6edf3]">
            Configured Inboxes ({agents.length})
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-[#30363d]">
            {agents.length === 0 ? (
              <div className="p-10 text-center text-xs text-[#8b949e]">
                No inboxes found. Click &quot;New Agent Inbox&quot; to add one.
              </div>
            ) : (
              agents.map((ag) => {
                const isSelected = selectedAgent?.id === ag.id;
                return (
                  <div
                    key={ag.id}
                    onClick={() => selectAgent(ag)}
                    className={`p-3 cursor-pointer text-xs transition-colors ${
                      isSelected
                        ? "bg-[#21262d] border-l-2 border-blue-400"
                        : "hover:bg-[#1c2128]"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-semibold text-[#e6edf3]">{ag.display_name}</span>
                      <span className="text-[10px] font-mono text-[#8b949e] uppercase">
                        {ag.threat_sensitivity}
                      </span>
                    </div>

                    <div className="font-mono text-[11px] text-blue-400 mb-1">{ag.email}</div>
                    <p className="text-[11px] text-[#8b949e] line-clamp-1">{ag.description}</p>

                    <div className="flex items-center justify-between text-[10px] text-[#8b949e] mt-2 pt-1 border-t border-[#30363d]">
                      <span>HITL: {ag.hitl_enabled ? "On" : "Off"}</span>
                      <span>Auto-reply: {ag.auto_reply_enabled ? "On" : "Off"}</span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Settings Editor */}
        <div className="lg:col-span-7 card-simple p-5 flex flex-col justify-between">
          {selectedAgent ? (
            <div className="space-y-5">
              <div className="flex items-start justify-between pb-3 border-b border-[#30363d]">
                <div>
                  <h3 className="text-sm font-semibold text-[#e6edf3]">
                    {selectedAgent.display_name}
                  </h3>
                  <div className="text-xs font-mono text-blue-400 mt-0.5">
                    {selectedAgent.email}
                  </div>
                  <p className="text-xs text-[#8b949e] mt-1">{selectedAgent.description}</p>
                </div>

                <button
                  onClick={() => handleDelete(selectedAgent.email)}
                  className="p-1.5 rounded border border-[#da3633]/30 text-[#f85149] hover:bg-[#da3633]/10 transition-colors text-xs"
                  title="Delete Inbox"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>

              <ProtectionPanel
                key={selectedAgent.email}
                email={selectedAgent.email}
                onSaved={refreshList}
              />

              {/* Auto-reply */}
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

              {/* Save Button */}
              <div className="flex justify-end pt-2">
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
                <MetricsPanel key={`m-${selectedAgent.email}`} email={selectedAgent.email} />
                <TestSendPanel email={selectedAgent.email} />
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-xs text-[#8b949e]">
              <Bot className="w-8 h-8 text-[#8b949e] mb-2" />
              <div>Select an agent to configure.</div>
            </div>
          )}
        </div>
      </div>

      <DeletedInboxes key={deletedRefresh} onRestored={fetchAgents} />

      {/* Provision Modal */}
      {isCreateOpen && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg w-full max-w-md p-5 space-y-3 shadow-xl">
            <div className="flex items-center justify-between pb-2 border-b border-[#30363d]">
              <h4 className="text-xs font-semibold text-[#e6edf3]">Create Agent Inbox</h4>
              <button
                onClick={() => setIsCreateOpen(false)}
                className="text-[#8b949e] hover:text-[#e6edf3] text-xs"
              >
                ✕
              </button>
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
                  {!newEmailSlug.includes("@") && (
                    <span className="text-xs font-mono text-[#8b949e] pl-2">@agents.local</span>
                  )}
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
                <Dropdown
                  label="Threat Scan Sensitivity"
                  options={sensitivityOptions}
                  value={newSensitivity}
                  onChange={setNewSensitivity}
                />
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="hitl-create"
                  checked={newHitl}
                  onChange={(e) => setNewHitl(e.target.checked)}
                  className="w-4 h-4 accent-[#238636]"
                />
                <label htmlFor="hitl-create" className="text-xs text-[#e6edf3]">
                  Enable Human-in-the-Loop review holds
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setIsCreateOpen(false)}
                  className="px-3 py-1.5 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white font-medium"
                >
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
