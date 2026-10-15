"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ApiKeyItem, ApiKeyCreated, Agent } from "@/lib/types";
import { Dropdown } from "@/components/Dropdown";
import { Key, Plus, Trash2, Copy, Check, ShieldCheck, AlertCircle } from "lucide-react";

const SCOPE_OPTIONS = [
  { value: "full_access", label: "Read and send" },
  { value: "read_only", label: "Read only" },
  { value: "reviewer", label: "Review only" },
];

export default function ApiKeysPage() {
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [keyName, setKeyName] = useState<string>("");
  const [scope, setScope] = useState<string>("full_access");
  const [agentEmail, setAgentEmail] = useState<string>("");
  const [expiresInDays, setExpiresInDays] = useState<string>("90");
  const [generating, setGenerating] = useState<boolean>(false);
  const [createdKey, setCreatedKey] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchKeys = async () => {
    try {
      setLoading(true);
      const data = await api.getApiKeys();
      setKeys(data);
      const configuredAgents = await api.getAgents();
      setAgents(configuredAgents);
      setAgentEmail((current) => current || configuredAgents[0]?.email || "");
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load API keys");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKeys();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!keyName.trim()) return;
    try {
      setGenerating(true);
      setError(null);
      const res = await api.createApiKey(keyName.trim(), agentEmail, scope, expiresInDays === "never" ? null : Number(expiresInDays));
      setCreatedKey(res);
      setKeyName("");
      await fetchKeys();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate API key");
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Revoke key "${name}"? Apps using this key will immediately lose access.`)) return;
    try {
      await api.deleteApiKey(id);
      await fetchKeys();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to revoke key");
    }
  };

  const copyKey = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="border-b border-[#30363d] pb-6">
        <h1 className="text-xl font-bold text-[#e6edf3] tracking-tight">API Keys & Authentication</h1>
        <p className="text-xs text-[#8b949e] mt-1">
          Manage Bearer tokens used by autonomous agent frameworks (LangChain, CrewAI, PydanticAI, OpenAI Agents SDK) and external services.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-[#f85149]/10 border border-[#f85149]/30 rounded-md text-xs text-[#f85149]">
          {error}
        </div>
      )}

      {/* Newly Created Key Alert */}
      {createdKey && (
        <div className="p-4 bg-[#238636]/10 border border-[#238636]/30 rounded-lg space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-[#3fb950] flex items-center gap-1.5">
              <ShieldCheck className="w-4 h-4" /> API Key Created — Save It Now
            </span>
            <button
              onClick={() => setCreatedKey(null)}
              className="text-xs text-[#8b949e] hover:text-[#e6edf3]"
            >
              Dismiss
            </button>
          </div>
          <p className="text-[11px] text-[#8b949e]">
            This token will never be displayed again. Store it securely in your environment variables.
            {createdKey.expires_at && ` It expires ${new Date(createdKey.expires_at).toLocaleDateString()}.`}
          </p>
          <div className="flex items-center gap-2 bg-[#0d1117] border border-[#30363d] rounded p-2">
            <span className="font-mono text-xs text-[#e6edf3] flex-1 break-all select-all">
              {createdKey.key}
            </span>
            <button
              onClick={() => copyKey(createdKey.key)}
              className="px-2.5 py-1 bg-[#21262d] hover:bg-[#30363d] text-[#e6edf3] text-xs rounded border border-[#30363d] transition flex items-center gap-1 shrink-0"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-[#3fb950]" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
        </div>
      )}

      {/* Create Key Form */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
        <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider mb-3">
          Create New Secret Key
        </h2>
        <form onSubmit={handleCreate} className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div>
            <input
              type="text"
              placeholder="Key Name (e.g. Production Agent)"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
            />
          </div>
          <div>
            <select value={agentEmail} onChange={(e) => setAgentEmail(e.target.value)} disabled={agents.length === 0} className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] disabled:opacity-50">
              {agents.length === 0 ? <option value="">Create an inbox first</option> : agents.map((agent) => <option key={agent.email} value={agent.email}>{agent.email}</option>)}
            </select>
          </div>
          <div>
            <Dropdown
              value={scope}
              onChange={setScope}
              options={SCOPE_OPTIONS}
            />
          </div>
          <div>
            <select value={expiresInDays} onChange={(e) => setExpiresInDays(e.target.value)} className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3]">
              <option value="30">Expires in 30 days</option>
              <option value="90">Expires in 90 days</option>
              <option value="365">Expires in 1 year</option>
              <option value="never">No expiration</option>
            </select>
          </div>
          <div>
            <button
              type="submit"
              disabled={generating || !keyName.trim() || !agentEmail}
              className="w-full flex items-center justify-center gap-1.5 px-4 py-2 bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold rounded-md transition disabled:opacity-50"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>{generating ? "Generating..." : "Generate Key"}</span>
            </button>
          </div>
        </form>
      </div>

      {/* Keys List Table */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
        <div className="p-4 border-b border-[#30363d] text-xs font-semibold text-[#e6edf3]">
          API Keys
        </div>
        {loading ? (
          <div className="p-8 text-center text-xs text-[#8b949e]">Loading keys...</div>
        ) : keys.length === 0 ? (
          <div className="p-12 text-center text-[#8b949e] space-y-2">
            <Key className="w-8 h-8 mx-auto opacity-50" />
            <div className="text-xs font-semibold text-[#e6edf3]">No API keys created</div>
            <div className="text-xs">Generate a key above to authenticate programmatic API calls.</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-[#8b949e] border-b border-[#30363d] bg-[#0d1117]/50">
                  <th className="py-2.5 px-4 font-medium">Name</th>
                  <th className="py-2.5 px-4 font-medium">Token Prefix</th>
                  <th className="py-2.5 px-4 font-medium">Scope</th>
                  <th className="py-2.5 px-4 font-medium">Created</th>
                  <th className="py-2.5 px-4 font-medium">Expires</th>
                  <th className="py-2.5 px-4 font-medium text-right">Revoke</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#30363d]/50">
                {keys.map((k) => (
                  <tr key={k.id} className={`hover:bg-[#21262d]/30 transition ${k.expires_at && new Date(k.expires_at) <= new Date() ? "opacity-50" : ""}`}>
                    <td className="py-3 px-4 font-medium text-[#e6edf3]">{k.name}</td>
                    <td className="py-3 px-4 font-mono text-[11px] text-[#8b949e]">{k.prefix}_••••••</td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-[#21262d] text-[#8b949e] border border-[#30363d]">
                        {k.scope}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-[#8b949e] text-[11px]">
                      {new Date(k.created_at).toLocaleDateString()}
                    </td>
                    <td className="py-3 px-4 text-[#8b949e] text-[11px]">
                      {k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "Never"}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => handleDelete(k.id, k.name)}
                        className="p-1 text-[#8b949e] hover:text-[#f85149] rounded transition"
                        title="Revoke key"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
