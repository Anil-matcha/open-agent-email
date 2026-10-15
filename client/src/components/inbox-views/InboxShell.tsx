"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { Agent } from "@/lib/types";
import { agentsApi, InboxMetrics } from "@/lib/agents-api";
import { Dropdown } from "@/components/Dropdown";

export const INBOX_TABS = [
  { slug: "messages", label: "Messages" },
  { slug: "followups", label: "Follow-ups" },
  { slug: "suppressions", label: "Suppressions" },
  { slug: "trash", label: "Trash" },
  { slug: "settings", label: "Settings" },
] as const;

// Shared chrome for every per-inbox route: inbox picker, summary strip and tabs.
// The inbox is chosen with the `?email=` query parameter.
export function InboxShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const params = useSearchParams();
  const email = params.get("email") ?? "";
  const isIndex = pathname.replace(/\/+$/, "") === "/inboxes";

  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [metrics, setMetrics] = useState<InboxMetrics | null>(null);

  useEffect(() => {
    if (isIndex) return;
    let active = true;
    api.getAgents().then((list) => { if (active) setAgents(list); }).catch(() => { if (active) setAgents([]); });
    return () => { active = false; };
  }, [isIndex]);

  // Without an inbox in the URL, pick the first one (or go back to the list when there are none).
  useEffect(() => {
    if (isIndex || agents === null || email) return;
    if (agents.length > 0) router.replace(`${pathname}?email=${encodeURIComponent(agents[0].email)}`);
    else router.replace("/inboxes");
  }, [isIndex, agents, email, pathname, router]);

  const refreshMetrics = useCallback(() => {
    if (!email) return;
    agentsApi.getMetrics(email).then(setMetrics).catch(() => setMetrics(null));
  }, [email]);

  useEffect(() => {
    if (isIndex || !email) return;
    refreshMetrics();
    const timer = window.setInterval(refreshMetrics, 30_000);
    window.addEventListener("focus", refreshMetrics);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refreshMetrics); };
  }, [isIndex, email, pathname, refreshMetrics]);

  if (isIndex) return <>{children}</>;

  const current = agents?.find((ag) => ag.email === email) ?? null;
  const activeTab = INBOX_TABS.find((tab) => pathname.startsWith(`/inboxes/${tab.slug}`))?.slug ?? "messages";
  const options = (agents ?? []).map((ag) => ({ value: ag.email, label: ag.display_name, sublabel: ag.email }));
  const tabHref = (slug: string) => `/inboxes/${slug}?email=${encodeURIComponent(email)}`;

  return (
    <div className="space-y-4" data-testid="inbox-shell">
      <div className="card-simple p-3 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/inboxes" className="text-xs text-[#8b949e] hover:text-[#e6edf3]">← All inboxes</Link>
          <div className="w-64">
            <Dropdown
              options={options}
              value={email}
              onChange={(value) => router.push(`${pathname}?email=${encodeURIComponent(value)}`)}
              placeholder="Select an inbox"
            />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <div className="min-w-0">
            <div className="font-semibold text-[#e6edf3]">{current?.display_name ?? "Inbox"}</div>
            <div className="font-mono text-[11px] text-blue-400 break-all">{email}</div>
          </div>
          <Link href={tabHref("messages")} className="px-2.5 py-1 rounded bg-[#0d1117] border border-[#30363d] text-[#e6edf3]">
            <span className="font-semibold">{metrics ? metrics.unread : "–"}</span> <span className="text-[#8b949e]">unread</span>
          </Link>
          <Link href="/reviews" className="px-2.5 py-1 rounded bg-[#0d1117] border border-[#30363d] text-[#e6edf3]">
            <span className="font-semibold">{metrics ? metrics.pending_reviews : "–"}</span> <span className="text-[#8b949e]">held for review</span>
          </Link>
        </div>
      </div>

      <nav aria-label="Inbox sections" className="flex gap-1 border-b border-[#30363d] overflow-x-auto">
        {INBOX_TABS.map((tab) => {
          const selected = tab.slug === activeTab;
          return (
            <Link
              key={tab.slug}
              href={tabHref(tab.slug)}
              aria-current={selected ? "page" : undefined}
              className={`px-4 py-2 text-xs whitespace-nowrap border-b-2 -mb-px transition-colors ${
                selected ? "border-blue-400 text-[#e6edf3] font-medium" : "border-transparent text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {tab.label}
              {tab.slug === "messages" && metrics && metrics.unread > 0 ? <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[#238636]/30 text-[#3fb950]">{metrics.unread}</span> : null}
            </Link>
          );
        })}
      </nav>

      {agents !== null && email && !current ? (
        <div role="alert" className="card-simple p-6 text-xs text-[#ff7b72]">
          No inbox named {email}. <Link href="/inboxes" className="underline">Back to all inboxes</Link>
        </div>
      ) : email ? (
        <div key={email}>{children}</div>
      ) : (
        <div className="card-simple p-6 text-xs text-[#8b949e]">Loading…</div>
      )}
    </div>
  );
}
