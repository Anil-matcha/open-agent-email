"use client";

import { useEffect, useState } from "react";
import { describeError, getMessageLifecycle, LifecycleEntry } from "@/lib/inbox-api";

const LABELS: Record<string, string> = {
  created: "Recorded",
  queued: "Queued for sending",
  scheduled: "Scheduled",
  review_requested: "Held for review",
  review_approved: "Review approved",
  review_rejected: "Review rejected",
  sending: "Sending",
  accepted_by_relay: "Accepted by relay",
  delivered: "Delivered",
  delivery_unknown: "Delivery unknown",
  blocked: "Blocked",
  failed: "Failed",
  canceled: "Canceled",
  trashed: "Moved to trash",
  restored: "Restored",
  operator_retry: "Retried by operator",
  provider_delivered: "Provider: delivered",
  provider_delayed: "Provider: delayed",
  provider_bounced: "Provider: bounced",
  provider_complained: "Provider: complaint",
  provider_rejected: "Provider: rejected",
};

function label(code: string): string {
  return LABELS[code] || code.replace(/_/g, " ");
}

export function LifecycleTimeline({ agentEmail, messageId }: { agentEmail: string; messageId: string }) {
  const [entries, setEntries] = useState<LifecycleEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setEntries(null);
    setError("");
    getMessageLifecycle(agentEmail, messageId)
      .then((data) => { if (active) setEntries(data.entries); })
      .catch((err) => { if (active) setError(describeError(err)); });
    return () => { active = false; };
  }, [agentEmail, messageId]);

  if (error) return <p role="alert" className="text-[11px] text-red-400">{error}</p>;
  if (!entries) return <p className="text-[11px] text-[#8b949e]">Loading history…</p>;
  if (entries.length === 0) return <p className="text-[11px] text-[#8b949e]">No history recorded yet.</p>;

  return (
    <ol className="space-y-1.5 border-l border-[#30363d] pl-3" aria-label="Message history">
      {entries.map((entry, index) => (
        <li key={`${entry.code}-${index}`} className="text-[11px]">
          <span className="font-medium text-[#e6edf3]">{label(entry.code)}</span>
          <span className="ml-2 text-[#8b949e]">{entry.at ? new Date(entry.at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(entry.at) ? entry.at : `${entry.at}Z`).toLocaleString() : "current state"}</span>
          {entry.detail && <div className="text-[#8b949e] break-words">{entry.detail}</div>}
        </li>
      ))}
    </ol>
  );
}
