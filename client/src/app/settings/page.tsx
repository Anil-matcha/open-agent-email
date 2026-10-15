"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SmtpConfig } from "@/lib/types";
import { Dropdown } from "@/components/Dropdown";
import { Sliders, Send, Save, CheckCircle2, AlertCircle, ShieldCheck } from "lucide-react";

const PORT_OPTIONS = [
  { value: "587", label: "Port 587 (Standard Submission / STARTTLS)" },
  { value: "465", label: "Port 465 (SMTPS / SSL Direct)" },
  { value: "25", label: "Port 25 (Standard Internet Relay)" },
  { value: "2525", label: "Port 2525 (Alternative SMTP / Cloud Dev)" },
];

const SMTP_PRESETS: Record<string, { label: string; host: string; port: string; username: string; use_tls: boolean; use_ssl: boolean }> = {
  gmail: { label: "Gmail", host: "smtp.gmail.com", port: "587", username: "", use_tls: true, use_ssl: false },
  ses: { label: "Amazon SES", host: "email-smtp.us-east-1.amazonaws.com", port: "587", username: "", use_tls: true, use_ssl: false },
  resend: { label: "Resend", host: "smtp.resend.com", port: "465", username: "resend", use_tls: false, use_ssl: true },
  sendgrid: { label: "SendGrid", host: "smtp.sendgrid.net", port: "587", username: "apikey", use_tls: true, use_ssl: false },
  postmark: { label: "Postmark", host: "smtp.postmarkapp.com", port: "587", username: "", use_tls: true, use_ssl: false },
  custom: { label: "Custom SMTP", host: "", port: "587", username: "", use_tls: true, use_ssl: false },
};

export default function SettingsPage() {
  const [config, setConfig] = useState<Partial<SmtpConfig>>({
    host: "",
    port: "587",
    username: "",
    password_configured: false,
    password_encryption_enabled: false,
    use_tls: true,
    use_ssl: false,
    from_email: "",
    enabled: false,
  });
  const [password, setPassword] = useState<string>("");
  const [provider, setProvider] = useState<string>("custom");
  const [loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);

  // Live Test states
  const [testRecipient, setTestRecipient] = useState<string>("");
  const [testing, setTesting] = useState<boolean>(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        setLoading(true);
        const data = await api.getSmtpConfig();
        setConfig(data);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load SMTP settings");
      } finally {
        setLoading(false);
      }
    };
    fetchConfig();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setSaving(true);
      setError(null);
      setSaveSuccess(false);

      const payload: Record<string, unknown> = {
        host: config.host,
        port: config.port,
        username: config.username,
        use_tls: config.use_tls,
        use_ssl: config.use_ssl,
        from_email: config.from_email,
        enabled: config.enabled,
      };
      if (password) {
        payload.password = password;
      }

      const updated = await api.updateSmtpConfig(payload as Partial<SmtpConfig>);
      setConfig(updated);
      setPassword("");
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save configuration");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!testRecipient.trim()) return;
    try {
      setTesting(true);
      setTestResult(null);
      const res = await api.testSmtpConnection(testRecipient.trim());
      setTestResult(res);
    } catch (err: unknown) {
      setTestResult({
        success: false,
        message: err instanceof Error ? err.message : "Failed to test connection",
      });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="p-12 text-center text-xs text-[#8b949e]">
        Loading settings...
      </div>
    );
  }

  return (
    <div className="p-8 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="border-b border-[#30363d] pb-6">
        <h1 className="text-xl font-bold text-[#e6edf3] tracking-tight">Upstream SMTP Relay</h1>
        <p className="text-xs text-[#8b949e] mt-1">
          Configure an upstream email service (Gmail SMTP with App Password, AWS SES, Resend, SendGrid, Postmark, or custom SMTP server) to deliver agent emails to real external inboxes.
        </p>
      </div>

      {error && (
        <div className="p-3 bg-[#f85149]/10 border border-[#f85149]/30 rounded-md text-xs text-[#f85149]">
          {error}
        </div>
      )}

      {saveSuccess && (
        <div className="p-3 bg-[#238636]/10 border border-[#238636]/30 rounded-md text-xs text-[#3fb950] flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4" />
          <span>SMTP settings saved successfully.</span>
        </div>
      )}

      {/* Main Form */}
      <form onSubmit={handleSave} className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-5">
        <div>
          <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">Choose your SMTP provider</label>
          <select value={provider} onChange={(e) => {
            const preset = SMTP_PRESETS[e.target.value];
            setProvider(e.target.value);
            setConfig((current) => ({ ...current, host: preset.host, port: preset.port, username: preset.username, use_tls: preset.use_tls, use_ssl: preset.use_ssl }));
          }} className="w-full md:w-1/2 bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3]">
            {Object.entries(SMTP_PRESETS).map(([key, preset]) => <option key={key} value={key}>{preset.label}</option>)}
          </select>
          <p className="text-[11px] text-[#8b949e] mt-1">Presets fill in SMTP connection details. Enter credentials from your provider; Amazon SES requires replacing the host with your region’s SMTP endpoint.</p>
          <p role="status" className={`mt-2 text-[11px] ${config.password_encryption_enabled ? "text-[#3fb950]" : "text-[#d29922]"}`}>
            {config.password_encryption_enabled
              ? "SMTP credentials are encrypted at rest. Keep the encryption key backed up with your database."
              : "SMTP credential encryption is off. Set SMTP_CONFIG_ENCRYPTION_KEY on the server before saving a relay password."}
          </p>
        </div>
        <div className="flex items-center justify-between pb-4 border-b border-[#30363d]">
          <div>
            <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider">
              Relay Credentials & Routing
            </h2>
            <p className="text-[11px] text-[#8b949e] mt-0.5">
              Outbound emails dispatched by agents will be submitted through this relay.
            </p>
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <span className="text-xs text-[#e6edf3] font-medium">Enable Outbound Relay</span>
            <input
              type="checkbox"
              checked={Boolean(config.enabled)}
              onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
              className="w-4 h-4 rounded bg-[#0d1117] border-[#30363d] text-[#238636] focus:ring-0"
            />
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">
              SMTP Host
            </label>
            <input
              type="text"
              placeholder="e.g. smtp.gmail.com or email-smtp.us-east-1.amazonaws.com"
              value={config.host || ""}
              onChange={(e) => setConfig({ ...config, host: e.target.value })}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">
              SMTP Port
            </label>
            <Dropdown
              value={config.port || "587"}
              onChange={(val) => setConfig({ ...config, port: val })}
              options={PORT_OPTIONS}
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">
              Username / API Key
            </label>
            <input
              type="text"
              placeholder="e.g. your_account@gmail.com or api_key"
              value={config.username || ""}
              onChange={(e) => setConfig({ ...config, username: e.target.value })}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">
              Password / Secret
            </label>
            <input
              type="password"
              placeholder={config.password_configured ? "•••••••••••• (Leave blank to keep existing)" : "App Password or SMTP token"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[#e6edf3] mb-1.5">
              Default Envelope From Address
            </label>
            <input
              type="email"
              placeholder="e.g. assistant@yourdomain.com"
              value={config.from_email || ""}
              onChange={(e) => setConfig({ ...config, from_email: e.target.value })}
              className="w-full bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
            />
          </div>

          <div className="flex items-end pb-1 gap-4">
            <label className="flex items-center gap-2 cursor-pointer text-xs text-[#e6edf3]">
              <input
                type="checkbox"
                checked={Boolean(config.use_tls)}
                onChange={(e) => setConfig({ ...config, use_tls: e.target.checked })}
                className="w-4 h-4 rounded bg-[#0d1117] border-[#30363d] text-[#238636]"
              />
              <span>Use STARTTLS</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer text-xs text-[#e6edf3]">
              <input
                type="checkbox"
                checked={Boolean(config.use_ssl)}
                onChange={(e) => setConfig({ ...config, use_ssl: e.target.checked })}
                className="w-4 h-4 rounded bg-[#0d1117] border-[#30363d] text-[#238636]"
              />
              <span>Use Direct SSL</span>
            </label>
          </div>
        </div>

        <div className="pt-4 border-t border-[#30363d] flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-semibold rounded-md transition disabled:opacity-50"
          >
            <Save className="w-3.5 h-3.5" />
            <span>{saving ? "Saving..." : "Save Configuration"}</span>
          </button>
        </div>
      </form>

      {/* Test Live Relay Section */}
      <div className="bg-[#161b22] border border-[#30363d] rounded-lg p-6 space-y-4">
        <div>
          <h2 className="text-xs font-semibold text-[#e6edf3] uppercase tracking-wider flex items-center gap-2">
            <Send className="w-3.5 h-3.5 text-[#58a6ff]" />
            <span>Send Live Test Email</span>
          </h2>
          <p className="text-[11px] text-[#8b949e] mt-1">
            Dispatch a real verification email through your configured SMTP relay to prove end-to-end internet delivery.
          </p>
        </div>

        <div className="flex gap-3">
          <input
            type="email"
            value={testRecipient}
            onChange={(e) => setTestRecipient(e.target.value)}
            placeholder="recipient@example.com"
            className="flex-1 bg-[#0d1117] border border-[#30363d] rounded-md px-3 py-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-[#58a6ff]"
          />
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !testRecipient.trim() || !config.host}
            className="flex items-center gap-1.5 px-4 py-2 bg-[#21262d] hover:bg-[#30363d] text-[#e6edf3] text-xs font-semibold rounded-md border border-[#30363d] transition disabled:opacity-50"
          >
            <Send className={`w-3.5 h-3.5 ${testing ? "animate-pulse" : ""}`} />
            <span>{testing ? "Sending..." : "Send Test Email"}</span>
          </button>
        </div>

        {testResult && (
          <div
            className={`p-3 rounded-md text-xs border flex items-start gap-2.5 ${
              testResult.success
                ? "bg-[#238636]/10 border-[#238636]/30 text-[#3fb950]"
                : "bg-[#f85149]/10 border-[#f85149]/30 text-[#f85149]"
            }`}
          >
            {testResult.success ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            )}
            <div>
              <div className="font-semibold">{testResult.success ? "Test Succeeded" : "Test Failed"}</div>
              <div className="text-[11px] mt-0.5 break-all">{testResult.message}</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
