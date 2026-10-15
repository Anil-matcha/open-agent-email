"use client";

import { useCallback, useEffect, useState } from "react";
import { getstarted, type CreatedInbox, type DomainView } from "@/lib/getstarted-api";
import { CopyButton, ErrorNote, PrimaryButton, SecondaryButton, StepHeading } from "./ui";
import { InboxForm } from "./InboxForm";

type RecordState = "verified" | "missing" | "failed";

const STATE_TEXT: Record<RecordState, { label: string; tone: string }> = {
  verified: { label: "Verified", tone: "text-[#3fb950]" },
  missing: { label: "Not found yet", tone: "text-[#d29922]" },
  failed: { label: "Found, but the value is wrong or the lookup failed", tone: "text-[#f85149]" },
};

function stateOf(domain: DomainView, key: "mx" | "spf" | "dkim" | "dmarc"): RecordState {
  const fromServer = domain.status_detail?.[key];
  if (fromServer) return fromServer;
  const raw = domain[`${key}_status` as "mx_status"] as string;
  if (raw === "verified") return "verified";
  return raw === "pending" ? "missing" : "failed";
}

type Props = {
  initialDomain?: string;
  onDomainChange: (domain: string) => void;
  onLater: () => void;
  onInboxCreated: (inbox: CreatedInbox) => void;
};

export function CustomDomainChecklist({ initialDomain, onDomainChange, onLater, onInboxCreated }: Props) {
  const [input, setInput] = useState("");
  const [domain, setDomain] = useState<DomainView | null>(null);
  const [loading, setLoading] = useState(Boolean(initialDomain));
  const [busy, setBusy] = useState(false);
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async (name: string) => {
    const all = await getstarted.domains();
    return all.find((d) => d.domain.toLowerCase() === name.toLowerCase()) ?? null;
  }, []);

  useEffect(() => {
    if (!initialDomain) return;
    let cancelled = false;
    load(initialDomain)
      .then((found) => {
        if (cancelled) return;
        if (found) setDomain(found);
        else setError(`${initialDomain} is not registered yet. Enter it below to start.`);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not load the domain"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [initialDomain, load]);

  const register = async () => {
    const name = input.trim().toLowerCase();
    setBusy(true);
    setError("");
    try {
      let created: DomainView | null = null;
      try {
        created = await getstarted.createDomain(name);
      } catch (e) {
        // A domain registered earlier is fine to continue with.
        created = await load(name).catch(() => null);
        if (!created) throw e;
      }
      setDomain(created);
      onDomainChange(created.domain);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not register the domain");
    } finally {
      setBusy(false);
    }
  };

  const check = async () => {
    if (!domain) return;
    setBusy(true);
    setError("");
    try {
      setDomain(await getstarted.verifyDomain(domain.domain));
      setCheckedAt(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : "The check could not run");
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="text-xs text-[#8b949e]">Loading your domain...</div>;

  if (!domain) {
    return (
      <div className="space-y-3">
        <StepHeading title="Register your domain" hint="Use a domain or subdomain you can add DNS records to, for example agents.yourcompany.com." />
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="agents.yourcompany.com"
            aria-label="Domain"
            className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs text-[#e6edf3] font-mono"
          />
          <PrimaryButton onClick={register} disabled={busy || !input.includes(".")}>
            {busy ? "Registering..." : "Register domain"}
          </PrimaryButton>
        </div>
        <ErrorNote message={error} />
      </div>
    );
  }

  const rows = [
    { key: "mx" as const, type: "MX", host: "@", value: domain.mx_record },
    { key: "spf" as const, type: "TXT (SPF)", host: "@", value: domain.spf_record },
    { key: "dkim" as const, type: "TXT (DKIM)", host: `${domain.dkim_selector}._domainkey`, value: domain.dkim_record },
    { key: "dmarc" as const, type: "TXT (DMARC)", host: "_dmarc", value: domain.dmarc_record },
  ];
  const receiving = Boolean(domain.capabilities?.receiving ?? domain.mx_status === "verified");
  const sending = Boolean(domain.capabilities?.sending ?? (domain.spf_status === "verified" && domain.dkim_status === "verified"));

  return (
    <div className="space-y-4">
      <StepHeading
        title={`Publish these DNS records for ${domain.domain}`}
        hint="Add them at your DNS provider, then press Check. Changes can take a few minutes, sometimes hours, to appear."
      />
      <div className="card-simple overflow-x-auto">
        <table className="w-full text-left text-[11px]">
          <thead>
            <tr className="text-[#8b949e] border-b border-[#30363d]">
              <th className="py-2 px-3 font-medium">Type</th>
              <th className="py-2 px-3 font-medium">Host / Name</th>
              <th className="py-2 px-3 font-medium">Value / Target</th>
              <th className="py-2 px-3 font-medium">Status</th>
              <th className="py-2 px-3 font-medium text-right">Copy</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#30363d]/50 font-mono">
            {rows.map((row) => {
              const state = STATE_TEXT[stateOf(domain, row.key)];
              return (
                <tr key={row.key}>
                  <td className="py-2 px-3 font-semibold text-[#58a6ff] whitespace-nowrap">{row.type}</td>
                  <td className="py-2 px-3 text-[#e6edf3]">{row.host}</td>
                  <td className="py-2 px-3 text-[#8b949e] break-all max-w-xs">{row.value}</td>
                  <td className={`py-2 px-3 font-sans ${state.tone}`}>{state.label}</td>
                  <td className="py-2 px-3 text-right whitespace-nowrap">
                    <CopyButton text={row.host} label="Host" /> <CopyButton text={row.value} label="Value" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <PrimaryButton onClick={check} disabled={busy}>{busy ? "Checking..." : "Check records"}</PrimaryButton>
        {checkedAt && <span className="text-[11px] text-[#8b949e]">Checked at {checkedAt.toLocaleTimeString()}</span>}
        {!receiving && <SecondaryButton onClick={onLater}>Finish later</SecondaryButton>}
      </div>
      <ErrorNote message={error} />

      <ul className="text-xs space-y-1">
        <li className={receiving ? "text-[#3fb950]" : "text-[#8b949e]"}>
          {receiving ? "Ready to receive mail on this domain." : "Receiving unlocks when the MX record is verified."}
        </li>
        <li className={sending ? "text-[#3fb950]" : "text-[#8b949e]"}>
          {sending ? "Ready to send from this domain." : "Sending unlocks when both SPF and DKIM are verified. DMARC is recommended but optional."}
        </li>
      </ul>

      {receiving && (
        <div className="card-simple p-4">
          <InboxForm
            domain={domain.domain}
            hint={sending ? undefined : "You can create the inbox now and receive mail. Sending from it works once SPF and DKIM verify, and also needs a mail provider under Settings."}
            onCreated={onInboxCreated}
          />
        </div>
      )}
    </div>
  );
}
