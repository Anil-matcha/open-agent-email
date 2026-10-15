"use client";

import Link from "next/link";
import type { DomainView } from "@/lib/getstarted-api";
import { ChoiceCard, StepHeading } from "./ui";
import { isLocalOnlyDomain } from "./snippets";

type Props = {
  sharedDomain: string;
  domains: DomainView[];
  onShared: () => void;
  onCustom: () => void;
  onResume: (domain: string) => void;
};

export function AddressChoice({ sharedDomain, domains, onShared, onCustom, onResume }: Props) {
  const pending = domains.filter((d) => !d.capabilities?.receiving);
  return (
    <div className="space-y-4">
      <StepHeading title="Pick the address your inbox will use" hint="The shared domain is the quickest way to try things. Your own domain looks professional and lets you send from your name." />
      <div className="grid gap-3 md:grid-cols-2">
        <ChoiceCard
          title="Shared domain"
          badge="Fastest"
          body={
            isLocalOnlyDomain(sharedDomain)
              ? `Inboxes end in @${sharedDomain}. This is a local-only name: it works for receiving on this machine, but is not reachable from the internet.`
              : `Inboxes end in @${sharedDomain}. No DNS setup needed.`
          }
          onClick={onShared}
        />
        <ChoiceCard
          title="Your own domain"
          body="Register a domain you control and publish a few DNS records. About 5 minutes of work, plus DNS propagation."
          onClick={onCustom}
        />
      </div>
      {isLocalOnlyDomain(sharedDomain) && (
        <p className="text-[11px] text-[#8b949e]">
          To use a real shared domain, set it on the{" "}
          <Link href="/settings?tab=server&key=SHARED_DOMAIN" className="text-[#58a6ff] hover:underline">Server settings tab</Link>.
        </p>
      )}
      {pending.length > 0 && (
        <div className="card-simple p-3 space-y-2">
          <div className="text-xs font-semibold text-[#e6edf3]">Pick up where you left off</div>
          {pending.map((d) => (
            <div key={d.id} className="flex items-center justify-between text-xs">
              <span className="font-mono text-[#e6edf3]">{d.domain}</span>
              <button type="button" onClick={() => onResume(d.domain)} className="text-[#58a6ff] hover:underline">
                Continue DNS setup
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
