"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, ExternalLink, Plus, RefreshCw, RotateCcw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Agent } from "@/lib/types";
import { WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint, WebhookEventType } from "@/lib/types";

const inputClass = "w-full rounded border border-[#30363d] bg-[#0d1117] px-2.5 py-2 text-xs text-[#e6edf3] placeholder:text-[#6e7681] focus:border-blue-400 focus:outline-none";
const buttonClass = "inline-flex items-center gap-1.5 rounded border border-[#30363d] px-2.5 py-1.5 text-xs text-[#c9d1d9] hover:bg-[#21262d] disabled:opacity-50";
const statuses: (WebhookDeliveryStatus | "")[] = ["", "pending", "processing", "delivered", "failed"];
const webhookEventTypes: { value: WebhookEventType; label: string }[] = [
  { value: "email.received", label: "Email received" },
  { value: "email.review_requested", label: "Review requested" },
  { value: "email.review_approved", label: "Review approved" },
  { value: "email.review_rejected", label: "Review rejected" },
  { value: "email.flagged", label: "Email flagged" },
  { value: "email.blocked", label: "Email blocked" },
  { value: "email.accepted_by_relay", label: "Accepted by relay" },
  { value: "email.failed", label: "Email failed" },
  { value: "email.delivery_unknown", label: "Delivery unknown" },
  { value: "email.delivered", label: "Recipient delivery confirmed" },
  { value: "email.bounced", label: "Recipient address bounced" },
  { value: "email.complained", label: "Recipient complaint" },
  { value: "email.delivery_delayed", label: "Recipient delivery delayed" },
  { value: "contact.due", label: "Contact follow-up due" },
];
const deliveryEventTypes: { value: string; label: string }[] = [
  ...webhookEventTypes,
  { value: "webhook.test", label: "Test event" },
];

function endpointEventTypes(endpoint: WebhookEndpoint) {
  return endpoint.events ?? ["*"];
}

function eventTypesLabel(eventTypes: string[]) {
  if (eventTypes.includes("*")) return "All events";
  if (!eventTypes.length) return "No events";
  return eventTypes.map((value) => webhookEventTypes.find((item) => item.value === value)?.label ?? value).join(", ");
}

function EventTypeSelector({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const allEvents = value.includes("*");
  const toggleAll = (checked: boolean) => onChange(checked ? ["*"] : []);
  const toggleEvent = (eventType: WebhookEventType, checked: boolean) => {
    const selected = allEvents ? [] : [...value];
    const next = checked ? [...selected, eventType] : selected.filter((item) => item !== eventType);
    onChange(next);
  };
  return <fieldset className="space-y-2 rounded border border-[#30363d] p-3">
    <legend className="px-1 text-xs text-[#8b949e]">Event subscriptions</legend>
    <label className="flex items-center gap-2 text-xs text-[#c9d1d9]"><input type="checkbox" checked={allEvents} onChange={(event) => toggleAll(event.target.checked)} />All events</label>
    <div className="grid gap-2 sm:grid-cols-2">
      {webhookEventTypes.map((eventType) => <label key={eventType.value} className="flex items-center gap-2 text-xs text-[#8b949e]"><input type="checkbox" checked={!allEvents && value.includes(eventType.value)} onChange={(event) => toggleEvent(eventType.value, event.target.checked)} />{eventType.label}<span className="font-mono text-[10px] text-[#6e7681]">{eventType.value}</span></label>)}
    </div>
    {!allEvents && !value.length && <p className="text-[11px] text-[#f85149]">Select at least one event.</p>}
    <p className="text-[10px] text-[#6e7681]">Signed test events are always sent, even when event subscriptions are filtered.</p>
  </fieldset>;
}

function date(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function statusStyle(status: WebhookDeliveryStatus) {
  if (status === "delivered") return "text-[#3fb950] border-[#3fb950]/40 bg-[#3fb950]/10";
  if (status === "failed") return "text-[#f85149] border-[#f85149]/40 bg-[#f85149]/10";
  return "text-[#d29922] border-[#d29922]/40 bg-[#d29922]/10";
}

export default function WebhooksPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [deliveryLoading, setDeliveryLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const deliveryRequestId = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [endpointFilter, setEndpointFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<WebhookDeliveryStatus | "">("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createAgent, setCreateAgent] = useState("");
  const [createUrl, setCreateUrl] = useState("");
  const [createEventTypes, setCreateEventTypes] = useState<string[]>(["*"]);
  const [creating, setCreating] = useState(false);
  const [secretNotice, setSecretNotice] = useState<{ endpoint: WebhookEndpoint; secret: string; kind: "created" | "rotated" } | null>(null);
  const [copied, setCopied] = useState(false);
  const [editingId, setEditingId] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [editingEventsId, setEditingEventsId] = useState("");
  const [editEventTypes, setEditEventTypes] = useState<string[]>(["*"]);
  const [savingId, setSavingId] = useState("");
  const [replayingId, setReplayingId] = useState("");
  const [testingId, setTestingId] = useState("");

  const loadEndpoints = useCallback(async () => {
    setError("");
    try {
      const [items, inboxes] = await Promise.all([api.getWebhooks(agentFilter || undefined), api.getAgents()]);
      setEndpoints(items);
      setAgents(inboxes);
      if (!createAgent && inboxes.length) setCreateAgent(inboxes[0].email);
      setEndpointFilter((current) => current && items.some((item) => item.id === current) ? current : "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load webhook endpoints.");
    }
  }, [agentFilter, createAgent]);

  const loadDeliveries = useCallback(async (cursor?: string, append = false) => {
    const requestId = ++deliveryRequestId.current;
    if (append) setLoadingOlder(true);
    else {
      setDeliveryLoading(true);
      setNextCursor(null);
    }
    setActionError("");
    try {
      const page = await api.getWebhookDeliveryPage({ endpoint_id: endpointFilter || undefined, status: statusFilter || undefined, event_type: eventTypeFilter || undefined, limit: 100, cursor });
      if (requestId !== deliveryRequestId.current) return;
      setDeliveries((current) => {
        if (!append) return page.items;
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...page.items.filter((item) => !seen.has(item.id))];
      });
      setNextCursor(page.next_cursor);
    } catch (err) {
      if (requestId === deliveryRequestId.current) setActionError(err instanceof Error ? err.message : "Could not load webhook deliveries.");
    } finally {
      if (requestId === deliveryRequestId.current) {
        setDeliveryLoading(false);
        setLoadingOlder(false);
      }
    }
  }, [endpointFilter, eventTypeFilter, statusFilter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    await Promise.all([loadEndpoints(), loadDeliveries()]);
    setLoading(false);
  }, [loadEndpoints, loadDeliveries]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { void loadDeliveries(); }, [loadDeliveries]);

  const endpointById = useMemo(() => new Map(endpoints.map((endpoint) => [endpoint.id, endpoint])), [endpoints]);

  const createEndpoint = async (event: React.FormEvent) => {
    event.preventDefault();
    setCreating(true);
    setActionError("");
    try {
      const created = await api.createWebhook({ agent_email: createAgent, url: createUrl.trim(), events: createEventTypes });
      // Keep the one-time secret only in this transient component state; never persist it.
      setSecretNotice({ endpoint: created, secret: created.signing_secret, kind: "created" });
      setCreateOpen(false);
      setCreateUrl("");
      setCreateEventTypes(["*"]);
      setEndpointFilter(created.id);
      await loadEndpoints();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not create webhook endpoint.");
    } finally {
      setCreating(false);
    }
  };

  const copySecret = async () => {
    if (!secretNotice) return;
    try {
      await navigator.clipboard.writeText(secretNotice.secret);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setActionError("Clipboard access was blocked. Select and copy the secret manually.");
    }
  };

  const toggleEndpoint = async (endpoint: WebhookEndpoint) => {
    setSavingId(endpoint.id);
    setActionError("");
    try {
      await api.updateWebhook(endpoint.id, { enabled: !endpoint.enabled });
      await loadEndpoints();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not update endpoint.");
    } finally { setSavingId(""); }
  };

  const rotateSecret = async (endpoint: WebhookEndpoint) => {
    if (!window.confirm(`Rotate the signing secret for ${endpoint.agent_email}? The new secret will be shown once and cannot be retrieved later. For 24 hours, requests will carry signatures made with both the old and new secrets; after that, requests will carry only the new signature. Update your receiver before the overlap ends.`)) return;
    setSavingId(endpoint.id);
    setActionError("");
    setActionNotice("");
    try {
      const rotated = await api.rotateWebhookSecret(endpoint.id);
      setCopied(false);
      setSecretNotice({ endpoint: rotated, secret: rotated.signing_secret, kind: "rotated" });
      await loadEndpoints();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not rotate webhook signing secret.");
    } finally { setSavingId(""); }
  };

  const saveUrl = async (endpoint: WebhookEndpoint) => {
    if (!editUrl.trim()) return;
    setSavingId(endpoint.id);
    setActionError("");
    try {
      await api.updateWebhook(endpoint.id, { url: editUrl.trim() });
      setEditingId("");
      await loadEndpoints();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not update endpoint URL.");
    } finally { setSavingId(""); }
  };

  const saveEventTypes = async (endpoint: WebhookEndpoint) => {
    setSavingId(endpoint.id);
    setActionError("");
    try {
      await api.updateWebhook(endpoint.id, { events: editEventTypes });
      setEditingEventsId("");
      await loadEndpoints();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not update event subscriptions.");
    } finally { setSavingId(""); }
  };

  const deleteEndpoint = async (endpoint: WebhookEndpoint) => {
    if (!window.confirm(`Delete endpoint for ${endpoint.agent_email}? Its delivery history will also be removed.`)) return;
    setSavingId(endpoint.id);
    setActionError("");
    try {
      await api.deleteWebhook(endpoint.id);
      if (endpointFilter === endpoint.id) setEndpointFilter("");
      await loadEndpoints();
      await loadDeliveries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not delete endpoint.");
    } finally { setSavingId(""); }
  };

  const replay = async (delivery: WebhookDelivery) => {
    if (!window.confirm(`Replay ${delivery.event_type} event ${delivery.id}? The receiver may process it again.`)) return;
    setReplayingId(delivery.id);
    setActionError("");
    try {
      await api.replayWebhookDelivery(delivery.id);
      await loadDeliveries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not replay webhook event.");
    } finally { setReplayingId(""); }
  };

  const sendTestEvent = async (endpoint: WebhookEndpoint) => {
    if (!window.confirm(`Send a signed test event to the live configured URL for ${endpoint.agent_email}? Destination: ${endpoint.url}`)) return;
    setTestingId(endpoint.id);
    setActionError("");
    setActionNotice("");
    try {
      const result = await api.testWebhook(endpoint.id);
      await loadDeliveries();
      setActionNotice(`Test event queued (${result.delivery_id}). Check delivery history for its result.`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Could not send webhook test event.");
    } finally { setTestingId(""); }
  };

  return (
    <div className="space-y-4">
      <section className="card-simple flex flex-col justify-between gap-3 p-4 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">Inbound email webhooks</h2>
          <p className="mt-1 text-xs text-[#8b949e]">Configure signed HTTPS notifications for delivered inbound messages and inspect event delivery history.</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => void refresh()} className={buttonClass} disabled={loading}><RefreshCw className="h-3.5 w-3.5" />Refresh</button>
          <button onClick={() => setCreateOpen(true)} className="inline-flex items-center gap-1.5 rounded bg-[#238636] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#2ea043]"><Plus className="h-3.5 w-3.5" />Add endpoint</button>
        </div>
      </section>

      {error && <div className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72]">{error}</div>}
      {actionError && <div className="rounded border border-[#f85149]/40 bg-[#f85149]/10 p-3 text-xs text-[#ff7b72]">{actionError}<button className="ml-2 underline" onClick={() => setActionError("")}>Dismiss</button></div>}
      {actionNotice && <div className="rounded border border-[#3fb950]/40 bg-[#3fb950]/10 p-3 text-xs text-[#3fb950]">{actionNotice}<button className="ml-2 underline" onClick={() => setActionNotice("")}>Dismiss</button></div>}

      {secretNotice && <section className="card-simple space-y-3 border border-[#d29922]/40 p-4">
        <div className="flex items-start justify-between gap-3">
          <div><h3 className="text-sm font-semibold text-[#e6edf3]">{secretNotice.kind === "rotated" ? "Copy the new signing secret now" : "Copy the signing secret now"}</h3><p className="mt-1 text-xs text-[#d29922]">This secret is shown once. Closing this notice clears it from the page.</p>
            {secretNotice.kind === "rotated" && <p className="mt-1 text-xs text-[#8b949e]">For 24 hours after rotation, each request carries signatures for both the old and new secrets. After the overlap, requests carry only the new signature.</p>}
          </div>
          <button className={buttonClass} onClick={() => { setSecretNotice(null); setCopied(false); }}>Done</button>
        </div>
        <div className="flex gap-2"><input readOnly value={secretNotice.secret} className={`${inputClass} font-mono`} aria-label="One-time webhook signing secret" /><button className={buttonClass} onClick={() => void copySecret()}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}{copied ? "Copied" : "Copy"}</button></div>
        <p className="text-[11px] text-[#8b949e]">Endpoint: {secretNotice.endpoint.agent_email} · {secretNotice.endpoint.url}</p>
      </section>}

      <section className="card-simple overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-[#30363d] p-3 sm:flex-row sm:items-center sm:justify-between">
          <div><h3 className="text-xs font-semibold text-[#e6edf3]">Endpoints <span className="font-mono text-[#8b949e]">({endpoints.length})</span></h3><p className="mt-0.5 text-[11px] text-[#8b949e]">URL query values are redacted by the API. Enter the full destination URL when editing.</p></div>
          <select className={`${inputClass} sm:w-64`} value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}><option value="">All inboxes</option>{agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.email}</option>)}</select>
        </div>
        {loading ? <div className="p-8 text-center text-xs text-[#8b949e]">Loading endpoints…</div> : endpoints.length === 0 ? <div className="p-8 text-center text-xs text-[#8b949e]">No webhook endpoints configured.</div> : <div className="divide-y divide-[#30363d]">
          {endpoints.map((endpoint) => <div key={endpoint.id} className="space-y-2 p-3">
            <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
              <div className="min-w-0"><div className="flex items-center gap-2"><span className="text-xs font-semibold text-[#e6edf3]">{endpoint.agent_email}</span><span className={`rounded border px-1.5 py-0.5 text-[10px] ${endpoint.enabled ? "border-[#3fb950]/40 bg-[#3fb950]/10 text-[#3fb950]" : "border-[#8b949e]/30 text-[#8b949e]"}`}>{endpoint.enabled ? "Enabled" : "Disabled"}</span></div>
                {editingId === endpoint.id ? <div className="mt-2 flex gap-2"><input className={inputClass} value={editUrl} onChange={(event) => setEditUrl(event.target.value)} placeholder="Enter the full URL, including query values" /><button className={buttonClass} onClick={() => void saveUrl(endpoint)} disabled={savingId === endpoint.id || !editUrl.trim()}>Save URL</button><button className={buttonClass} onClick={() => setEditingId("")}>Cancel</button></div> : <div className="mt-1 break-all font-mono text-[11px] text-[#8b949e]">{endpoint.url}</div>}
                <div className="mt-1 text-[11px] text-[#8b949e]">Events: {eventTypesLabel(endpointEventTypes(endpoint))}</div>
                {editingEventsId === endpoint.id && <div className="mt-2 max-w-2xl space-y-2"><EventTypeSelector value={editEventTypes} onChange={setEditEventTypes} /><div className="flex gap-2"><button className={buttonClass} onClick={() => void saveEventTypes(endpoint)} disabled={savingId === endpoint.id || !editEventTypes.length}>Save subscriptions</button><button className={buttonClass} onClick={() => setEditingEventsId("")}>Cancel</button></div></div>}
                <div className="mt-1 text-[10px] text-[#6e7681]">Created {date(endpoint.created_at)} · Updated {date(endpoint.updated_at)}</div>
              </div>
              <div className="flex shrink-0 flex-wrap gap-1.5">
                <button className={buttonClass} onClick={() => void toggleEndpoint(endpoint)} disabled={savingId === endpoint.id}>{endpoint.enabled ? "Disable" : "Enable"}</button>
                <button className={buttonClass} onClick={() => void rotateSecret(endpoint)} disabled={savingId === endpoint.id}><RefreshCw className="h-3.5 w-3.5" />Rotate secret</button>
                <button className={buttonClass} onClick={() => void sendTestEvent(endpoint)} disabled={!endpoint.enabled || testingId === endpoint.id || savingId === endpoint.id} title={!endpoint.enabled ? "Enable endpoint to send a test event" : undefined}>{testingId === endpoint.id ? "Sending test…" : "Send test event"}</button>
                {editingId !== endpoint.id && <button className={buttonClass} onClick={() => { setEditingId(endpoint.id); setEditUrl(""); }}>Edit URL</button>}
                {editingEventsId !== endpoint.id && <button className={buttonClass} onClick={() => { setEditingEventsId(endpoint.id); setEditEventTypes(endpointEventTypes(endpoint)); }}>Edit events</button>}
                <button className="inline-flex items-center gap-1.5 rounded border border-[#da3633]/40 px-2.5 py-1.5 text-xs text-[#f85149] hover:bg-[#da3633]/10 disabled:opacity-50" onClick={() => void deleteEndpoint(endpoint)} disabled={savingId === endpoint.id}><Trash2 className="h-3.5 w-3.5" />Delete</button>
              </div>
            </div>
          </div>)}
        </div>}
      </section>

      <section className="card-simple overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-[#30363d] p-3 sm:flex-row sm:items-center sm:justify-between">
          <div><h3 className="text-xs font-semibold text-[#e6edf3]">Delivery history <span className="font-mono text-[#8b949e]">({deliveries.length})</span></h3><p className="mt-0.5 text-[11px] text-[#8b949e]">Event payloads contain message metadata only, without body or attachments.</p></div>
          <div className="flex flex-wrap gap-2"><select className={`${inputClass} sm:w-52`} value={endpointFilter} onChange={(event) => setEndpointFilter(event.target.value)}><option value="">All endpoints</option>{endpoints.map((endpoint) => <option key={endpoint.id} value={endpoint.id}>{endpoint.agent_email} · {endpoint.id.slice(-8)}</option>)}</select><select className={`${inputClass} sm:w-36`} value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as WebhookDeliveryStatus | "")}><option value="">All statuses</option>{statuses.filter(Boolean).map((status) => <option key={status} value={status}>{status}</option>)}</select><select className={`${inputClass} sm:w-48`} value={eventTypeFilter} onChange={(event) => { deliveryRequestId.current += 1; setDeliveries([]); setNextCursor(null); setEventTypeFilter(event.target.value); }}><option value="">All event types</option>{deliveryEventTypes.map((eventType) => <option key={eventType.value} value={eventType.value}>{eventType.label} · {eventType.value}</option>)}</select></div>
        </div>
        {deliveryLoading && !deliveries.length ? <div className="p-8 text-center text-xs text-[#8b949e]">Loading delivery history…</div> : !deliveries.length ? <div className="p-8 text-center text-xs text-[#8b949e]">No delivery events match these filters.</div> : <div className="divide-y divide-[#30363d]">
          {deliveries.map((delivery) => <DeliveryRow key={delivery.id} delivery={delivery} endpoint={endpointById.get(delivery.endpoint_id)} replaying={replayingId === delivery.id} onReplay={() => void replay(delivery)} />)}
        </div>}
        {deliveries.length > 0 && nextCursor && <div className="border-t border-[#30363d] p-3 text-center"><button className={buttonClass} disabled={loadingOlder} onClick={() => void loadDeliveries(nextCursor, true)}><RefreshCw className={`h-3.5 w-3.5 ${loadingOlder ? "animate-spin" : ""}`} />{loadingOlder ? "Loading…" : "Load older"}</button></div>}
      </section>

      {createOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"><form onSubmit={createEndpoint} className="w-full max-w-lg space-y-4 rounded-lg border border-[#30363d] bg-[#161b22] p-5 shadow-xl">
        <div><h3 className="text-sm font-semibold text-[#e6edf3]">Add webhook endpoint</h3><p className="mt-1 text-xs text-[#8b949e]">Only HTTPS destinations on port 443 are accepted. A new signing secret is generated for each endpoint.</p></div>
        <label className="block space-y-1 text-xs text-[#8b949e]">Inbox<select required className={inputClass} value={createAgent} onChange={(event) => setCreateAgent(event.target.value)}><option value="" disabled>Select inbox</option>{agents.map((agent) => <option key={agent.id} value={agent.email}>{agent.email}</option>)}</select></label>
        <label className="block space-y-1 text-xs text-[#8b949e]">HTTPS URL<input required type="url" pattern="https://.*" className={`${inputClass} font-mono`} placeholder="https://example.com/hooks/email" value={createUrl} onChange={(event) => setCreateUrl(event.target.value)} /></label>
        <EventTypeSelector value={createEventTypes} onChange={setCreateEventTypes} />
        <div className="flex justify-end gap-2"><button type="button" className={buttonClass} onClick={() => setCreateOpen(false)}>Cancel</button><button type="submit" disabled={creating || !createAgent || !createEventTypes.length} className="rounded bg-[#238636] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#2ea043] disabled:opacity-50">{creating ? "Creating…" : "Create endpoint"}</button></div>
      </form></div>}
    </div>
  );
}

function DeliveryRow({ delivery, endpoint, replaying, onReplay }: { delivery: WebhookDelivery; endpoint?: WebhookEndpoint; replaying: boolean; onReplay: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const canReplay = (delivery.status === "failed" || delivery.status === "delivered") && endpoint?.enabled;
  return <div className="p-3">
    <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-[11px] text-[#e6edf3]">{delivery.id}</span><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusStyle(delivery.status)}`}>{delivery.status}</span><span className="text-[10px] text-[#8b949e]">{delivery.event_type}</span></div>
        <div className="mt-1 text-[11px] text-[#8b949e]">{delivery.agent_email} · {endpoint?.url ?? delivery.endpoint_id} · {date(delivery.created_at)} · {delivery.attempts} attempts{delivery.replay_count > 0 ? ` · ${delivery.replay_count} replays` : ""}</div>
        {delivery.last_error && <div className="mt-1 break-words text-[11px] text-[#f85149]">{delivery.last_error}</div>}
        {delivery.replayed_at && <div className="mt-1 text-[10px] text-[#8b949e]">Last replay {date(delivery.replayed_at)}{delivery.replayed_by ? ` by ${delivery.replayed_by}` : ""}</div>}
      </div>
      <div className="flex shrink-0 gap-1.5"><button className="inline-flex items-center gap-1.5 rounded border border-[#30363d] px-2.5 py-1.5 text-xs text-[#c9d1d9] hover:bg-[#21262d]" onClick={() => setExpanded((value) => !value)}>{expanded ? "Hide event" : "View event"}<ExternalLink className="h-3 w-3" /></button><button className={buttonClass} disabled={!canReplay || replaying} title={!endpoint?.enabled ? "Enable endpoint to replay" : undefined} onClick={onReplay}><RotateCcw className="h-3.5 w-3.5" />{replaying ? "Replaying…" : "Replay"}</button></div>
    </div>
    {expanded && <pre className="mt-3 max-h-64 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-3 text-[11px] text-[#c9d1d9]">{JSON.stringify(delivery.payload, null, 2)}</pre>}
  </div>;
}
