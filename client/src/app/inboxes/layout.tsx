import { Suspense } from "react";
import { InboxShell } from "@/components/inbox-views/InboxShell";

export default function InboxesLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<div className="card-simple p-6 text-xs text-[#8b949e]">Loading…</div>}>
      <InboxShell>{children}</InboxShell>
    </Suspense>
  );
}
