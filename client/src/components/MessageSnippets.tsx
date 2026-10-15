"use client";

import { useEffect, useState } from "react";

type MessageSnippet = {
  id: string;
  name: string;
  body: string;
};

type Props = {
  inboxEmail: string;
  value: string;
  onApply: (body: string) => void;
};

function storageKey(inboxEmail: string) {
  return `open-agent-email:message-snippets:${inboxEmail.toLowerCase()}`;
}

export function MessageSnippets({ inboxEmail, value, onApply }: Props) {
  const [snippets, setSnippets] = useState<MessageSnippet[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [storageAvailable, setStorageAvailable] = useState(true);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(storageKey(inboxEmail));
      const parsed: unknown = stored ? JSON.parse(stored) : [];
      if (Array.isArray(parsed)) {
        setSnippets(parsed.filter((item): item is MessageSnippet =>
          item !== null && typeof item === "object" &&
          typeof item.id === "string" && typeof item.name === "string" && typeof item.body === "string"
        ));
      } else {
        setSnippets([]);
      }
      setStorageAvailable(true);
    } catch {
      setSnippets([]);
      setStorageAvailable(false);
    }
    setSelectedId("");
  }, [inboxEmail]);

  const persist = (next: MessageSnippet[]) => {
    setSnippets(next);
    try {
      window.localStorage.setItem(storageKey(inboxEmail), JSON.stringify(next));
      setStorageAvailable(true);
    } catch {
      setStorageAvailable(false);
    }
  };

  const saveCurrent = () => {
    const body = value.trim();
    if (!body) return;
    const name = window.prompt("Name this response snippet:");
    if (!name?.trim()) return;
    const next = [...snippets, { id: crypto.randomUUID(), name: name.trim().slice(0, 80), body }];
    persist(next);
    setSelectedId(next[next.length - 1].id);
  };

  const selected = snippets.find((snippet) => snippet.id === selectedId);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor={`message-snippet-${inboxEmail}`}>Saved response snippets</label>
      <select
        id={`message-snippet-${inboxEmail}`}
        value={selectedId}
        onChange={(event) => setSelectedId(event.target.value)}
        className="max-w-[200px] bg-[#0d1117] border border-[#30363d] rounded px-2 py-1 text-[11px] text-[#e6edf3]"
      >
        <option value="">Insert saved response…</option>
        {snippets.map((snippet) => <option key={snippet.id} value={snippet.id}>{snippet.name}</option>)}
      </select>
      <button type="button" onClick={() => { if (selected) onApply(selected.body); }} disabled={!selected} className="text-[11px] text-blue-400 hover:text-blue-300 disabled:opacity-40">Insert</button>
      <button type="button" onClick={saveCurrent} disabled={!value.trim()} className="text-[11px] text-[#8b949e] hover:text-[#e6edf3] disabled:opacity-40">Save current text</button>
      {selected && <button type="button" onClick={() => { persist(snippets.filter((snippet) => snippet.id !== selected.id)); setSelectedId(""); }} className="text-[11px] text-red-400 hover:text-red-300">Delete</button>}
      {!storageAvailable && <span className="text-[10px] text-amber-400">Browser storage unavailable; snippets may not persist.</span>}
    </div>
  );
}
