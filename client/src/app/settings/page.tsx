"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { RelaySettings } from "./RelaySettings";
import { ServerSettings } from "./ServerSettings";

type Tab = "relay" | "server";

const TABS: { id: Tab; label: string }[] = [
  { id: "relay", label: "Mail provider" },
  { id: "server", label: "Server" },
];

function SettingsTabs() {
  // Links from the setup checklist open this page as /settings?tab=server&key=PUBLIC_BASE_URL.
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>(params.get("tab") === "server" ? "server" : "relay");
  const focusKey = params.get("key");

  return (
    <div className="p-8 max-w-4xl mx-auto space-y-6">
      <div role="tablist" aria-label="Settings" className="flex gap-1 border-b border-[#30363d]">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={`px-4 py-2 text-xs font-semibold border-b-2 -mb-px transition ${
              tab === item.id ? "border-[#58a6ff] text-[#e6edf3]" : "border-transparent text-[#8b949e] hover:text-[#e6edf3]"
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "relay" ? <RelaySettings /> : <ServerSettings focusKey={focusKey} />}
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<div className="p-12 text-center text-xs text-[#8b949e]">Loading settings...</div>}>
      <SettingsTabs />
    </Suspense>
  );
}
