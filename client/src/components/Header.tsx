"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Terminal, Shield, LogIn, LogOut } from "lucide-react";
import Link from "next/link";

export function Header() {
  const pathname = usePathname();
  const [authenticated, setAuthenticated] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const [token, setToken] = useState("");
  const [authError, setAuthError] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    fetch("/api/session", { cache: "no-store" }).then((r) => setAuthenticated(r.ok)).catch(() => setAuthenticated(false));
  }, []);

  const connect = async (event: React.FormEvent) => {
    event.preventDefault();
    setConnecting(true);
    setAuthError("");
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not connect to the API");
      setAuthenticated(true);
      setShowLogin(false);
      setToken("");
      window.location.reload();
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Could not connect to the API");
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async () => {
    await fetch("/api/session", { method: "DELETE" });
    setAuthenticated(false);
    window.location.reload();
  };

  const getPageTitle = () => {
    switch (pathname) {
      case "/":
        return { title: "Overview", subtitle: "System telemetry and email activity" };
      case "/inbox":
        return { title: "Inboxes", subtitle: "Two-way email threads" };
      case "/reviews":
        return { title: "Review Queue", subtitle: "Held messages awaiting human review" };
      case "/outbox":
        return { title: "Outbound Queue", subtitle: "Delivery jobs and retry history" };
      case "/simulator":
        return { title: "Simulator", subtitle: "Test inbound email threat scanner" };
      case "/agents":
        return { title: "Agent Directory", subtitle: "Manage email inboxes and policies" };
      case "/mcp":
        return { title: "MCP Integration", subtitle: "Connect Cursor and Claude Code" };
      default:
        return { title: "Dashboard", subtitle: "Open Agent Email Gateway" };
    }
  };

  const info = getPageTitle();

  return (
    <header className="h-14 border-b border-[#30363d] bg-[#161b22] px-6 flex items-center justify-between sticky top-0 z-20">
      <div>
        <h1 className="text-sm font-semibold text-[#e6edf3] flex items-center gap-2">
          {info.title}
          <span className="text-xs font-normal text-[#8b949e] hidden md:inline">
            — {info.subtitle}
          </span>
        </h1>
      </div>

      <div className="flex items-center gap-3">
        {authenticated ? (
          <button onClick={disconnect} className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#30363d] text-xs text-[#8b949e] hover:text-[#e6edf3]">
            <LogOut className="w-3.5 h-3.5" /> Disconnect
          </button>
        ) : (
          <button onClick={() => setShowLogin(true)} className="flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#30363d] bg-[#238636] text-xs text-white hover:bg-[#2ea043]">
            <LogIn className="w-3.5 h-3.5" /> Connect dashboard
          </button>
        )}
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#30363d] bg-[#0d1117] text-xs text-[#8b949e]">
          <span className="w-1.5 h-1.5 rounded-full bg-[#2ea043]" />
          <span className="font-mono text-[11px] text-[#e6edf3]">@agents.local</span>
        </div>

        <Link
          href="/simulator"
          className="px-3 py-1 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-xs font-medium text-[#e6edf3] transition-colors"
        >
          Test Inbound
        </Link>

        <a
          href="http://127.0.0.1:8000/docs"
          target="_blank"
          rel="noreferrer"
          className="p-1.5 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d] transition-colors text-xs"
          title="OpenAPI Documentation"
        >
          <Terminal className="w-3.5 h-3.5" />
        </a>
      </div>
      {showLogin && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <form onSubmit={connect} className="w-full max-w-md rounded-lg border border-[#30363d] bg-[#161b22] p-6 space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-[#e6edf3]">Connect to Open Agent Email</h2>
              <p className="mt-2 text-xs leading-5 text-[#8b949e]">Enter the <code>ADMIN_API_KEY</code> configured for the API server. The dashboard keeps it in a secure, HttpOnly session cookie.</p>
            </div>
            <input autoFocus type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Administrator API key" className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3]" />
            {authError && <p className="text-xs text-[#f85149]">{authError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setShowLogin(false)} className="px-3 py-2 text-xs text-[#8b949e]">Cancel</button>
              <button type="submit" disabled={!token.trim() || connecting} className="px-3 py-2 rounded bg-[#238636] text-xs text-white disabled:opacity-50">{connecting ? "Connecting…" : "Connect"}</button>
            </div>
          </form>
        </div>
      )}
    </header>
  );
}
