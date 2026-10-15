"use client";

import { ChoiceCard, StepHeading } from "./ui";

export type SetupMethod = "agent" | "web";

export function SetupMethodChoice({ onChoose }: { onChoose: (method: SetupMethod) => void }) {
  return (
    <div className="space-y-4">
      <StepHeading title="How will you use your inbox?" hint="This only changes what we show you next. You can do both later." />
      <div className="grid gap-3 md:grid-cols-2">
        <ChoiceCard
          title="From an AI agent"
          badge="Claude Code, Cursor, MCP"
          body="Create an inbox and a key, then connect your coding agent over MCP in one copy-paste step."
          onClick={() => onChoose("agent")}
        />
        <ChoiceCard
          title="From the web or the API"
          body="Choose an address on the shared domain or on your own domain, then use the dashboard, REST API or SDKs."
          onClick={() => onChoose("web")}
        />
      </div>
    </div>
  );
}
