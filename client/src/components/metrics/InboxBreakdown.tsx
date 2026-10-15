"use client";

import { Fragment, useState } from "react";
import type { AccountMetricsAgent } from "@/lib/types";
import { bounces, count, getInboxLiveMetrics, percent, type InboxLiveMetrics } from "@/lib/metrics-api";

type Sort = "received" | "delivered" | "messages";

export function InboxBreakdown({ agents, truncated }: { agents: AccountMetricsAgent[]; truncated: boolean }) {
  const [sort, setSort] = useState<Sort>("messages");
  const [open, setOpen] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, InboxLiveMetrics | string>>({});

  const rows = [...agents].sort((a, b) => {
    const pick = (row: AccountMetricsAgent) => (sort === "messages" ? row.messages_in_window : row.summary[sort]);
    return pick(b) - pick(a) || a.agent_email.localeCompare(b.agent_email);
  });

  const toggle = async (email: string) => {
    if (open === email) {
      setOpen(null);
      return;
    }
    setOpen(email);
    if (live[email] && typeof live[email] !== "string") return;
    try {
      const data = await getInboxLiveMetrics(email);
      setLive((current) => ({ ...current, [email]: data }));
    } catch (error) {
      setLive((current) => ({ ...current, [email]: error instanceof Error ? error.message : "Could not load this inbox" }));
    }
  };

  return (
    <section aria-labelledby="inbox-breakdown" className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="inbox-breakdown" className="text-sm font-semibold text-[#e6edf3]">By inbox</h2>
          <p className="text-[11px] text-[#8b949e]">Select a row for that inbox&apos;s current unread and review counts.</p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-[#8b949e]">
          Sort by
          <select
            value={sort}
            onChange={(event) => setSort(event.target.value as Sort)}
            className="rounded-md border border-[#30363d] bg-[#0d1117] px-2 py-1 text-[11px] text-[#e6edf3]"
          >
            <option value="messages">Total messages</option>
            <option value="received">Received</option>
            <option value="delivered">Delivered</option>
          </select>
        </label>
      </div>

      {rows.length === 0 ? (
        <p className="mt-4 text-center text-xs text-[#8b949e]">No inbox activity in this range.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-[#8b949e]">
              <tr>
                <th scope="col" className="py-1.5 pr-4 font-medium">Inbox</th>
                <th scope="col" className="py-1.5 pr-4 text-right font-medium">Messages</th>
                <th scope="col" className="py-1.5 pr-4 text-right font-medium">Received</th>
                <th scope="col" className="py-1.5 pr-4 text-right font-medium">Delivered</th>
                <th scope="col" className="py-1.5 pr-4 text-right font-medium">Delivered rate</th>
                <th scope="col" className="py-1.5 pr-4 text-right font-medium">Bounced</th>
                <th scope="col" className="py-1.5 text-right font-medium">Complaint rate</th>
              </tr>
            </thead>
            <tbody className="text-[#e6edf3]">
              {rows.map((row) => {
                const details = live[row.agent_email];
                const expanded = open === row.agent_email;
                return (
                  <Fragment key={row.agent_email}>
                    <tr className="border-t border-[#21262d]">
                      <th scope="row" className="py-1.5 pr-4 text-left font-normal">
                        <button
                          type="button"
                          aria-expanded={expanded}
                          onClick={() => toggle(row.agent_email)}
                          className="font-mono text-[#58a6ff] hover:underline"
                        >
                          {row.agent_email}
                        </button>
                      </th>
                      <td className="py-1.5 pr-4 text-right font-mono">{count(row.messages_in_window)}</td>
                      <td className="py-1.5 pr-4 text-right font-mono">{count(row.summary.received)}</td>
                      <td className="py-1.5 pr-4 text-right font-mono">{count(row.summary.delivered)}</td>
                      <td className="py-1.5 pr-4 text-right font-mono">{percent(row.rates.delivered_rate)}</td>
                      <td className="py-1.5 pr-4 text-right font-mono">{count(bounces(row.summary))}</td>
                      <td className="py-1.5 text-right font-mono">{percent(row.rates.complaint_rate)}</td>
                    </tr>
                    {expanded && (
                      <tr className="bg-[#0d1117]">
                        <td colSpan={7} className="px-3 py-2 text-[11px] text-[#c9d1d9]">
                          {details === undefined ? (
                            "Loading…"
                          ) : typeof details === "string" ? (
                            <span className="text-[#f85149]">{details}</span>
                          ) : (
                            <span>
                              Right now: <b className="font-mono">{details.unread}</b> unread,{" "}
                              <b className="font-mono">{details.pending_reviews}</b> awaiting review,{" "}
                              <b className="font-mono">{details.messages.inbound}</b> inbound and{" "}
                              <b className="font-mono">{details.messages.outbound}</b> outbound stored.
                            </span>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {truncated && (
        <p className="mt-2 text-[11px] text-[#d29922]">Showing the busiest inboxes only; the totals above include all of them.</p>
      )}
    </section>
  );
}
