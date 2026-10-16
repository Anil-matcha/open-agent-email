"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard,
  Mail,
  ShieldAlert,
  Globe,
  Sliders,
  Key,
  Cpu,
  Archive,
  Webhook,
  Users,
  FileText,
  Activity,
  UserRound,
  Rocket,
  BarChart3,
  Server,
} from "lucide-react";
import { api } from "@/lib/api";
import { BADGE_REFRESH_EVENT } from "@/lib/badges";

type NavItem = {
  name: string;
  href: string;
  icon: LucideIcon;
  /** Extra path prefixes that also mark this item active. */
  also?: string[];
  badge?: number;
  badgeTone?: "unread" | "review";
};

type NavGroup = { label?: string; items: NavItem[] };

const POLL_MS = 8000;

function matches(pathname: string, item: NavItem): boolean {
  const hrefs = [item.href, ...(item.also ?? [])];
  return hrefs.some((href) => (href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`)));
}

export function Sidebar() {
  const pathname = usePathname();
  const [pendingReviews, setPendingReviews] = useState(0);
  const [unreadMessages, setUnreadMessages] = useState(0);
  const [inboxCount, setInboxCount] = useState<number | null>(null);
  const [settingsTab, setSettingsTab] = useState("mail");
  const [extraNav, setExtraNav] = useState<{ items: { name: string; href: string }[]; hide: string[] }>({ items: [], hide: [] });

  // Poll, refresh on every navigation, when the tab regains focus, and when a screen asks for it.
  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const stats = await api.getStats();
        if (cancelled) return;
        setPendingReviews(stats.pending_reviews);
        setUnreadMessages(stats.unread_messages);
        setInboxCount(stats.total_agents);
      } catch {
        // not connected yet, or the server is restarting: keep the last numbers
      }
      if (!cancelled) {
        setSettingsTab(new URLSearchParams(window.location.search).get("tab") === "server" ? "server" : "mail");
      }
    };
    run();
    const interval = setInterval(run, POLL_MS);
    window.addEventListener("focus", run);
    window.addEventListener(BADGE_REFRESH_EVENT, run);
    return () => {
      cancelled = true;
      clearInterval(interval);
      window.removeEventListener("focus", run);
      window.removeEventListener(BADGE_REFRESH_EVENT, run);
    };
  }, [pathname]);

  useEffect(() => {
    fetch("/api/extra-nav", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setExtraNav({ items: Array.isArray(d.items) ? d.items : [], hide: Array.isArray(d.hide) ? d.hide : [] }))
      .catch(() => undefined);
  }, [pathname]);

  const needsSetup = inboxCount === 0;

  const groups: NavGroup[] = [
    {
      items: [
        ...(needsSetup ? [] : [{ name: "Get started", href: "/get-started", icon: Rocket }]),
        { name: "Overview", href: "/", icon: LayoutDashboard },
      ],
    },
    {
      label: "Mail",
      items: [
        {
          name: "Inboxes",
          href: "/inboxes",
          icon: Mail,
          badge: unreadMessages > 0 ? unreadMessages : undefined,
          badgeTone: "unread",
        },
        {
          name: "Pending",
          href: "/reviews",
          icon: ShieldAlert,
          badge: pendingReviews > 0 ? pendingReviews : undefined,
          badgeTone: "review",
        },
        { name: "Outbox", href: "/outbox", icon: Archive },
      ],
    },
    {
      label: "Insights",
      items: [
        { name: "Metrics", href: "/metrics", icon: BarChart3 },
        { name: "Event log", href: "/events", icon: Activity },
      ],
    },
    {
      label: "Audience",
      items: [
        { name: "Contacts", href: "/contacts", icon: Users },
        { name: "Templates", href: "/templates", icon: FileText },
      ],
    },
    {
      label: "Setup",
      items: [
        { name: "Domains & DNS", href: "/domains", icon: Globe },
        { name: "API Keys", href: "/api-keys", icon: Key },
        { name: "Webhooks", href: "/webhooks", icon: Webhook },
        { name: "Settings", href: "/settings", icon: Sliders },
      ],
    },
    {
      label: "More",
      items: [
        { name: "Account", href: "/account", icon: UserRound },
        { name: "MCP Gateway", href: "/mcp", icon: Cpu },
        ...extraNav.items.map((item) => ({ ...item, icon: UserRound })),
      ],
    },
  ]
    .map((group) => ({ ...group, items: group.items.filter((item) => !extraNav.hide.includes(item.href)) }))
    .filter((group) => group.items.length > 0);

  // "Messages" and "Inboxes" share /inbox; let only the more specific entry claim it.
  const isActive = (item: NavItem) => {
    if (item.name === "Inboxes") return pathname === "/inboxes" || pathname.startsWith("/inboxes/");
    return matches(pathname, item);
  };

  return (
    <aside className="w-56 border-r border-[#30363d] bg-[#161b22] flex flex-col justify-between h-screen sticky top-0 shrink-0 select-none">
      <div className="min-h-0 flex flex-col">
        {/* Brand Header */}
        <div className="p-4 border-b border-[#30363d]">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-md bg-[#238636] flex items-center justify-center text-white font-bold text-xs">
              <Mail className="w-4 h-4" />
            </div>
            <div>
              <div className="text-xs font-semibold text-[#e6edf3] tracking-tight">Open Agent Email</div>
              <div className="text-[10px] text-[#8b949e]">Gateway & Infrastructure</div>
            </div>
          </Link>
        </div>

        {/* Until there is an inbox, point at the guided setup first. */}
        {needsSetup && (
          <div className="p-2 pb-0">
            <Link
              href="/get-started"
              aria-current={pathname.startsWith("/get-started") ? "page" : undefined}
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-md text-xs font-semibold border transition-colors ${
                pathname.startsWith("/get-started")
                  ? "bg-[#238636] text-white border-[#2ea043]"
                  : "bg-[#238636]/15 text-[#3fb950] border-[#3fb950]/40 hover:bg-[#238636]/25"
              }`}
            >
              <Rocket className="w-4 h-4" />
              <span className="flex-1">Get started</span>
              <span className="text-[10px] font-normal opacity-80">3 min</span>
            </Link>
          </div>
        )}

        {/* Navigation */}
        <nav className="p-2 space-y-3 overflow-y-auto" aria-label="Main">
          {groups.map((group, index) => (
            <div key={group.label ?? `group-${index}`} className="space-y-0.5">
              {group.label && (
                <div className="px-3 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-wider text-[#6e7681]">
                  {group.label}
                </div>
              )}
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = isActive(item);
                return (
                  <div key={item.href + item.name}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                        active
                          ? "bg-[#21262d] text-white font-semibold"
                          : "text-[#8b949e] hover:text-[#e6edf3] hover:bg-[#21262d]/50"
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <Icon className="w-4 h-4 text-[#8b949e]" />
                        <span>{item.name}</span>
                      </div>
                      {item.badge !== undefined && (
                        <span
                          aria-label={`${item.badge} ${item.badgeTone === "unread" ? "unread" : "pending"}`}
                          className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                            item.badgeTone === "unread"
                              ? "bg-[#238636]/20 text-[#3fb950] border border-[#3fb950]/30"
                              : "bg-[#9e6a03]/30 text-[#d29922] border border-[#d29922]/30"
                          }`}
                        >
                          {item.badge}
                        </span>
                      )}
                    </Link>
                    {item.href === "/settings" && pathname.startsWith("/settings") && (
                      <div className="ml-6 mt-0.5 space-y-0.5 border-l border-[#30363d] pl-2">
                        <Link
                          href="/settings"
                          onClick={() => setSettingsTab("mail")}
                          className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[11px] ${
                            settingsTab === "mail" ? "text-white bg-[#21262d]" : "text-[#8b949e] hover:text-[#e6edf3]"
                          }`}
                        >
                          <Sliders className="w-3 h-3" />
                          Mail provider
                        </Link>
                        <Link
                          href="/settings?tab=server"
                          onClick={() => setSettingsTab("server")}
                          className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[11px] ${
                            settingsTab === "server" ? "text-white bg-[#21262d]" : "text-[#8b949e] hover:text-[#e6edf3]"
                          }`}
                        >
                          <Server className="w-3 h-3" />
                          Server
                        </Link>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </nav>
      </div>

      {/* SMTP Listener Status Footer */}
      <div className="p-3 border-t border-[#30363d] text-[11px] text-[#8b949e] flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#238636] inline-block animate-pulse" />
          <span>SMTP Port 2525</span>
        </div>
        <span className="text-[10px] font-mono text-[#8b949e]">READY</span>
      </div>
    </aside>
  );
}
