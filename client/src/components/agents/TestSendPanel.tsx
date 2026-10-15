"use client";

import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { api } from "@/lib/api";
import { agentsApi } from "@/lib/agents-api";

type Outcome = { tone: "ok" | "warn" | "error"; text: string };

const FINAL_STATUSES = new Set(["delivered", "accepted_by_relay", "failed", "blocked", "rejected", "delivery_unknown"]);

function describe(status: string, detail?: string | null): Outcome {
  switch (status) {
    case "delivered":
      return { tone: "ok", text: "Delivered to the inbox." };
    case "accepted_by_relay":
      return {
        tone: "ok",
        text: "Your mail provider accepted it. That does not confirm it reached the inbox: open the Inbox page in a minute and look for \"Your inbox test message\".",
      };
    case "pending_review":
      return { tone: "warn", text: "Held for approval. Approve it in Reviews and it will be sent." };
    case "failed":
      return { tone: "error", text: `Sending failed${detail ? `: ${detail}` : "."}` };
    case "blocked":
      return { tone: "error", text: `Blocked before sending${detail ? `: ${detail}` : "."}` };
    case "delivery_unknown":
      return { tone: "warn", text: "The provider did not clearly accept or reject it. Check the Outbox before sending again." };
    default:
      return { tone: "warn", text: "Queued. Waiting for the sender to pick it up…" };
  }
}

export function TestSendPanel({ email }: { email: string }) {
  const [relayReady, setRelayReady] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    api
      .getSmtpConfig()
      .then((cfg) => setRelayReady(Boolean(cfg.enabled && cfg.host)))
      .catch(() => setRelayReady(null));
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  useEffect(() => {
    setOutcome(null);
    if (timer.current) clearTimeout(timer.current);
  }, [email]);

  const poll = (messageId: string, attempt: number) => {
    timer.current = setTimeout(async () => {
      try {
        const state = await agentsApi.getMessage(email, messageId);
        const next = describe(state.status, state.delivery_error);
        setOutcome(next);
        if (!FINAL_STATUSES.has(state.status) && state.status !== "pending_review" && attempt < 8) {
          poll(messageId, attempt + 1);
        }
      } catch (err) {
        setOutcome({ tone: "error", text: `Could not read the message status: ${(err as Error).message}` });
      }
    }, 1500);
  };

  const send = async () => {
    try {
      setBusy(true);
      setOutcome(null);
      const result = await agentsApi.sendTest(email);
      if (result.status === "pending_review") {
        setOutcome(describe("pending_review"));
      } else {
        setOutcome(describe("queued"));
        poll(result.message_id, 0);
      }
    } catch (err) {
      setOutcome({ tone: "error", text: `Could not queue the test: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const tone = { ok: "text-[#3fb950]", warn: "text-[#d29922]", error: "text-[#f85149]" };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-medium text-[#e6edf3]">Send a test message</div>
          <p className="text-[11px] text-[#8b949e]">
            Sends a message from the platform to this inbox through your outgoing mail provider, to check the full route.
          </p>
        </div>
        <button
          onClick={send}
          disabled={busy || relayReady === false}
          className="px-3 py-1.5 rounded border border-[#30363d] text-[#e6edf3] hover:bg-[#21262d] disabled:opacity-50 text-xs flex items-center gap-1.5 shrink-0"
        >
          <Send className="w-3.5 h-3.5" />
          <span>{busy ? "Queuing..." : "Send test"}</span>
        </button>
      </div>
      {relayReady === false && (
        <p className="text-[11px] text-[#d29922]">
          No outgoing mail provider is configured, so a test cannot be sent. Set one up in Settings first.
        </p>
      )}
      {outcome && <p className={`text-[11px] ${tone[outcome.tone]}`}>{outcome.text}</p>}
    </div>
  );
}
