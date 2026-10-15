const API_BASE = "/api/backend";

export type SettingKind = "setting" | "readonly" | "secret";
export type SettingSource = "ui" | "env" | "default";

export interface ServerSettingItem {
  key: string;
  group: string;
  label: string;
  description: string;
  kind: SettingKind;
  type: "string" | "integer" | "list" | "secret";
  editable: boolean;
  requires_restart: boolean;
  value?: string | number | string[] | null;
  default?: string | number | string[] | null;
  source?: SettingSource;
  minimum?: number | null;
  maximum?: number | null;
  example?: string;
  configured?: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface ReadinessCheck {
  id: string;
  label: string;
  ok: boolean;
  severity: "required" | "recommended" | "optional";
  detail: string;
  fix: string | null;
  link: string | null;
}

export interface Readiness {
  items: ReadinessCheck[];
  ready: boolean;
}

// The server answers errors as JSON with a readable `detail`; fall back to the raw text.
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const body = JSON.parse(text);
      if (typeof body?.detail === "string") message = body.detail;
      else if (typeof body?.error?.message === "string") message = body.error.message;
    } catch {
      // keep the raw text
    }
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return response.json();
}

export const serverSettingsApi = {
  list: () => call<{ items: ServerSettingItem[] }>("/v1/settings").then((r) => r.items),
  update: (values: Record<string, string | number | null>) =>
    call<{ items: ServerSettingItem[] }>("/v1/settings", { method: "PUT", body: JSON.stringify({ values }) }).then((r) => r.items),
  readiness: () => call<Readiness>("/v1/settings/readiness"),
};
