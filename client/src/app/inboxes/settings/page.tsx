"use client";

import { useSearchParams } from "next/navigation";
import { SettingsView } from "@/components/inbox-views/SettingsView";

export default function Page() {
  const email = useSearchParams().get("email");
  if (!email) return null;
  return <SettingsView key={email} email={email} />;
}
