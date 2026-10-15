"use client";

import { useEffect, useState } from "react";
import { Lock, Save } from "lucide-react";
import { Dropdown } from "@/components/Dropdown";
import { agentsApi, ExpiryAction, Sensitivity } from "@/lib/agents-api";

const sensitivityOptions = [
  { value: "off", label: "Off (Permissive)", sublabel: "No scanning" },
  { value: "low", label: "Low Sensitivity", sublabel: "Only obvious attacks" },
  { value: "medium", label: "Medium (Standard)", sublabel: "Default threshold" },
  { value: "high", label: "High Sensitivity", sublabel: "Strict review queue" },
];

const expiryOptions = [
  { value: "reject", label: "Reject", sublabel: "Safe default: nothing is sent" },
  { value: "approve", label: "Approve", sublabel: "Sends the message if nobody decides in time" },
];

export function ProtectionPanel({ email, onSaved }: { email: string; onSaved: () => void }) {
  const [sensitivity, setSensitivity] = useState<Sensitivity>("medium");
  const [hitl, setHitl] = useState(true);
  const [notifyEmail, setNotifyEmail] = useState("");
  const [expiryHours, setExpiryHours] = useState("");
  const [expiryAction, setExpiryAction] = useState<ExpiryAction>("reject");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMessage(null);
    Promise.all([agentsApi.getProtection(email), agentsApi.getReviewSettings(email)])
      .then(([protection, review]) => {
        if (cancelled) return;
        setSensitivity(protection.threat_sensitivity);
        setHitl(protection.hitl_enabled);
        setNotifyEmail(review.review_notify_email ?? "");
        setExpiryHours(review.review_expiry_hours ? String(review.review_expiry_hours) : "");
        setExpiryAction(review.review_expiry_action);
      })
      .catch((err: Error) => !cancelled && setMessage({ ok: false, text: `Could not load settings: ${err.message}` }))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [email]);

  const save = async () => {
    const hours = expiryHours.trim() === "" ? null : Number(expiryHours);
    if (hours !== null && (!Number.isInteger(hours) || hours < 1 || hours > 720)) {
      setMessage({ ok: false, text: "Expiry must be a whole number of hours between 1 and 720, or empty for no expiry." });
      return;
    }
    try {
      setSaving(true);
      setMessage(null);
      await agentsApi.updateProtection(email, {
        threat_sensitivity: sensitivity,
        hitl_enabled: hitl,
        review_notify_email: notifyEmail.trim() === "" ? null : notifyEmail.trim(),
        review_expiry_hours: hours,
        review_expiry_action: expiryAction,
      });
      setMessage({ ok: true, text: "Protection settings saved." });
      onSaved();
    } catch (err) {
      setMessage({ ok: false, text: `Save failed: ${(err as Error).message}` });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="text-xs text-[#8b949e]">Loading protection settings…</div>;

  return (
    <div className="space-y-3">
      <Dropdown
        label="Threat Scan Sensitivity"
        options={sensitivityOptions}
        value={sensitivity}
        onChange={(v) => setSensitivity(v as Sensitivity)}
      />

      <div className="p-3 rounded bg-[#0d1117] border border-[#30363d] flex items-center justify-between">
        <div>
          <div className="text-xs font-medium text-[#e6edf3] flex items-center gap-1.5">
            <Lock className="w-3.5 h-3.5 text-[#8b949e]" />
            Human approval gate
          </div>
          <p className="text-[11px] text-[#8b949e]">
            Holds suspicious inbound mail and outgoing messages for a person to approve.
          </p>
        </div>
        <input
          type="checkbox"
          checked={hitl}
          onChange={(e) => setHitl(e.target.checked)}
          className="w-4 h-4 accent-[#238636] cursor-pointer"
        />
      </div>

      <div className="p-3 rounded bg-[#0d1117] border border-[#30363d] space-y-3">
        <div className="text-xs font-medium text-[#e6edf3]">Review notifications and expiry</div>
        <div>
          <label className="text-[11px] text-[#8b949e] block mb-1">Email the reviewer at</label>
          <input
            type="email"
            placeholder="reviewer@yourcompany.com (empty for no email)"
            value={notifyEmail}
            onChange={(e) => setNotifyEmail(e.target.value)}
            className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500"
          />
          <p className="text-[11px] text-[#8b949e] mt-1">
            The reviewer gets approve and reject links. Sending that email needs a working outgoing mail setup.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-[#8b949e] block mb-1">Decide within (hours)</label>
            <input
              type="number"
              min={1}
              max={720}
              placeholder="No expiry"
              value={expiryHours}
              onChange={(e) => setExpiryHours(e.target.value)}
              className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500"
            />
          </div>
          <Dropdown
            label="If nobody decides"
            options={expiryOptions}
            value={expiryAction}
            onChange={(v) => setExpiryAction(v as ExpiryAction)}
          />
        </div>
        {expiryHours.trim() !== "" && expiryAction === "approve" && (
          <p className="text-[11px] text-[#d29922]">
            Held messages will be approved and sent automatically after {expiryHours} hours without a decision.
          </p>
        )}
      </div>

      <div className="flex items-center justify-between gap-3">
        <span className={`text-[11px] ${message?.ok ? "text-[#3fb950]" : "text-[#f85149]"}`}>{message?.text}</span>
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
        >
          <Save className="w-3.5 h-3.5" />
          <span>{saving ? "Saving..." : "Save protection"}</span>
        </button>
      </div>
    </div>
  );
}
