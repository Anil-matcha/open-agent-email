/** Address the snippets point at. Replace it when the server is not on this machine. */
export const API_URL = "http://127.0.0.1:8000";
export const MCP_URL = `${API_URL}/mcp/rpc`;
export const SMTP_PORT = 2525;

export function claudeCodeSnippet(key: string): string {
  return `claude mcp add --transport http open-agent-email ${MCP_URL} \\\n  --header "Authorization: Bearer ${key}"`;
}

export function cursorSnippet(key: string): string {
  return JSON.stringify(
    { mcpServers: { "open-agent-email": { url: MCP_URL, headers: { Authorization: `Bearer ${key}` } } } },
    null,
    2,
  );
}

export function curlSnippet(inbox: string, key: string): string {
  return `curl -s ${API_URL}/v1/agents/${inbox}/messages \\\n  -H "Authorization: Bearer ${key}"`;
}

export function pythonSnippet(inbox: string, key: string): string {
  return `from open_agent_email import AgentEmailClient\n\nclient = AgentEmailClient("${API_URL}", api_key="${key}")\nfor m in client.list_messages("${inbox}", direction="inbound"):\n    print(m["sender"], m["subject"])`;
}

export function typescriptSnippet(inbox: string, key: string): string {
  return `import { AgentEmailClient } from "@open-agent-email/sdk";\n\nconst client = new AgentEmailClient({ baseUrl: "${API_URL}", apiKey: "${key}" });\nfor await (const m of client.messages.iterate("${inbox}")) console.log(m.sender, m.subject);`;
}

export function smtpSnippet(inbox: string): string {
  return `python3 - <<'PY'\nimport smtplib\nfrom email.message import EmailMessage\nm = EmailMessage()\nm["From"], m["To"], m["Subject"] = "person@sender.test", "${inbox}", "Hi from outside"\nm.set_content("Hello agent")\nwith smtplib.SMTP("127.0.0.1", ${SMTP_PORT}) as s:\n    s.send_message(m)\nPY`;
}

/** Local names accepted for a new inbox. Kept strict so a typo is caught before the server sees it. */
export function validLocalPart(value: string): boolean {
  return /^[a-z0-9][a-z0-9._+-]{0,63}$/i.test(value.trim());
}

/** Domains that cannot receive real internet mail or be sent to. */
export function isLocalOnlyDomain(domain: string): boolean {
  return /\.(local|localhost|test|invalid|example)$/i.test(domain) || domain === "localhost";
}
