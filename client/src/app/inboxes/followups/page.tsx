"use client";

import { useSearchParams } from "next/navigation";
import { FollowUpsView } from "@/components/inbox-views/FollowUpsView";

export default function Page() {
  const email = useSearchParams().get("email");
  if (!email) return null;
  return <FollowUpsView key={email} email={email} />;
}
