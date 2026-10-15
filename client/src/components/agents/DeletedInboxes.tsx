"use client";

import { useCallback, useEffect, useState } from "react";
import { RotateCcw, Trash2 } from "lucide-react";
import { agentsApi, DeletedAgent } from "@/lib/agents-api";

export function DeletedInboxes({ onRestored }: { onRestored: () => void }) {
  const [items, setItems] = useState<DeletedAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<DeletedAgent | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setItems(await agentsApi.listDeleted());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const restore = async (email: string) => {
    try {
      setBusy(true);
      await agentsApi.restore(email);
      await load();
      onRestored();
    } catch (err) {
      setError(`Restore failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const purge = async () => {
    if (!purgeTarget || typed !== purgeTarget.email) return;
    try {
      setBusy(true);
      await agentsApi.purge(purgeTarget.email);
      setPurgeTarget(null);
      setTyped("");
      await load();
    } catch (err) {
      setError(`Permanent delete failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card-simple">
      <div className="p-3 border-b border-[#30363d]">
        <div className="text-xs font-medium text-[#e6edf3]">Deleted inboxes ({items.length})</div>
        <p className="text-[11px] text-[#8b949e]">
          A deleted inbox stops receiving mail but keeps its messages. Restore it to use it again, or delete it permanently.
        </p>
      </div>
      {error && <div className="px-3 pt-2 text-[11px] text-[#f85149]">{error}</div>}
      {loading ? (
        <div className="p-4 text-xs text-[#8b949e]">Loading…</div>
      ) : items.length === 0 ? (
        <div className="p-4 text-xs text-[#8b949e]">No deleted inboxes.</div>
      ) : (
        <div className="divide-y divide-[#30363d]">
          {items.map((ag) => (
            <div key={ag.id} className="p-3 flex items-center justify-between gap-3 text-xs">
              <div className="min-w-0">
                <div className="font-mono text-[11px] text-blue-400 truncate">{ag.email}</div>
                <div className="text-[11px] text-[#8b949e]">
                  {ag.display_name} · deleted {new Date(ag.deleted_at).toLocaleString()}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => restore(ag.email)}
                  disabled={busy}
                  className="px-2.5 py-1 rounded border border-[#30363d] text-[#e6edf3] hover:bg-[#21262d] disabled:opacity-50 flex items-center gap-1"
                >
                  <RotateCcw className="w-3 h-3" /> Restore
                </button>
                <button
                  onClick={() => {
                    setPurgeTarget(ag);
                    setTyped("");
                  }}
                  disabled={busy}
                  className="px-2.5 py-1 rounded border border-[#da3633]/30 text-[#f85149] hover:bg-[#da3633]/10 disabled:opacity-50 flex items-center gap-1"
                >
                  <Trash2 className="w-3 h-3" /> Delete forever
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {purgeTarget && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg w-full max-w-md p-5 space-y-3 shadow-xl text-xs">
            <h4 className="font-semibold text-[#e6edf3]">Delete {purgeTarget.email} permanently?</h4>
            <p className="text-[#8b949e]">
              This removes the inbox and all of its data and cannot be undone. Type the address to confirm.
            </p>
            <input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={purgeTarget.email}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] font-mono focus:outline-none focus:border-zinc-500"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setPurgeTarget(null)}
                className="px-3 py-1.5 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]"
              >
                Cancel
              </button>
              <button
                onClick={purge}
                disabled={busy || typed !== purgeTarget.email}
                className="px-3 py-1.5 rounded bg-[#da3633] hover:bg-[#f85149] disabled:opacity-40 text-white font-medium"
              >
                {busy ? "Deleting..." : "Delete forever"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
