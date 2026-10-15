"use client";

import { useState } from "react";
import { getstarted, type CreatedInbox, type KeyScope } from "@/lib/getstarted-api";
import { ErrorNote, PrimaryButton, StepHeading } from "./ui";
import { validLocalPart } from "./snippets";

type Props = {
  domain: string;
  title?: string;
  hint?: string;
  onCreated: (inbox: CreatedInbox) => void;
};

export function InboxForm({ domain, title = "Name your inbox", hint, onCreated }: Props) {
  const [local, setLocal] = useState("hello");
  const [hold, setHold] = useState(false);
  const [scope, setScope] = useState<KeyScope>("full_access");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const address = `${local.trim().toLowerCase()}@${domain}`;
  const valid = validLocalPart(local);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onCreated(await getstarted.createInbox(address, hold, scope));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the inbox");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <StepHeading title={title} hint={hint} />
      <div className="flex items-center gap-2">
        <input
          value={local}
          onChange={(e) => setLocal(e.target.value)}
          aria-label="Inbox name"
          className="w-48 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs text-[#e6edf3] font-mono"
        />
        <span className="text-xs text-[#8b949e] font-mono">@{domain}</span>
      </div>
      {!valid && local.trim() !== "" && (
        <div className="text-[11px] text-[#d29922]">Use letters, numbers, dots, dashes, underscores or plus signs.</div>
      )}
      <label className="flex items-start gap-2 text-[11px] text-[#8b949e]">
        <input type="checkbox" className="mt-0.5" checked={hold} onChange={(e) => setHold(e.target.checked)} />
        <span>Hold suspicious and outgoing mail for a person to approve. Recommended for real agents; leave off for a quick try.</span>
      </label>
      <label className="flex items-center gap-2 text-[11px] text-[#8b949e]">
        Key permissions
        <select
          value={scope}
          onChange={(e) => setScope(e.target.value as KeyScope)}
          className="px-2 py-1 rounded bg-[#0d1117] border border-[#30363d] text-[11px] text-[#e6edf3]"
        >
          <option value="full_access">Read and send</option>
          <option value="read_only">Read only</option>
        </select>
      </label>
      <div className="flex items-center gap-3">
        <PrimaryButton onClick={submit} disabled={busy || !valid}>
          {busy ? "Creating..." : "Create inbox"}
        </PrimaryButton>
        <span className="text-[11px] text-[#8b949e] font-mono">{valid ? address : ""}</span>
      </div>
      <ErrorNote message={error} />
    </div>
  );
}
