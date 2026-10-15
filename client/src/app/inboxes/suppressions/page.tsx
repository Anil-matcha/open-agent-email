"use client";

import { useSearchParams } from "next/navigation";
import { SuppressionsView } from "@/components/inbox-views/SuppressionsView";

export default function Page() {
  const email = useSearchParams().get("email");
  if (!email) return null;
  return <SuppressionsView key={email} email={email} />;
}
