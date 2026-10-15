"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { api } from "@/lib/api";
import { Message } from "@/lib/types";

type Props = {
  onCreated: () => void;
  onDone: () => void;
};

const API_URL = "http://127.0.0.1:8000";
const SMTP_PORT = 2525;

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked; the text stays selectable in the box.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex items-center gap-1 px-2 py-1 rounded border border-[#30363d] bg-[#21262d] text-[10px] text-[#e6edf3]"
    >
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function CodeBlock({ label, code }: { label: string; code: string }) {
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

export function FirstRunGuide({ onCreated, onDone }: Props) {
  const [email, setEmail] = useState("hello@agents.example.com");
  const [requireApproval, setRequireApproval] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [inbox, setInbox] = useState("");
  const [apiKey, setApiKey] = useState("");

  const [recipient, setRecipient] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Message | null>(null);
  const [sendError, setSendError] = useState("");

  const [received, setReceived] = useState<Message | null>(null);

  const createInbox = async () => {
    setCreating(true);
    setError("");
    try {
      const agent = await api.createAgent({ email: email.trim(), hitl_enabled: requireApproval });
      const key = await api.createApiKey("first-run", agent.email);
      setInbox(agent.email);
      setApiKey(key.key);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the inbox");
    } finally {
      setCreating(false);
    }
  };

  const sendTest = async () => {
    setSending(true);
    setSendError("");
    setSent(null);
    try {
      const message = await api.sendMessage(inbox, {
        recipient: recipient.trim(),
        subject: "Hello from Open Agent Email",
        body_text: "This is a test message from your new inbox.",
      });
      setSent(message);
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "Could not send the message");
    } finally {
      setSending(false);
    }
  };

  // Watch for inbound mail so a message delivered over SMTP shows up here.
  useEffect(() => {
    if (!inbox || received) return;
    const check = async () => {
      try {
        const inbound = await api.getMessages(inbox, { direction: "inbound", limit: 1 });
        if (inbound.length > 0) setReceived(inbound[0]);
      } catch {
        // Keep polling; a transient error is not worth surfacing here.
      }
    };
    check();
    const timer = setInterval(check, 3000);
    return () => clearInterval(timer);
  }, [inbox, received]);

  const keyForSnippets = apiKey || "oae_...";
  const curl = `curl -s ${API_URL}/v1/agents/${inbox}/messages \\\n  -H "Authorization: Bearer ${keyForSnippets}"`;
  const python = `from open_agent_email import AgentEmailClient\n\nclient = AgentEmailClient("${API_URL}", api_key="${keyForSnippets}")\nfor m in client.list_messages("${inbox}", direction="inbound"):\n    print(m["sender"], m["subject"])`;
  const typescript = `import { AgentEmailClient } from "@open-agent-email/sdk";\n\nconst client = new AgentEmailClient({ baseUrl: "${API_URL}", apiKey: "${keyForSnippets}" });\nfor await (const m of client.messages.iterate("${inbox}")) console.log(m.sender, m.subject);`;
  const smtp = `python3 - <<'PY'\nimport smtplib\nfrom email.message import EmailMessage\nm = EmailMessage()\nm["From"], m["To"], m["Subject"] = "person@sender.test", "${inbox}", "Hi from outside"\nm.set_content("Hello agent")\nwith smtplib.SMTP("127.0.0.1", ${SMTP_PORT}) as s:\n    s.send_message(m)\nPY`;

  return (
    <div className="card-simple p-4 space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-sm font-semibold text-[#e6edf3]">Welcome. Let&apos;s send and receive your first email.</div>
          <div className="text-xs text-[#8b949e]">Three short steps. You can skip this and use the sidebar at any time.</div>
        </div>
        <button type="button" onClick={onDone} className="text-xs text-[#8b949e] hover:text-[#e6edf3]">
          {inbox ? "Done" : "Skip"}
        </button>
      </div>

      <section className="space-y-2">
        <div className="text-xs font-semibold text-[#e6edf3]">1. Create an inbox</div>
        {!inbox ? (
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-label="Inbox email address"
                className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs text-[#e6edf3] font-mono"
              />
              <button
                type="button"
                onClick={createInbox}
                disabled={creating || !email.includes("@")}
                className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium disabled:opacity-50"
              >
                {creating ? "Creating..." : "Create inbox"}
              </button>
            </div>
            <label className="flex items-center gap-2 text-[11px] text-[#8b949e]">
              <input type="checkbox" checked={requireApproval} onChange={(e) => setRequireApproval(e.target.checked)} />
              Hold outgoing and suspicious mail for approval (recommended for real agents; leave off for this walkthrough)
            </label>
            {error && <div className="text-[11px] text-red-400 break-words">{error}</div>}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="text-xs text-[#8b949e]">
              Created <span className="font-mono text-[#e6edf3]">{inbox}</span>. This API key is shown once, so copy it now:
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-[11px] font-mono text-[#e6edf3] break-all">
                {apiKey}
              </code>
              <CopyButton text={apiKey} />
            </div>
            <CodeBlock label="curl" code={curl} />
            <CodeBlock label="Python" code={python} />
            <CodeBlock label="TypeScript" code={typescript} />
          </div>
        )}
      </section>

      {inbox && (
        <section className="space-y-2">
          <div className="text-xs font-semibold text-[#e6edf3]">2. Receive an email</div>
          <div className="text-xs text-[#8b949e]">
            Run this in a terminal on this machine to deliver a message to the inbox over SMTP. It will appear below.
          </div>
          <CodeBlock label="Deliver a test message" code={smtp} />
          {received ? (
            <div className="p-2 rounded border border-[#238636] text-xs text-[#e6edf3]">
              Received from <span className="font-mono">{received.sender}</span>: {received.subject}
            </div>
          ) : (
            <div className="text-[11px] text-[#8b949e]">Waiting for a message...</div>
          )}
        </section>
      )}

      {inbox && (
        <section className="space-y-2">
          <div className="text-xs font-semibold text-[#e6edf3]">3. Send an email</div>
          <div className="text-xs text-[#8b949e]">
            Sending to an outside address needs an upstream SMTP relay, which you can add under Settings. Without one the message is recorded as failed, and the result below says so.
          </div>
          <div className="flex gap-2">
            <input
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              placeholder="you@yourdomain.com"
              aria-label="Test recipient"
              className="flex-1 px-2 py-1.5 rounded bg-[#0d1117] border border-[#30363d] text-xs text-[#e6edf3] font-mono"
            />
            <button
              type="button"
              onClick={sendTest}
              disabled={sending || !recipient.includes("@")}
              className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium disabled:opacity-50"
            >
              {sending ? "Sending..." : "Send test message"}
            </button>
          </div>
          {sendError && <div className="text-[11px] text-red-400 break-words">{sendError}</div>}
          {sent && (
            <div className="text-[11px] text-[#8b949e]">
              Message accepted with status <span className="font-mono text-[#e6edf3]">{sent.status}</span>. Check the Outbox page for the final delivery result.
            </div>
          )}
        </section>
      )}
    </div>
  );
}
