"use client";

import { useState } from "react";

const MAX_LABELS = 20;
const MAX_LENGTH = 64;

export function LabelEditor({
  labels,
  disabled,
  onAdd,
  onRemove,
}: {
  labels: string[];
  disabled?: boolean;
  onAdd: (label: string) => Promise<void>;
  onRemove: (label: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update labels.");
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const label = draft.trim();
    if (!label) return;
    if (label.length > MAX_LENGTH) return setError(`Labels can be at most ${MAX_LENGTH} characters.`);
    if (labels.includes(label)) return setDraft("");
    if (labels.length >= MAX_LABELS) return setError(`A message can have at most ${MAX_LABELS} labels.`);
    void run(async () => {
      await onAdd(label);
      setDraft("");
    });
  };

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5" aria-label="Labels">
        {labels.length === 0 && <span className="text-[10px] text-[#8b949e]">No labels</span>}
        {labels.map((label) => (
          <span key={label} className="inline-flex items-center gap-1 rounded-full border border-[#30363d] bg-[#21262d] px-2 py-0.5 text-[10px] text-[#e6edf3]">
            {label}
            <button
              type="button"
              disabled={disabled || busy}
              onClick={() => void run(() => onRemove(label))}
              aria-label={`Remove label ${label}`}
              className="text-[#8b949e] hover:text-[#f85149] disabled:opacity-50"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <form onSubmit={submit} className="flex items-center gap-2">
        <input
          type="text"
          value={draft}
          maxLength={MAX_LENGTH}
          disabled={disabled || busy}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Add label (for example needs-human)"
          aria-label="New label"
          className="w-56 rounded border border-[#30363d] bg-[#0d1117] p-1.5 text-[11px] text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-zinc-500 disabled:opacity-50"
        />
        <button type="submit" disabled={disabled || busy || !draft.trim()} className="rounded border border-[#30363d] bg-[#21262d] px-2 py-1 text-[11px] text-[#e6edf3] hover:bg-[#30363d] disabled:opacity-50">
          Add
        </button>
      </form>
      {error && <p role="alert" className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
