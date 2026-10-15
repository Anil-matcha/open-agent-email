"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Circle, RotateCcw, Save, Lock } from "lucide-react";
import {
  Readiness,
  ReadinessCheck,
  ServerSettingItem,
  SettingSource,
  serverSettingsApi,
} from "@/lib/server-settings-api";

const SOURCE_LABEL: Record<SettingSource, string> = {
  ui: "Saved here",
  env: "From environment",
  default: "Default",
};

const SEVERITY_LABEL: Record<ReadinessCheck["severity"], string> = {
  required: "Required",
  recommended: "Recommended",
  optional: "Optional",
};

const inputClass =
  "w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]";

function display(value: ServerSettingItem["value"]): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.join(", ");
  return String(value);
}

function ReadinessChecklist({ readiness }: { readiness: Readiness }) {
  return (
    <section aria-label="Setup checklist" className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">Setup checklist</h2>
        <p className="text-[11px] text-[#8b949e] mt-0.5">
          {readiness.ready
            ? "Everything required is in place."
            : "Some required steps are still open. Fix them to send and receive mail."}
        </p>
      </div>
      <ul className="space-y-3">
        {readiness.items.map((check) => (
          <li key={check.id} className="flex items-start gap-3">
            {check.ok ? (
              <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-[#3fb950]" aria-label="Done" />
            ) : (
              <Circle
                className={`w-4 h-4 mt-0.5 shrink-0 ${check.severity === "required" ? "text-[#f85149]" : "text-[#d29922]"}`}
                aria-label="Not done"
              />
            )}
            <div className="min-w-0">
              <div className="text-xs text-[#e6edf3] font-medium">
                {check.label}
                <span className="ml-2 text-[10px] uppercase tracking-wider text-[#8b949e]">{SEVERITY_LABEL[check.severity]}</span>
              </div>
              <div className="text-[11px] text-[#8b949e] mt-0.5">{check.detail}</div>
              {!check.ok && check.fix && (
                <div className="text-[11px] text-[#d29922] mt-0.5">
                  {check.fix}{" "}
                  {check.link && (
                    <a href={check.link} className="text-[#58a6ff] hover:underline">
                      Open
                    </a>
                  )}
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ServerSettings({ focusKey }: { focusKey: string | null }) {
  const [items, setItems] = useState<ServerSettingItem[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const focused = useRef(false);

  const load = useCallback(async () => {
    const [settings, ready] = await Promise.all([serverSettingsApi.list(), serverSettingsApi.readiness()]);
    setItems(settings);
    setReadiness(ready);
    setDrafts(Object.fromEntries(settings.filter((s) => s.kind === "setting").map((s) => [s.key, display(s.value)])));
  }, []);

  useEffect(() => {
    // The loading flag starts true; state is only set once the request settles.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load()
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load server settings"))
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    if (loading || !focusKey || focused.current) return;
    const element = document.getElementById(`setting-${focusKey}`);
    if (element) {
      focused.current = true;
      element.scrollIntoView({ block: "center" });
      element.focus();
    }
  }, [loading, focusKey]);

  const editable = useMemo(() => items.filter((s) => s.kind === "setting"), [items]);
  const groups = useMemo(() => {
    const out = new Map<string, ServerSettingItem[]>();
    for (const item of editable) out.set(item.group, [...(out.get(item.group) || []), item]);
    return Array.from(out.entries());
  }, [editable]);
  const readOnly = items.filter((s) => s.kind === "readonly");
  const secrets = items.filter((s) => s.kind === "secret");

  const changed = editable.filter((s) => (drafts[s.key] ?? "") !== display(s.value));

  const parse = (item: ServerSettingItem, raw: string): string | number => {
    if (item.type !== "integer") return raw;
    const text = raw.trim();
    if (!/^-?\d+$/.test(text)) throw new Error(`${item.label} must be a whole number`);
    return Number(text);
  };

  const submit = async (values: Record<string, string | number | null>) => {
    try {
      setSaving(true);
      setError(null);
      setSaved(false);
      const updated = await serverSettingsApi.update(values);
      setItems(updated);
      setDrafts(Object.fromEntries(updated.filter((s) => s.kind === "setting").map((s) => [s.key, display(s.value)])));
      setReadiness(await serverSettingsApi.readiness());
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const save = (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const values: Record<string, string | number | null> = {};
      for (const item of changed) values[item.key] = parse(item, drafts[item.key] ?? "");
      if (Object.keys(values).length) void submit(values);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Check the values and try again");
    }
  };

  if (loading) return <div className="p-12 text-center text-xs text-[#8b949e]">Loading server settings...</div>;

  return (
    <div className="space-y-6">
      <div className="border-b border-[#30363d] pb-6">
        <h1 className="text-xl font-bold text-[#e6edf3] tracking-tight">Server</h1>
        <p className="text-xs text-[#8b949e] mt-1">
          Check that the server is ready and change settings that apply immediately. Values saved here take priority over the
          environment. Secrets and settings that need a restart are shown but cannot be changed here.
        </p>
      </div>

      {error && (
        <div role="alert" className="p-3 bg-[#f85149]/10 border border-[#f85149]/30 rounded-md text-xs text-[#f85149] flex items-start gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span className="break-words">{error}</span>
        </div>
      )}
      {saved && (
        <div role="status" className="p-3 bg-[#238636]/10 border border-[#238636]/30 rounded-md text-xs text-[#3fb950] flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4" />
          <span>Settings saved and applied.</span>
        </div>
      )}

      {readiness && <ReadinessChecklist readiness={readiness} />}

      <form onSubmit={save} className="space-y-6">
        {groups.map(([group, groupItems]) => (
          <section key={group} aria-label={group} className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-5">
            <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">{group}</h2>
            {groupItems.map((item) => (
              <div key={item.key}>
                <div className="flex items-center justify-between gap-3 mb-1.5">
                  <label htmlFor={`setting-${item.key}`} className="block text-xs font-medium text-[#e6edf3]">
                    {item.label}
                  </label>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-[#8b949e]">{SOURCE_LABEL[item.source || "default"]}</span>
                    {item.source === "ui" && (
                      <button
                        type="button"
                        disabled={saving}
                        onClick={() => void submit({ [item.key]: null })}
                        title="Remove the saved value and use the environment or default"
                        className="flex items-center gap-1 text-[10px] text-[#58a6ff] hover:underline disabled:opacity-50"
                      >
                        <RotateCcw className="w-3 h-3" />
                        Reset
                      </button>
                    )}
                  </div>
                </div>
                <input
                  id={`setting-${item.key}`}
                  type="text"
                  inputMode={item.type === "integer" ? "numeric" : undefined}
                  placeholder={item.example || undefined}
                  value={drafts[item.key] ?? ""}
                  onChange={(e) => setDrafts((current) => ({ ...current, [item.key]: e.target.value }))}
                  className={`${inputClass} ${focusKey === item.key ? "border-[#58a6ff]" : ""}`}
                />
                <p className="text-[11px] text-[#8b949e] mt-1">
                  {item.description}
                  {item.type === "integer" && item.minimum !== null && item.maximum !== null && (
                    <> Allowed: {item.minimum} to {item.maximum}.</>
                  )}
                </p>
              </div>
            ))}
          </section>
        ))}

        <div className="flex items-center justify-end gap-3">
          <span className="text-[11px] text-[#8b949e]">
            {changed.length ? `${changed.length} unsaved change${changed.length === 1 ? "" : "s"}` : "No changes"}
          </span>
          <button
            type="submit"
            disabled={saving || changed.length === 0}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold rounded-md transition disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{saving ? "Saving..." : "Save changes"}</span>
          </button>
        </div>
      </form>

      <section aria-label="Needs a restart" className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-3">
        <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">Needs a restart</h2>
        <p className="text-[11px] text-[#8b949e]">Read when the server starts. Change these in the environment, then restart.</p>
        <dl className="divide-y divide-[#30363d]">
          {readOnly.map((item) => (
            <div key={item.key} className="py-2 flex items-start justify-between gap-4">
              <dt className="text-xs text-[#e6edf3]">
                {item.label}
                <span className="block text-[10px] text-[#8b949e] font-mono">{item.key}</span>
              </dt>
              <dd className="text-xs text-[#8b949e] font-mono break-all text-right">{display(item.value) || "(empty)"}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-label="Secrets" className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-3">
        <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider flex items-center gap-2">
          <Lock className="w-3.5 h-3.5" />
          Secrets
        </h2>
        <p className="text-[11px] text-[#8b949e]">Set in the environment. The values are never shown.</p>
        <dl className="divide-y divide-[#30363d]">
          {secrets.map((item) => (
            <div key={item.key} className="py-2 flex items-center justify-between gap-4">
              <dt className="text-xs text-[#e6edf3]">
                {item.label}
                <span className="block text-[10px] text-[#8b949e] font-mono">{item.key}</span>
              </dt>
              <dd className={`text-xs font-medium ${item.configured ? "text-[#3fb950]" : "text-[#d29922]"}`}>
                {item.configured ? "Configured" : "Not set"}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}
