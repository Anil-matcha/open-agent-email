import { Suspense } from "react";
import { redirect } from "next/navigation";

type Search = Promise<{ email?: string }>;

async function Redirect({ searchParams }: { searchParams: Search }): Promise<never> {
  const { email } = await searchParams;
  redirect(email ? `/inboxes/messages?email=${encodeURIComponent(email)}` : "/inboxes/messages");
}

// This screen now lives in the per-inbox view under /inboxes.
export default function Page({ searchParams }: { searchParams: Search }) {
  return (
    <Suspense fallback={null}>
      <Redirect searchParams={searchParams} />
    </Suspense>
  );
}
