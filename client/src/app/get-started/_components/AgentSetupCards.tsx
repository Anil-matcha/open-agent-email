"use client";

import { useEffect, useState } from "react";
import { getstarted, type CreatedInbox, type DomainView } from "@/lib/getstarted-api";
import { CodeBlock, CopyButton, PrimaryButton, StepHeading } from "./ui";
import { InboxForm } from "./InboxForm";
import { claudeCodeSnippet, curlSnippet, cursorSnippet, pythonSnippet, typescriptSnippet } from "./snippets";

type Props = {
  sharedDomain: string;
  inbox: CreatedInbox | null;
  onCreated: (inbox: CreatedInbox) => void;
  onContinue: () => void;
};

export function AgentSetupCards({ sharedDomain, inbox, onCreated, onContinue }: Props) {
  const [domains, setDomains] = useState<DomainView[]>([]);
  const [chosenDomain, setChosenDomain] = useState<string | null>(null);
  const domain = chosenDomain ?? sharedDomain;

  useEffect(() => {
    getstarted.domains().then((all) => setDomains(all.filter((d) => d.capabilities?.receiving))).catch(() => setDomains([]));
  }, []);

  if (!inbox) {
    return (
      <div className="space-y-4">
        {domains.length > 0 && (
          <label className="flex items-center gap-2 text-[11px] text-[#8b949e]">
            Domain
            <select
              value={domain}
              onChange={(e) => setChosenDomain(e.target.value)}
              className="px-2 py-1 rounded bg-[#0d1117] border border-[#30363d] text-[11px] text-[#e6edf3]"
            >
              <option value={sharedDomain}>{sharedDomain} (shared)</option>
              {domains.map((d) => (
                <option key={d.id} value={d.domain}>{d.domain}</option>
              ))}
            </select>
          </label>
        )}
        <InboxForm
          domain={domain}
          title="Create an inbox for your agent"
          hint="We'll create the inbox and a key limited to it, then show you how to connect your agent."
          onCreated={onCreated}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <StepHeading title="Connect your agent" hint="Copy a snippet into your tool. The key below is limited to this one inbox." />
      <div className="card-simple p-3 space-y-2">
        <div className="text-xs text-[#8b949e]">
          Inbox <span className="font-mono text-[#e6edf3]">{inbox.email}</span>. This API key is shown once, so copy it now:
        </div>
        <div className="flex items-center gap-2">
          <code className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-[11px] font-mono text-[#e6edf3] break-all">{inbox.key}</code>
          <CopyButton text={inbox.key} />
        </div>
      </div>
      <CodeBlock label="Claude Code" code={claudeCodeSnippet(inbox.key)} />
      <CodeBlock label="Cursor (.cursor/mcp.json)" code={cursorSnippet(inbox.key)} />
      <CodeBlock label="Python" code={pythonSnippet(inbox.email, inbox.key)} />
      <CodeBlock label="TypeScript" code={typescriptSnippet(inbox.email, inbox.key)} />
      <CodeBlock label="curl" code={curlSnippet(inbox.email, inbox.key)} />
      <p className="text-[11px] text-[#8b949e]">
        These point at http://127.0.0.1:8000. Replace it with your server&apos;s address if the agent runs elsewhere.
      </p>
      <PrimaryButton onClick={onContinue}>Continue</PrimaryButton>
    </div>
  );
}
