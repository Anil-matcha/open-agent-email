"use client";

import { useEffect, useState } from "react";
import {
  Cpu,
  Copy,
  Check,
  Terminal,
  Bot,
  Code2
} from "lucide-react";
import { api } from "@/lib/api";

export default function McpPage() {
  const [copiedCursor, setCopiedCursor] = useState<boolean>(false);
  const [copiedClaude, setCopiedClaude] = useState<boolean>(false);
  const [mcpInfo, setMcpInfo] = useState<any>(null);

  useEffect(() => {
    const fetchMcp = async () => {
      try {
        const info = await api.getMcpInfo();
        setMcpInfo(info);
      } catch (err) {
        console.error(err);
      }
    };
    fetchMcp();
  }, []);

  const cursorJson = JSON.stringify(
    {
      mcpServers: {
        "open-agent-email": {
          url: "http://127.0.0.1:8000/mcp/rpc",
          headers: { Authorization: "Bearer PASTE_AGENT_API_KEY" },
        },
      },
    },
    null,
    2
  );

  const claudeCommand = 'claude mcp add --transport http open-agent-email http://127.0.0.1:8000/mcp/rpc --header "Authorization: Bearer PASTE_AGENT_API_KEY"';

  const copyToClipboard = (text: string, type: "cursor" | "claude") => {
    navigator.clipboard.writeText(text);
    if (type === "cursor") {
      setCopiedCursor(true);
      setTimeout(() => setCopiedCursor(false), 2000);
    } else {
      setCopiedClaude(true);
      setTimeout(() => setCopiedClaude(false), 2000);
    }
  };

  const tools = [
    {
      name: "list_agents",
      description: "Lists all configured AI agent email addresses and statuses.",
      sampleParams: "{}",
    },
    {
      name: "list_messages",
      description: "Fetches recent emails received or sent by an agent inbox.",
      sampleParams: '{"agent_email": "support@agents.local", "status": "delivered"}',
    },
    {
      name: "get_message",
      description: "Reads full email body, RFC headers, and SPF/DKIM verification tags.",
      sampleParams: '{"agent_email": "support@agents.local", "message_id": "msg_..."}',
    },
    {
      name: "send_message",
      description: "Dispatches a new outbound email from the agent's verified address.",
      sampleParams: '{"agent_email": "support@agents.local", "recipient": "user@example.com", "subject": "Hello", "body_text": "..."}',
    },
    {
      name: "reply_to_message",
      description: "Replies in-thread to an existing email, preserving RFC Message-ID and In-Reply-To.",
      sampleParams: '{"agent_email": "support@agents.local", "message_id": "msg_...", "body_text": "Got it!"}',
    },
    {
      name: "forward_to_human",
      description: "Hand an email to a human teammate with optional context; outbound approval rules still apply.",
      sampleParams: '{"agent_email": "support@agents.local", "message_id": "msg_...", "recipient": "teammate@example.com", "note": "Please review this request"}',
    },
    {
      name: "list_reviews",
      description: "Fetches emails held in the Human-in-the-Loop review queue.",
      sampleParams: '{"status": "pending"}',
    },
    {
      name: "approve_review",
      description: "Approves a held email and delivers it to the target agent.",
      sampleParams: '{"review_id": "rev_...", "notes": "Approved via MCP"}',
    },
    {
      name: "reject_review",
      description: "Quarantines a dangerous prompt injection email from delivery.",
      sampleParams: '{"review_id": "rev_...", "notes": "Prompt injection detected"}',
    },
  ];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="card-simple p-4 space-y-1">
        <h2 className="text-sm font-semibold text-[#e6edf3] flex items-center gap-2">
          <Cpu className="w-4 h-4 text-blue-400" />
          Model Context Protocol (MCP) Server
        </h2>
        <p className="text-xs text-[#8b949e]">
          Create an inbox key in API Keys, then add it as a Bearer token in your MCP client configuration.
        </p>
      </div>

      {/* Integration Snippets: Cursor & Claude Code */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Cursor Config */}
        <div className="card-simple p-4 space-y-2 flex flex-col justify-between">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-[#e6edf3] flex items-center gap-1.5">
                <Code2 className="w-3.5 h-3.5 text-blue-400" />
                Cursor IDE Config
              </span>
              <span className="text-[10px] font-mono text-[#8b949e]">.cursor/mcp.json</span>
            </div>
            <p className="text-xs text-[#8b949e]">
              Add this to your project&apos;s <code className="text-[#e6edf3]">.cursor/mcp.json</code>:
            </p>
            <pre className="p-3 rounded bg-[#0d1117] border border-[#30363d] font-mono text-xs text-[#e6edf3] overflow-x-auto">
              {cursorJson}
            </pre>
          </div>

          <button
            onClick={() => copyToClipboard(cursorJson, "cursor")}
            className="w-full mt-2 py-1.5 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-xs font-medium text-[#e6edf3] flex items-center justify-center gap-1.5 transition-colors"
          >
            {copiedCursor ? <Check className="w-3.5 h-3.5 text-[#2ea043]" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedCursor ? "Copied!" : "Copy JSON"}</span>
          </button>
        </div>

        {/* Claude Code CLI */}
        <div className="card-simple p-4 space-y-2 flex flex-col justify-between">
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-[#e6edf3] flex items-center gap-1.5">
                <Terminal className="w-3.5 h-3.5 text-cyan-400" />
                Claude Code CLI
              </span>
              <span className="text-[10px] font-mono text-[#8b949e]">Terminal command</span>
            </div>
            <p className="text-xs text-[#8b949e]">
              Run this in your terminal to register the tools:
            </p>
            <pre className="p-3 rounded bg-[#0d1117] border border-[#30363d] font-mono text-xs text-cyan-300 overflow-x-auto whitespace-pre-wrap">
              {claudeCommand}
            </pre>
          </div>

          <button
            onClick={() => copyToClipboard(claudeCommand, "claude")}
            className="w-full mt-2 py-1.5 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] text-xs font-medium text-[#e6edf3] flex items-center justify-center gap-1.5 transition-colors"
          >
            {copiedClaude ? <Check className="w-3.5 h-3.5 text-[#2ea043]" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedClaude ? "Copied!" : "Copy Command"}</span>
          </button>
        </div>
      </div>

      {/* Available MCP Tools Catalog */}
      <div className="card-simple p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold text-[#e6edf3] flex items-center gap-1.5">
            <Bot className="w-3.5 h-3.5 text-blue-400" />
            Registered Tools ({tools.length})
          </h3>
          <span className="text-[10px] font-mono px-2 py-0.5 rounded border border-[#2ea043]/30 bg-[#2ea043]/10 text-[#2ea043]">
            Port 8000
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
          {tools.map((tool) => (
            <div key={tool.name} className="p-3 rounded bg-[#0d1117] border border-[#30363d] space-y-1.5 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-mono font-medium text-blue-400">{tool.name}</span>
                <span className="text-[10px] text-[#8b949e] font-mono">Tool</span>
              </div>
              <p className="text-[#8b949e] leading-relaxed">{tool.description}</p>
              <div className="pt-1.5 border-t border-[#30363d]">
                <code className="text-[10px] font-mono text-[#8b949e] block truncate">
                  {tool.sampleParams}
                </code>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
