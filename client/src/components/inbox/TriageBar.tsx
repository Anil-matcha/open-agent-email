"use client";

import { useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";

/** Trash, restore and permanent delete (typed confirmation) for one message. */
export function TriageBar({
  trashed,
  scheduled,
  onTrash,
  onRestore,
  onDeleteForever,
}: {
  trashed: boolean;
  scheduled: boolean;
  onTrash: () => Promise<void>;
  onRestore: () => Promise<void>;
  onDeleteForever: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setConfirming(false);
      setTyped("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const button = "px-2.5 py-1 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] disabled:opacity-50 text-[#e6edf3] text-[11px] flex items-center gap-1.5";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {trashed ? (
          <>
            <button type="button" disabled={busy} onClick={() => void run(onRestore)} className={button}>
              <RotateCcw className="w-3 h-3" /> Restore
            </button>
            <button type="button" disabled={busy} onClick={() => setConfirming(true)} className={`${button} !text-[#f85149]`}>
              <Trash2 className="w-3 h-3" /> Delete forever
            </button>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={() => void run(onTrash)} className={button}>
            <Trash2 className="w-3 h-3" /> {scheduled ? "Cancel scheduled send (move to trash)" : "Move to trash"}
          </button>
        )}
      </div>
      {confirming && (
        <form
          onSubmit={(event) => { event.preventDefault(); if (typed === "DELETE") void run(onDeleteForever); }}
          className="rounded border border-[#f85149]/30 bg-[#f85149]/10 p-2 space-y-1.5"
        >
          <p className="text-[11px] text-[#f85149]">
            This removes the message, its attachments and its history for good. Type DELETE to confirm.
          </p>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              aria-label="Type DELETE to confirm"
              autoFocus
              className="w-28 rounded border border-[#30363d] bg-[#0d1117] p-1.5 text-xs text-[#e6edf3] font-mono"
            />
            <button type="submit" disabled={busy || typed !== "DELETE"} className="rounded bg-[#da3633] px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-50">
              Delete forever
            </button>
            <button type="button" onClick={() => { setConfirming(false); setTyped(""); }} className="text-[11px] text-[#8b949e] hover:text-[#e6edf3]">
              Cancel
            </button>
          </div>
        </form>
      )}
      {error && <p role="alert" className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
}
