"use client";

import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked; the text stays selectable on the page.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1 px-2 py-1 rounded border border-[#30363d] bg-[#21262d] text-[10px] text-[#e6edf3] hover:bg-[#30363d]"
    >
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

export function CodeBlock({ label, code }: { label: string; code: string }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase text-[#8b949e]">{label}</span>
        <CopyButton text={code} />
      </div>
      <pre className="p-2 rounded bg-[#0d1117] border border-[#30363d] text-[11px] font-mono text-[#e6edf3] overflow-x-auto whitespace-pre">
        {code}
      </pre>
    </div>
  );
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium disabled:opacity-50"
    >
      {children}
    </button>
  );
}

export function SecondaryButton({ children, onClick, disabled }: { children: ReactNode; onClick?: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="px-3 py-1.5 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-[#e6edf3] text-xs disabled:opacity-50"
    >
      {children}
    </button>
  );
}

export function ChoiceCard({
  title,
  body,
  badge,
  onClick,
}: {
  title: string;
  body: string;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="card-simple p-4 text-left space-y-2 hover:border-[#58a6ff] transition w-full"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-[#e6edf3]">{title}</span>
        {badge && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#1f6feb]/20 text-[#58a6ff]">{badge}</span>}
      </div>
      <p className="text-xs text-[#8b949e]">{body}</p>
    </button>
  );
}

export function StepHeading({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="space-y-1">
      <h2 className="text-sm font-semibold text-[#e6edf3]">{title}</h2>
      {hint && <p className="text-xs text-[#8b949e]">{hint}</p>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null;
  return <div className="text-[11px] text-red-400 break-words">{message}</div>;
}
