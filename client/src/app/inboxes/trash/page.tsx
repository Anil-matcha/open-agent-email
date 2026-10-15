"use client";

import { useSearchParams } from "next/navigation";
import { MessagesView } from "@/components/inbox-views/MessagesView";

export default function Page() {
  const email = useSearchParams().get("email");
  if (!email) return null;
  return <MessagesView key={email} email={email} mode="trash" />;
}
