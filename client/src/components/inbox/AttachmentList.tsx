"use client";

import { useState } from "react";
import { absoluteSignedLink, SignedAttachment } from "@/lib/inbox-api";

/** Attachments with a download link and a copyable short-lived signed link. */
export function AttachmentList({ attachments, agentEmail, messageId }: { attachments: SignedAttachment[]; agentEmail: string; messageId: string }) {
  const [copied, setCopied] = useState("");
  const [failed, setFailed] = useState("");

  const copy = async (attachment: SignedAttachment) => {
    if (!attachment.download_url) return;
    setFailed("");
    try {
      await navigator.clipboard.writeText(absoluteSignedLink(attachment.download_url));
      setCopied(attachment.id);
      window.setTimeout(() => setCopied((current) => (current === attachment.id ? "" : current)), 2000);
    } catch {
      setFailed(attachment.id);
    }
  };

  return (
    <div className="mt-3 border-t border-[#30363d] pt-2">
      <div className="mb-1.5 text-[10px] font-semibold text-[#8b949e]">Attachments ({attachments.length})</div>
      <div className="space-y-1">
        {attachments.map((attachment) => (
          <div key={attachment.id} className="flex items-center gap-2 rounded border border-[#30363d] px-2.5 py-1.5 text-[11px]">
            <a
              href={`/api/backend/v1/agents/${encodeURIComponent(agentEmail)}/messages/${messageId}/attachments/${attachment.id}`}
              className="min-w-0 flex-1 truncate text-[#58a6ff] hover:underline"
            >
              {attachment.filename}
            </a>
            <span className="shrink-0 text-[#8b949e]">{(attachment.size_bytes / 1024).toFixed(1)} KB</span>
            {attachment.download_url && (
              <button
                type="button"
                onClick={() => void copy(attachment)}
                title="Copies a link that works for 5 minutes for anyone signed in to this dashboard"
                className="shrink-0 rounded border border-[#30363d] px-1.5 py-0.5 text-[10px] text-[#e6edf3] hover:bg-[#21262d]"
              >
                {copied === attachment.id ? "Copied" : failed === attachment.id ? "Copy failed" : "Copy 5-minute link"}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
