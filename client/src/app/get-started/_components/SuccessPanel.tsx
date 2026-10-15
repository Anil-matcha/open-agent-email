"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getstarted, type CreatedInbox } from "@/lib/getstarted-api";
import type { Message } from "@/lib/types";
import { CodeBlock, CopyButton, ErrorNote, PrimaryButton, StepHeading } from "./ui";
import { isLocalOnlyDomain, smtpSnippet } from "./snippets";

type Props = {
  inbox: CreatedInbox;
  /** True or false when the readiness check answered, null when it could not be read. */
  relayConfigured: boolean | null;
};

function describe(message: Message): string {
  switch (message.status) {
    case "delivered":
      return "Delivered to a local inbox.";
    case "accepted_by_relay":
      return "Accepted by your mail provider. That does not confirm it reached the recipient.";
    case "sending":
      return "Still sending. Check the Outbox for the final result.";
    case "pending_review":
      return "Held for approval. Approve it on the Reviews page and it will be sent.";
    case "failed":
      return `Failed: ${message.delivery_error || "no reason was recorded"}.`;
    case "delivery_unknown":
      return "The provider did not confirm acceptance. Check the Outbox before resending.";
    case "blocked":
      return "Blocked because the recipient is suppressed for this inbox.";
    default:
      return `Status: ${message.status}.`;
  }
}

export function SuccessPanel({ inbox, relayConfigured }: Props) {
  const [received, setReceived] = useState<Message | null>(null);
  const [recipient, setRecipient] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Message | null>(null);
  const [sendError, setSendError] = useState("");
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;
    return () => {
      stopped.current = true;
    };
  }, []);

  // Watch for inbound mail so a message delivered over SMTP shows up here.
  useEffect(() => {
    if (received) return;
    const check = async () => {
      try {
        const inbound = await getstarted.inbound(inbox.email);
        if (inbound.length > 0 && !stopped.current) setReceived(inbound[0]);
      } catch {
        // Keep polling; a transient error is not worth surfacing.
      }
    };
    check();
    const timer = setInterval(check, 3000);
    return () => clearInterval(timer);
  }, [inbox.email, received]);

  const sendTest = async () => {
    setSending(true);
    setSendError("");
    setSent(null);
    try {
      const first = await getstarted.sendTest(inbox.email, recipient.trim());
      setSent(first);
      // Delivery happens in the background; follow it briefly so the result shown is the real one.
      for (let attempt = 0; attempt < 8 && !stopped.current; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const latest = await getstarted.message(inbox.email, first.id);
        setSent(latest);
        if (latest.status !== "sending") break;
      }
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Could not send the message");
    } finally {
      setSending(false);
    }
  };

  const domain = inbox.email.split("@")[1] ?? "";
  const localOnly = isLocalOnlyDomain(domain);

  return (
    <div className="space-y-5">
      <div className="card-simple p-4 space-y-3">
        <StepHeading title="Your inbox is ready" />
        <div className="flex items-center gap-2">
          <code className="px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs font-mono text-[#e6edf3]">{inbox.email}</code>
          <CopyButton text={inbox.email} />
        </div>
        {inbox.key ? (
          <div className="space-y-1">
            <div className="text-[11px] text-[#8b949e]">
              API key ({inbox.keyScope === "read_only" ? "read only" : "read and send"}), shown once:
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-[11px] font-mono text-[#e6edf3] break-all">{inbox.key}</code>
              <CopyButton text={inbox.key} />
            </div>
          </div>
        ) : null}
      </div>

      <section className="card-simple p-4 space-y-2">
        <StepHeading
          title="Receive a test email"
          hint="Run this in a terminal on this machine to deliver a message over SMTP. It will appear below."
        />
        <CodeBlock label="Deliver a test message" code={smtpSnippet(inbox.email)} />
        {received ? (
          <div className="p-2 rounded border border-[#238636] text-xs text-[#e6edf3]">
            Received from <span className="font-mono">{received.sender}</span>: {received.subject}
          </div>
        ) : (
          <div className="text-[11px] text-[#8b949e]">Waiting for a message...</div>
        )}
      </section>

      <section className="card-simple p-4 space-y-2">
        <StepHeading
          title="Send a test email"
          hint={
            relayConfigured === false
              ? "No outgoing mail provider is configured, so sending to an outside address will fail. Add one under Settings first."
              : "Sending to an outside address needs an outgoing mail provider under Settings. If none is set up, the result below says so."
          }
        />
        {localOnly && (
          <div className="text-[11px] text-[#d29922]">
            {domain} is a local-only name. Messages cannot be sent to it, and outsiders cannot send to it over the internet.
          </div>
        )}
        <div className="flex gap-2">
          <input
            value={recipient}
            onChange={(e) => setRecipient(e.target.value)}
            placeholder="you@yourdomain.com"
            aria-label="Test recipient"
            className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs text-[#e6edf3] font-mono"
          />
          <PrimaryButton onClick={sendTest} disabled={sending || !recipient.includes("@")}>
            {sending ? "Sending..." : "Send test message"}
          </PrimaryButton>
        </div>
        <ErrorNote message={sendError} />
        {sent && <div className="text-[11px] text-[#8b949e]">{describe(sent)}</div>}
      </section>

      <div className="flex flex-wrap gap-4 text-xs">
        <Link href="/inbox" className="text-[#58a6ff] hover:underline">Open the inbox</Link>
        <Link href="/domains" className="text-[#58a6ff] hover:underline">Domains</Link>
        <Link href="/settings" className="text-[#58a6ff] hover:underline">Mail provider settings</Link>
        <Link href="/api-keys" className="text-[#58a6ff] hover:underline">API keys</Link>
      </div>
    </div>
  );
}
