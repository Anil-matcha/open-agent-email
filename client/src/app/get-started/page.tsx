"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getstarted, type CreatedInbox, type DomainView, type ReadinessItem } from "@/lib/getstarted-api";
import { AddressChoice } from "./_components/AddressChoice";
import { AgentSetupCards } from "./_components/AgentSetupCards";
import { CustomDomainChecklist } from "./_components/CustomDomainChecklist";
import { InboxForm } from "./_components/InboxForm";
import { SetupMethodChoice } from "./_components/SetupMethodChoice";
import { SuccessPanel } from "./_components/SuccessPanel";
import { PrimaryButton, SecondaryButton } from "./_components/ui";

// The current step lives in the URL (?step=) so the browser Back button walks the funnel.
const STEPS = ["choose", "address", "shared_form", "custom_checklist", "agent_mcp", "success"] as const;
type Step = (typeof STEPS)[number];

function isStep(value: string | null): value is Step {
  return value !== null && (STEPS as readonly string[]).includes(value);
}

function GetStarted() {
  const router = useRouter();
  const params = useSearchParams();
  const rawStep = params.get("step");
  const step: Step = isStep(rawStep) ? rawStep : "choose";
  const domainParam = params.get("domain") ?? undefined;

  const [inbox, setInbox] = useState<CreatedInbox | null>(null);
  const [sharedDomain, setSharedDomain] = useState("agents.local");
  const [domains, setDomains] = useState<DomainView[]>([]);
  const [readiness, setReadiness] = useState<ReadinessItem[] | null>(null);

  useEffect(() => {
    getstarted.health().then((h) => h.shared_domain && setSharedDomain(h.shared_domain)).catch(() => undefined);
    getstarted.domains().then(setDomains).catch(() => setDomains([]));
    // Readiness needs the administrator credential; if it is unavailable we simply show less.
    getstarted.readiness().then((r) => setReadiness(r.items)).catch(() => setReadiness(null));
  }, []);

  const go = useCallback(
    (next: Step, extra: Record<string, string> = {}) => {
      router.push(`/get-started?${new URLSearchParams({ step: next, ...extra }).toString()}`);
    },
    [router],
  );

  const created = (value: CreatedInbox, next: Step | null) => {
    setInbox(value);
    if (next) go(next);
  };

  const relayConfigured = readiness ? (readiness.find((item) => item.id === "relay")?.ok ?? null) : null;
  const blockers = (readiness ?? []).filter((item) => item.severity === "required" && !item.ok && item.id !== "relay");

  const alreadyCreated = inbox && (step === "shared_form" || step === "custom_checklist") && (
    <div className="card-simple p-4 space-y-3">
      <div className="text-xs text-[#e6edf3]">
        You already created <span className="font-mono">{inbox.email}</span>.
      </div>
      <div className="flex gap-2">
        <PrimaryButton onClick={() => go("success")}>Continue</PrimaryButton>
        <SecondaryButton onClick={() => setInbox(null)}>Create another</SecondaryButton>
      </div>
    </div>
  );

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <header className="space-y-1">
        <div className="text-[10px] uppercase tracking-wide text-[#8b949e]">Onboarding · about 3 minutes</div>
        <h1 className="text-lg font-semibold text-[#e6edf3]">Set up your first inbox</h1>
        <p className="text-xs text-[#8b949e]">
          Create an email address for an agent or an application. You can change all of this later from the dashboard.
        </p>
      </header>

      {blockers.length > 0 && (
        <div className="card-simple p-3 space-y-1 border-[#d29922]">
          {blockers.map((item) => (
            <div key={item.id} className="text-xs text-[#d29922]">
              {item.label}: {item.fix ?? item.detail}
            </div>
          ))}
        </div>
      )}

      {step !== "choose" && (
        <button type="button" onClick={() => router.back()} className="text-xs text-[#8b949e] hover:text-[#e6edf3]">
          ← Back
        </button>
      )}

      {step === "choose" && (
        <SetupMethodChoice onChoose={(method) => go(method === "agent" ? "agent_mcp" : "address")} />
      )}

      {step === "address" && (
        <AddressChoice
          sharedDomain={sharedDomain}
          domains={domains}
          onShared={() => go("shared_form")}
          onCustom={() => go("custom_checklist")}
          onResume={(domain) => go("custom_checklist", { domain })}
        />
      )}

      {step === "shared_form" && (alreadyCreated || (
        <div className="card-simple p-4">
          <InboxForm domain={sharedDomain} onCreated={(value) => created(value, "success")} />
        </div>
      ))}

      {step === "custom_checklist" && (alreadyCreated || (
        <CustomDomainChecklist
          initialDomain={domainParam}
          onDomainChange={(domain) => router.replace(`/get-started?step=custom_checklist&domain=${encodeURIComponent(domain)}`)}
          onLater={() => go("address")}
          onInboxCreated={(value) => created(value, "success")}
        />
      ))}

      {step === "agent_mcp" && (
        <AgentSetupCards
          sharedDomain={sharedDomain}
          inbox={inbox}
          onCreated={(value) => created(value, null)}
          onContinue={() => go("success")}
        />
      )}

      {step === "success" &&
        (inbox ? (
          <SuccessPanel inbox={inbox} relayConfigured={relayConfigured} />
        ) : (
          <div className="card-simple p-4 space-y-2">
            <div className="text-xs text-[#e6edf3]">Nothing was created in this session yet.</div>
            <div className="text-xs text-[#8b949e]">
              The key is only shown once, so this page cannot be reopened later. Start again to create an inbox, or{" "}
              <Link href="/agents" className="text-[#58a6ff] hover:underline">manage existing inboxes</Link>.
            </div>
            <PrimaryButton onClick={() => go("choose")}>Start again</PrimaryButton>
          </div>
        ))}
    </div>
  );
}

export default function GetStartedPage() {
  return (
    <Suspense fallback={<div className="text-xs text-[#8b949e]">Loading...</div>}>
      <GetStarted />
    </Suspense>
  );
}
