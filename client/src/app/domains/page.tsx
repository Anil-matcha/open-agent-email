"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Domain } from "@/lib/types";
import { Globe, Plus, CheckCircle2, Clock, Trash2, Copy, Check, RefreshCw } from "lucide-react";

export default function DomainsPage() {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [newDomain, setNewDomain] = useState<string>("");
  const [adding, setAdding] = useState<boolean>(false);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchDomains = async () => {
    try {
      setLoading(true);
      const data = await api.getDomains();
      setDomains(data);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load domains");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDomains();
  }, []);

  const handleAddDomain = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newDomain.trim()) return;
    try {
      setAdding(true);
      setError(null);
      await api.createDomain(newDomain.trim());
      setNewDomain("");
      await fetchDomains();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to add domain");
    } finally {
      setAdding(false);
    }
  };

  const handleVerify = async (domainName: string) => {
    try {
      setVerifying(domainName);
      await api.verifyDomain(domainName);
      await fetchDomains();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setVerifying(null);
    }
  };

  const handleDelete = async (domainName: string) => {
    if (!confirm(`Delete domain ${domainName}?`)) return;
    try {
      await api.deleteDomain(domainName);
      await fetchDomains();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const copyToClipboard = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-[#30363d] pb-6">
        <div>
          <h1 className="text-xl font-bold text-[#e6edf3] tracking-tight">Domains & DNS</h1>
          <p className="text-xs text-[#8b949e] mt-1">
            Configure custom domains to receive emails directly via inbound MX records and authenticate outbound mail.
          </p>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-[#f85149]/10 border border-[#f85149]/30 rounded-md text-xs text-[#f85149]">
          {error}
        </div>
      )}

      {/* Add Domain Form */}
      <div className="p-4 bg-[#161b22] border border-[#30363d] rounded-lg">
        <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider mb-3">
          Add Custom Agent Domain
        </h2>
        <form onSubmit={handleAddDomain} className="flex gap-3">
          <input
            type="text"
            placeholder="e.g. agents.yourcompany.com"
            value={newDomain}
            onChange={(e) => setNewDomain(e.target.value)}
            className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
          />
          <button
            type="submit"
            disabled={adding || !newDomain.trim()}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold rounded-md transition disabled:opacity-50"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>{adding ? "Adding..." : "Add Domain"}</span>
          </button>
        </form>
      </div>

      {/* Domains List */}
      <div className="space-y-4">
        {loading ? (
          <div className="p-12 text-center text-xs text-[#8b949e] bg-[#161b22] border border-[#30363d] rounded-lg">
            Loading domains...
          </div>
        ) : domains.length === 0 ? (
          <div className="p-12 text-center bg-[#161b22] border border-[#30363d] rounded-lg space-y-3">
            <Globe className="w-8 h-8 text-[#8b949e] mx-auto opacity-50" />
            <h3 className="text-xs font-semibold text-[#e6edf3]">No custom domains configured</h3>
            <p className="text-xs text-[#8b949e] max-w-sm mx-auto">
              Add your domain above to generate the MX, SPF, DKIM, and DMARC DNS records required for real email routing.
            </p>
          </div>
        ) : (
          domains.map((dom) => (
            <div key={dom.id} className="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
              <div className="p-4 border-b border-[#30363d] flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold text-[#e6edf3] font-mono">{dom.domain}</span>
                  {dom.status === "verified" ? (
                    <span className="flex items-center gap-1 px-2 py-0.5 text-[10px] bg-[#238636]/20 text-[#3fb950] border border-[#238636]/40 rounded font-medium">
                      <CheckCircle2 className="w-3 h-3" /> Verified
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 px-2 py-0.5 text-[10px] bg-[#9e6a03]/20 text-[#d29922] border border-[#9e6a03]/40 rounded font-medium">
                      <Clock className="w-3 h-3" /> Pending DNS Setup
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleVerify(dom.domain)}
                    disabled={verifying === dom.domain}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-[#21262d] hover:bg-[#30363d] text-[#e6edf3] text-xs rounded border border-[#30363d] transition disabled:opacity-50"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${verifying === dom.domain ? "animate-spin" : ""}`} />
                    <span>{verifying === dom.domain ? "Verifying..." : "Verify DNS"}</span>
                  </button>
                  <button
                    onClick={() => handleDelete(dom.domain)}
                    className="p-1.5 hover:bg-[#f85149]/20 text-[#8b949e] hover:text-[#f85149] rounded transition"
                    title="Delete domain"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>

              {/* DNS Records Table */}
              <div className="p-4 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[#8b949e] border-b border-[#30363d] pb-2">
                      <th className="py-2 px-3 font-medium">Type</th>
                      <th className="py-2 px-3 font-medium">Host / Name</th>
                      <th className="py-2 px-3 font-medium">Value / Target</th>
                      <th className="py-2 px-3 font-medium">Status</th>
                      <th className="py-2 px-3 font-medium text-right">Copy</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#30363d]/50 font-mono text-[11px]">
                    {/* MX Record */}
                    <tr>
                      <td className="py-2.5 px-3 font-semibold text-[#58a6ff]">MX</td>
                      <td className="py-2.5 px-3 text-[#e6edf3]">@</td>
                      <td className="py-2.5 px-3 text-[#8b949e] break-all">{dom.mx_record}</td>
                      <td className="py-2.5 px-3">
                        <span className={dom.mx_status === "verified" ? "text-[#3fb950]" : "text-[#d29922]"}>
                          {dom.mx_status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          onClick={() => copyToClipboard(`mx-${dom.id}`, dom.mx_record)}
                          className="p-1 text-[#8b949e] hover:text-[#e6edf3] transition"
                        >
                          {copiedKey === `mx-${dom.id}` ? <Check className="w-3.5 h-3.5 text-[#3fb950]" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                      </td>
                    </tr>

                    {/* SPF Record */}
                    <tr>
                      <td className="py-2.5 px-3 font-semibold text-[#58a6ff]">TXT (SPF)</td>
                      <td className="py-2.5 px-3 text-[#e6edf3]">@</td>
                      <td className="py-2.5 px-3 text-[#8b949e] break-all">{dom.spf_record}</td>
                      <td className="py-2.5 px-3">
                        <span className={dom.spf_status === "verified" ? "text-[#3fb950]" : "text-[#d29922]"}>
                          {dom.spf_status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          onClick={() => copyToClipboard(`spf-${dom.id}`, dom.spf_record)}
                          className="p-1 text-[#8b949e] hover:text-[#e6edf3] transition"
                        >
                          {copiedKey === `spf-${dom.id}` ? <Check className="w-3.5 h-3.5 text-[#3fb950]" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                      </td>
                    </tr>

                    {/* DKIM Record */}
                    <tr>
                      <td className="py-2.5 px-3 font-semibold text-[#58a6ff]">TXT (DKIM)</td>
                      <td className="py-2.5 px-3 text-[#e6edf3]">{dom.dkim_selector}._domainkey</td>
                      <td className="py-2.5 px-3 text-[#8b949e] break-all">{dom.dkim_record}</td>
                      <td className="py-2.5 px-3">
                        <span className={dom.dkim_status === "verified" ? "text-[#3fb950]" : "text-[#d29922]"}>
                          {dom.dkim_status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          onClick={() => copyToClipboard(`dkim-${dom.id}`, dom.dkim_record)}
                          className="p-1 text-[#8b949e] hover:text-[#e6edf3] transition"
                        >
                          {copiedKey === `dkim-${dom.id}` ? <Check className="w-3.5 h-3.5 text-[#3fb950]" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                      </td>
                    </tr>

                    {/* DMARC Record */}
                    <tr>
                      <td className="py-2.5 px-3 font-semibold text-[#58a6ff]">TXT (DMARC)</td>
                      <td className="py-2.5 px-3 text-[#e6edf3]">_dmarc</td>
                      <td className="py-2.5 px-3 text-[#8b949e] break-all">{dom.dmarc_record}</td>
                      <td className="py-2.5 px-3">
                        <span className={dom.dmarc_status === "verified" ? "text-[#3fb950]" : "text-[#d29922]"}>
                          {dom.dmarc_status}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        <button
                          onClick={() => copyToClipboard(`dmarc-${dom.id}`, dom.dmarc_record)}
                          className="p-1 text-[#8b949e] hover:text-[#e6edf3] transition"
                        >
                          {copiedKey === `dmarc-${dom.id}` ? <Check className="w-3.5 h-3.5 text-[#3fb950]" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
