"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Mail,
  Send,
  ShieldCheck,
  ShieldAlert,
  Reply,
  Forward,
  Plus,
  Bot
} from "lucide-react";
import { api } from "@/lib/api";
import { Agent, EmailTemplate, EmailTemplatePreview, Message, MessageAttachment, OutboundAttachmentInput } from "@/lib/types";
import { Dropdown } from "@/components/Dropdown";
import { serverDate } from "@/lib/time";
import { MessageSnippets } from "@/components/MessageSnippets";
import { ScheduleField, scheduleProblem, toOffsetIso } from "@/components/inbox/ScheduleField";
import { LabelEditor } from "@/components/inbox/LabelEditor";
import { TriageBar } from "@/components/inbox/TriageBar";
import { LifecycleTimeline } from "@/components/inbox/LifecycleTimeline";
import { AttachmentList } from "@/components/inbox/AttachmentList";
import {
  TriageMessage,
  SignedAttachment,
  describeError,
  deleteMessagePermanently,
  getSignedAttachments,
  listFilteredMessages,
  replyScheduled,
  restoreMessage,
  sendScheduledMessage,
  statusClasses,
  trashMessage,
  updateMessageLabels,
} from "@/lib/inbox-api";

function parseTemplateData(source: string): Record<string, unknown> {
  const value: unknown = JSON.parse(source);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Template data must be a JSON object.");
  }
  return value as Record<string, unknown>;
}

export default function InboxPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentEmail, setSelectedAgentEmail] = useState<string>("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [selectedMessage, setSelectedMessage] = useState<Message | null>(null);
  const [threadMessages, setThreadMessages] = useState<Message[]>([]);
  const [threadAttachments, setThreadAttachments] = useState<Record<string, SignedAttachment[]>>({});
  const [view, setView] = useState<"inbox" | "scheduled" | "trash">("inbox");
  const [labelFilter, setLabelFilter] = useState<string>("");
  const [labelInput, setLabelInput] = useState<string>("");
  const [showHistory, setShowHistory] = useState<boolean>(false);
  const [replySchedule, setReplySchedule] = useState<string>("");
  const [composeSchedule, setComposeSchedule] = useState<string>("");
  const [filterDirection, setFilterDirection] = useState<"" | "inbound" | "outbound">("");
  const [filterReadStatus, setFilterReadStatus] = useState<"unread" | "read" | "all">("all");
  const [filterStatus, setFilterStatus] = useState<Message["status"] | "">("");
  const [loading, setLoading] = useState<boolean>(true);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [listError, setListError] = useState<string>("");
  const [searchInput, setSearchInput] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const messageRequestId = useRef(0);
  const readMarkRequests = useRef(new Set<string>());
  const failedReadMarks = useRef(new Set<string>());
  const previousReadSelection = useRef("");
  const selectedMessageKey = useRef("");
  const activeMessageView = useRef("");
  const activeAgentEmail = useRef(selectedAgentEmail);
  const activeReadStatus = useRef(filterReadStatus);
  selectedMessageKey.current = `${selectedAgentEmail}\u0000${selectedMessage?.id || ""}`;
  activeMessageView.current = `${selectedAgentEmail}\u0000${filterDirection}\u0000${filterReadStatus}\u0000${filterStatus}\u0000${searchQuery}`;
  activeAgentEmail.current = selectedAgentEmail;
  activeReadStatus.current = filterReadStatus;

  // In-Thread Reply Form
  const [replyText, setReplyText] = useState<string>("");
  const [emailTemplates, setEmailTemplates] = useState<EmailTemplate[]>([]);
  const [replyTemplateId, setReplyTemplateId] = useState("");
  const [replyTemplateData, setReplyTemplateData] = useState("{}");
  const [replyTemplatePreview, setReplyTemplatePreview] = useState<EmailTemplatePreview | null>(null);
  const [replyTemplateError, setReplyTemplateError] = useState("");
  const replyDraftKey = selectedAgentEmail && selectedMessage
    ? `open-agent-email:reply-draft:${selectedAgentEmail}:${selectedMessage.thread_id}`
    : "";
  const previousReplyDraftKey = useRef("");
  const [sendingReply, setSendingReply] = useState<boolean>(false);
  const [handoffRecipient, setHandoffRecipient] = useState<string>("");
  const [handoffNote, setHandoffNote] = useState<string>("");
  const [sendingHandoff, setSendingHandoff] = useState<boolean>(false);
  const [handoffResult, setHandoffResult] = useState<string>("");

  // Keep unsent replies per inbox/thread on this device so navigating away from
  // a conversation does not discard a human's work.
  useEffect(() => {
    const previousKey = previousReplyDraftKey.current;
    if (previousKey === replyDraftKey) return;
    setReplyTemplateId("");
    setReplyTemplateData("{}");
    setReplyTemplatePreview(null);
    setReplyTemplateError("");
    if (previousKey) {
      try {
        if (replyText) window.localStorage.setItem(previousKey, replyText);
        else window.localStorage.removeItem(previousKey);
      } catch { /* Storage can be unavailable in private or restricted contexts. */ }
    }
    previousReplyDraftKey.current = replyDraftKey;
    if (!replyDraftKey) {
      setReplyText("");
      return;
    }
    try {
      setReplyText(window.localStorage.getItem(replyDraftKey) || "");
    } catch {
      setReplyText("");
    }
  }, [replyDraftKey]);

  // New Compose Outbound Modal
  const [isComposeOpen, setIsComposeOpen] = useState<boolean>(false);
  const [composeRecipient, setComposeRecipient] = useState<string>("");
  const [composeSubject, setComposeSubject] = useState<string>("");
  const [composeBody, setComposeBody] = useState<string>("");
  const [composeTemplateId, setComposeTemplateId] = useState("");
  const [composeTemplateData, setComposeTemplateData] = useState("{}");
  const [composeTemplatePreview, setComposeTemplatePreview] = useState<EmailTemplatePreview | null>(null);
  const [composeTemplateError, setComposeTemplateError] = useState("");
  const [composeAttachments, setComposeAttachments] = useState<OutboundAttachmentInput[]>([]);
  const [composeAttachmentError, setComposeAttachmentError] = useState<string>("");
  const [sendingCompose, setSendingCompose] = useState<boolean>(false);

  // Load Agents
  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const ags = await api.getAgents();
        setAgents(ags);
        if (ags.length > 0) {
          setSelectedAgentEmail(ags[0].email);
        }
      } catch (err) {
        console.error("Failed to load agents:", err);
      }
    };
    fetchAgents();
  }, []);

  useEffect(() => {
    let active = true;
    if (!selectedAgentEmail) {
      setEmailTemplates([]);
      return;
    }
    api.getEmailTemplates(selectedAgentEmail)
      .then((items) => { if (active) setEmailTemplates(items); })
      .catch((error) => {
        if (active) {
          setEmailTemplates([]);
          console.error("Failed to load inbox templates:", error);
        }
      });
    setComposeTemplateId("");
    setComposeTemplateData("{}");
    setComposeTemplatePreview(null);
    setReplyTemplateId("");
    setReplyTemplateData("{}");
    setReplyTemplatePreview(null);
    return () => { active = false; };
  }, [selectedAgentEmail]);

  // Load Messages for Selected Agent
  const loadMessages = async (cursor?: string, append = false) => {
    if (!selectedAgentEmail) return;
    const requestId = ++messageRequestId.current;
    try {
      if (append) setLoadingMore(true);
      else setLoading(true);
      setListError("");
      if (view !== "inbox" || labelFilter) {
        // The cursor-paged list has no trash, scheduled or label filter, so these views use the plain list (newest 200).
        const rows = await listFilteredMessages(selectedAgentEmail, {
          trashed: view === "trash",
          status: view === "scheduled" ? "scheduled" : filterStatus || undefined,
          label: labelFilter || undefined,
          direction: filterDirection || undefined,
          limit: 200,
        });
        if (requestId !== messageRequestId.current) return;
        const needle = searchQuery.toLowerCase();
        const shown = rows.filter((row) =>
          (filterReadStatus === "all" || (filterReadStatus === "unread" ? !row.is_read : row.is_read)) &&
          (!needle || `${row.subject} ${row.sender} ${row.recipient}`.toLowerCase().includes(needle))
        ) as unknown as Message[];
        setMessages(shown);
        setNextCursor(null);
        if (!append) {
          if (shown.length === 0) setSelectedMessage(null);
          else if (!selectedMessage || !shown.some((item) => item.id === selectedMessage.id)) setSelectedMessage(shown[0]);
        }
        return;
      }
      const page = await api.getMessagePage(selectedAgentEmail, {
        q: searchQuery || undefined,
        direction: filterDirection || undefined,
        read_status: filterReadStatus,
        status: filterStatus || undefined,
        limit: 50,
        cursor,
      });
      if (requestId !== messageRequestId.current) return;
      const updated = append
        ? [...messages, ...page.items.filter((item) => !messages.some((existing) => existing.id === item.id))]
        : page.items;
      setMessages(updated);
      setNextCursor(page.next_cursor);
      if (!append) {
        if (page.items.length === 0) setSelectedMessage(null);
        else if (!selectedMessage || !page.items.some((item) => item.id === selectedMessage.id)) setSelectedMessage(page.items[0]);
      }
    } catch (err) {
      console.error("Failed to fetch messages:", err);
      if (requestId === messageRequestId.current) setListError(err instanceof Error ? err.message : "Could not load messages.");
    } finally {
      if (requestId === messageRequestId.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => setSearchQuery(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setMessages([]);
    setNextCursor(null);
    loadMessages();
  }, [selectedAgentEmail, filterDirection, filterReadStatus, filterStatus, searchQuery, view, labelFilter]);

  useEffect(() => {
    let active = true;
    setThreadMessages([]);
    setThreadAttachments({});
    if (selectedMessage && selectedAgentEmail) {
      api.getThread(selectedAgentEmail, selectedMessage.thread_id)
        .then(async (items) => {
          if (!active) return;
          setThreadMessages(items);
          const entries = await Promise.all(items.map(async (item) => [
            item.id,
            await getSignedAttachments(selectedAgentEmail, item.id),
          ] as const));
          if (active) setThreadAttachments(Object.fromEntries(entries));
        })
        .catch((err) => console.error("Failed to load conversation:", err));
    }
    return () => { active = false; };
  }, [selectedAgentEmail, selectedMessage?.id]);

  useEffect(() => {
    const previousKey = previousReadSelection.current;
    const currentKey = selectedMessageKey.current;
    if (previousKey && previousKey !== currentKey) {
      failedReadMarks.current.delete(previousKey);
      readMarkRequests.current.delete(previousKey);
    }
    previousReadSelection.current = currentKey;
  }, [selectedAgentEmail, selectedMessage?.id]);

  // Opening an unread inbound message marks it read. Keep GETs read-only and
  // deduplicate effect reruns (including React Strict Mode) by mailbox/message.
  useEffect(() => {
    if (!selectedAgentEmail || !selectedMessage || selectedMessage.direction !== "inbound" || selectedMessage.is_read) return;
    const agentEmail = selectedAgentEmail;
    const message = selectedMessage;
    const messageKey = `${agentEmail}\u0000${message.id}`;
    if (readMarkRequests.current.has(messageKey) || failedReadMarks.current.has(messageKey)) return;
    readMarkRequests.current.add(messageKey);

    const viewAtStart = activeMessageView.current;
    const mark = (item: Message) => item.id === message.id ? { ...item, is_read: true } : item;
    setSelectedMessage((current) => current?.id === message.id ? { ...current, is_read: true } : current);
    setMessages((current) => current.map(mark));
    setThreadMessages((current) => current.map(mark));

    api.markMessageRead(agentEmail, message.id)
      .then((updated) => {
        if (activeAgentEmail.current !== agentEmail) return;
        const stillSelected = selectedMessageKey.current === messageKey;
        if (stillSelected && activeReadStatus.current === "unread") {
          setSelectedMessage((current) => current?.id === updated.id ? null : current);
        } else if (stillSelected) {
          setSelectedMessage((current) => current?.id === updated.id ? updated : current);
        }
        setMessages((current) => activeReadStatus.current === "unread"
          ? current.filter((item) => item.id !== updated.id)
          : current.map((item) => item.id === updated.id ? updated : item));
        if (stillSelected) {
          setThreadMessages((current) => current.map((item) => item.id === updated.id ? updated : item));
        }
      })
      .catch((error) => {
        if (activeAgentEmail.current !== agentEmail) return;
        const stillSelected = selectedMessageKey.current === messageKey;
        if (stillSelected) setSelectedMessage((current) => current?.id === message.id ? message : current);
        setMessages((current) => current.map((item) => item.id === message.id ? message : item));
        if (stillSelected) {
          setThreadMessages((current) => current.map((item) => item.id === message.id ? message : item));
        }
        readMarkRequests.current.delete(messageKey);
        if (stillSelected) failedReadMarks.current.add(messageKey);
        if (activeMessageView.current === viewAtStart) {
          setListError(error instanceof Error ? error.message : "Could not mark this message as read.");
        }
      });
  }, [selectedAgentEmail, selectedMessage?.id, selectedMessage?.direction, selectedMessage?.is_read, filterReadStatus, messages]);

  // Handle Send Reply
  const previewReplyTemplate = async () => {
    if (!selectedAgentEmail || !replyTemplateId) return;
    try {
      setReplyTemplateError("");
      setReplyTemplatePreview(await api.previewSavedEmailTemplate(
        selectedAgentEmail, replyTemplateId, parseTemplateData(replyTemplateData),
      ));
    } catch (error) {
      setReplyTemplatePreview(null);
      setReplyTemplateError(error instanceof Error ? error.message : "Could not preview template.");
    }
  };

  const previewComposeTemplate = async () => {
    if (!selectedAgentEmail || !composeTemplateId) return;
    try {
      setComposeTemplateError("");
      setComposeTemplatePreview(await api.previewSavedEmailTemplate(
        selectedAgentEmail, composeTemplateId, parseTemplateData(composeTemplateData),
      ));
    } catch (error) {
      setComposeTemplatePreview(null);
      setComposeTemplateError(error instanceof Error ? error.message : "Could not preview template.");
    }
  };

  const handleSendReply = async () => {
    if (!selectedMessage || (!replyTemplateId && !replyText.trim())) return;
    if (scheduleProblem(replySchedule)) return;
    try {
      setSendingReply(true);
      const latestMessage = threadMessages[threadMessages.length - 1] || selectedMessage;
      const replyBody = replyTemplateId
        ? { template_id: replyTemplateId, template_data: parseTemplateData(replyTemplateData) }
        : { body_text: replyText };
      const replyAt = replySchedule ? toOffsetIso(replySchedule) : null;
      const reply = replyAt
        ? ((await replyScheduled(selectedAgentEmail, latestMessage.id, { ...replyBody, scheduled_at: replyAt })) as unknown as Message)
        : await api.replyMessage(selectedAgentEmail, latestMessage.id, replyBody);
      setReplySchedule("");
      try { window.localStorage.removeItem(replyDraftKey); } catch { /* Best effort cleanup. */ }
      setReplyText("");
      setReplyTemplateId("");
      setReplyTemplateData("{}");
      setReplyTemplatePreview(null);
      await loadMessages();
      setSelectedMessage(reply);
    } catch (err: any) {
      alert(`Failed to send reply: ${describeError(err)}`);
    } finally {
      setSendingReply(false);
    }
  };

  // Triage: take a message out of the current list (trashed, restored or deleted) and move on to the next one.
  const dropFromList = (id: string) => {
    const remaining = messages.filter((item) => item.id !== id);
    setMessages(remaining);
    if (selectedMessage?.id === id) setSelectedMessage(remaining[0] ?? null);
  };

  const patchMessage = (updated: TriageMessage) => {
    const merge = (item: Message) => (item.id === updated.id ? ({ ...item, ...updated } as unknown as Message) : item);
    setMessages((current) => current.map(merge));
    setThreadMessages((current) => current.map(merge));
    setSelectedMessage((current) => (current && current.id === updated.id ? merge(current) : current));
  };

  const handleTrash = async (id: string) => {
    await trashMessage(selectedAgentEmail, id).catch((err) => { throw new Error(describeError(err)); });
    dropFromList(id);
  };
  const handleRestore = async (id: string) => {
    await restoreMessage(selectedAgentEmail, id).catch((err) => { throw new Error(describeError(err)); });
    dropFromList(id);
  };
  const handleDeleteForever = async (id: string) => {
    await deleteMessagePermanently(selectedAgentEmail, id).catch((err) => { throw new Error(describeError(err)); });
    dropFromList(id);
  };
  const handleLabelChange = async (id: string, change: { add_labels?: string[]; remove_labels?: string[] }) => {
    const updated = await updateMessageLabels(selectedAgentEmail, id, change).catch((err) => { throw new Error(describeError(err)); });
    patchMessage(updated);
  };

  const handleHandoff = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedMessage || !handoffRecipient.trim()) return;
    try {
      setSendingHandoff(true);
      setHandoffResult("");
      const forwarded = await api.forwardMessage(selectedAgentEmail, selectedMessage.id, {
        recipient: handoffRecipient.trim(),
        note: handoffNote.trim() || undefined,
      });
      setHandoffResult(forwarded.status === "pending_review"
        ? "Handoff is waiting for outbound approval."
        : forwarded.status === "failed"
          ? `Delivery failed: ${forwarded.delivery_error || "Check the SMTP relay settings."}`
          : `Forwarded to ${forwarded.recipient}.`);
      setHandoffRecipient("");
      setHandoffNote("");
      await loadMessages();
    } catch (err) {
      setHandoffResult(err instanceof Error ? err.message : "Could not forward this message.");
    } finally {
      setSendingHandoff(false);
    }
  };

  // Handle Fresh Outbound Compose
  const handleComposeSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!composeRecipient || (!composeTemplateId && (!composeSubject || !composeBody))) return;
    if (scheduleProblem(composeSchedule)) return;
    try {
      setSendingCompose(true);
      const composeAt = composeSchedule ? toOffsetIso(composeSchedule) : null;
      const content = composeTemplateId
        ? {
          recipient: composeRecipient,
          template_id: composeTemplateId,
          template_data: parseTemplateData(composeTemplateData),
          attachments: composeAttachments,
        }
        : {
          recipient: composeRecipient,
          subject: composeSubject,
          body_text: composeBody,
          attachments: composeAttachments,
        };
      const msg = composeAt
        ? ((await sendScheduledMessage(selectedAgentEmail, { ...content, scheduled_at: composeAt })) as unknown as Message)
        : await api.sendMessage(selectedAgentEmail, content);
      setComposeSchedule("");
      setIsComposeOpen(false);
      setComposeRecipient("");
      setComposeSubject("");
      setComposeBody("");
      setComposeTemplateId("");
      setComposeTemplateData("{}");
      setComposeTemplatePreview(null);
      setComposeAttachments([]);
      setComposeAttachmentError("");
      await loadMessages();
      setSelectedMessage(msg);
    } catch (err: any) {
      alert(`Failed to send email: ${describeError(err)}`);
    } finally {
      setSendingCompose(false);
    }
  };

  const handleComposeFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setComposeAttachmentError("");
    const current = [...composeAttachments];
    try {
      for (const file of Array.from(files)) {
        if (file.size > 10 * 1024 * 1024) throw new Error(`${file.name} is larger than the 10 MiB limit.`);
        if (current.length >= 10) throw new Error("A message can include at most 10 attachments.");
        const dataUrl = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
          reader.onload = () => typeof reader.result === "string" ? resolve(reader.result) : reject(new Error(`Could not read ${file.name}.`));
          reader.readAsDataURL(file);
        });
        const encoded = dataUrl.split(",", 2)[1];
        if (!encoded) throw new Error(`Could not encode ${file.name}.`);
        current.push({ filename: file.name, content_type: file.type || "application/octet-stream", content_base64: encoded });
      }
      const totalBytes = current.reduce((total, item) => total + Math.floor(item.content_base64.length * 3 / 4), 0);
      if (totalBytes > 10 * 1024 * 1024) throw new Error("Attachments together must be 10 MiB or smaller.");
      setComposeAttachments(current);
    } catch (error) {
      setComposeAttachmentError(error instanceof Error ? error.message : "Could not add attachments.");
    }
  };

  if (agents.length === 0 && !loading) {
    return (
      <div className="card-simple p-12 text-center text-xs text-[#8b949e] space-y-3">
        <Bot className="w-8 h-8 text-[#8b949e] mx-auto" />
        <div className="text-sm font-semibold text-[#e6edf3]">No Agent Inboxes Configured</div>
        <p className="max-w-md mx-auto text-[11px]">
          Create an agent inbox to start receiving, viewing, and sending real two-way email threads.
        </p>
        <Link
          href="/agents"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>Create Agent Inbox</span>
        </Link>
      </div>
    );
  }

  const isTrashed = view === "trash" || Boolean(selectedMessage && (selectedMessage as unknown as TriageMessage).deleted_at);

  const agentDropdownOptions = agents.map((ag) => ({
    value: ag.email,
    label: `${ag.display_name}`,
    sublabel: ag.email,
  }));

  const directionOptions = [
    { value: "", label: "All Messages" },
    { value: "inbound", label: "Inbound Only" },
    { value: "outbound", label: "Outbound Only" },
  ];
  const viewOptions = [
    { value: "inbox", label: "Inbox" },
    { value: "scheduled", label: "Scheduled" },
    { value: "trash", label: "Trash" },
  ];
  const readStatusOptions = [
    { value: "all", label: "All Read Statuses" },
    { value: "unread", label: "Unread" },
    { value: "read", label: "Read" },
  ];
  const deliveryStatusOptions = [
    { value: "", label: "All Delivery Statuses" },
    { value: "delivered", label: "Delivered" },
    { value: "accepted_by_relay", label: "Accepted by Relay" },
    { value: "sending", label: "Sending" },
    { value: "delivery_unknown", label: "Delivery Unknown" },
    { value: "pending_review", label: "Pending Review" },
    { value: "rejected", label: "Rejected" },
    { value: "blocked", label: "Blocked" },
    { value: "failed", label: "Failed" },
  ];

  return (
    <div className="space-y-4">
      {/* Top Controls Row */}
      <div className="card-simple p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        {/* Custom Dropdowns for Agent & Filter */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="w-64">
            <Dropdown
              options={agentDropdownOptions}
              value={selectedAgentEmail}
              onChange={(val) => {
                setSelectedAgentEmail(val);
                setSelectedMessage(null);
              }}
              placeholder="Select an Agent Inbox"
            />
          </div>

          <div className="w-36">
            <Dropdown
              options={viewOptions}
              value={view}
              onChange={(value) => { setView(value as "inbox" | "scheduled" | "trash"); setSelectedMessage(null); }}
            />
          </div>
          <div className="w-44">
            <Dropdown
              options={directionOptions}
              value={filterDirection}
              onChange={(value) => setFilterDirection(value as "" | "inbound" | "outbound")}
            />
          </div>
          <div className="w-40">
            <Dropdown
              options={readStatusOptions}
              value={filterReadStatus}
              onChange={(value) => setFilterReadStatus(value as "unread" | "read" | "all")}
            />
          </div>
          <div className="w-48">
            <Dropdown
              options={deliveryStatusOptions}
              value={filterStatus}
              onChange={(value) => setFilterStatus(value as Message["status"] | "")}
            />
          </div>
          <form
            onSubmit={(event) => { event.preventDefault(); setLabelFilter(labelInput.trim()); }}
            className="flex items-center gap-1"
          >
            <input
              type="text"
              value={labelInput}
              onChange={(event) => { setLabelInput(event.target.value); if (!event.target.value.trim()) setLabelFilter(""); }}
              placeholder="Filter by label"
              aria-label="Filter by label"
              className="w-36 bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-zinc-500"
            />
            {labelFilter && (
              <button type="button" onClick={() => { setLabelInput(""); setLabelFilter(""); }} className="text-[11px] text-[#8b949e] hover:text-[#e6edf3]">Clear</button>
            )}
          </form>
          <input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="Search messages…"
            aria-label="Search messages"
            className="w-full sm:w-64 bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-zinc-500"
          />
        </div>

        <button
          onClick={() => setIsComposeOpen(true)}
          className="px-3 py-2 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium flex items-center gap-1.5 transition-colors self-start sm:self-auto"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>Compose Outbound</span>
        </button>
      </div>

      {/* Two-Column Client */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-[600px]">
        {/* Left: Message List */}
        <div className="lg:col-span-5 card-simple flex flex-col overflow-hidden">
          <div className="p-3 border-b border-[#30363d] flex items-center justify-between text-xs text-[#8b949e]">
            <span className="font-medium text-[#e6edf3]">
              Messages {messages.length ? `(${messages.length}${nextCursor ? "+" : ""})` : ""}
            </span>
            <button onClick={() => loadMessages()} disabled={loading} className="hover:text-[#e6edf3] disabled:opacity-50 text-[11px]">
              Refresh
            </button>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-[#30363d]">
            {(view !== "inbox" || labelFilter) && !loading && messages.length > 0 && (
              <div className="p-2 text-[10px] text-[#8b949e]">Showing the newest {messages.length} matching messages. Paging is only available in the Inbox view without a label filter.</div>
            )}
            {listError && (
              <div role="alert" className="p-3 text-xs text-[#f85149]">{listError}</div>
            )}
            {loading && messages.length === 0 ? (
              <div className="p-10 text-center text-xs text-[#8b949e]">Loading messages…</div>
            ) : messages.length === 0 ? (
              <div className="p-10 text-center text-xs text-[#8b949e]">
                {searchQuery ? "No messages match this search." : view === "trash" ? "Trash is empty." : view === "scheduled" ? "No scheduled messages." : labelFilter ? "No messages carry this label." : "No messages found for this inbox."}
              </div>
            ) : (
              messages.map((msg) => {
                const isSelected = selectedMessage?.id === msg.id;
                return (
                  <div
                    key={msg.id}
                    onClick={() => setSelectedMessage(msg)}
                    className={`p-3 cursor-pointer text-xs transition-colors ${
                      isSelected
                        ? "bg-[#21262d] border-l-2 border-blue-400"
                        : "hover:bg-[#1c2128]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className={`truncate ${msg.direction === "inbound" && !msg.is_read ? "font-semibold text-white" : "font-medium text-[#e6edf3]"}`}>
                        {msg.direction === "inbound" ? msg.sender : `To: ${msg.recipient}`}
                      </span>
                      <span className="text-[10px] font-mono text-[#8b949e] shrink-0">
                        {serverDate(msg.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>

                    <div className="text-[#e6edf3] font-medium truncate mb-1">
                      {msg.subject || "(No Subject)"}
                    </div>

                    {msg.direction === "inbound" && !msg.is_read && (
                      <span className="inline-flex mb-1 rounded-full bg-blue-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-blue-300">Unread</span>
                    )}

                    <p className="text-[11px] text-[#8b949e] line-clamp-2">
                      {msg.body_text}
                    </p>

                    <div className="flex items-center gap-1.5 mt-2">
                      <span className="text-[9px] font-mono px-1.5 py-0.2 rounded border border-[#30363d] text-[#8b949e] capitalize">
                        {msg.direction}
                      </span>
                      <span
                        className={`text-[9px] font-mono px-1.5 py-0.2 rounded border ${statusClasses(msg.status)}`}
                      >
                        {msg.status.replace("_", " ")}
                      </span>
                      {(msg as unknown as TriageMessage).labels?.map((tag) => (
                        <span key={tag} className="text-[9px] px-1.5 py-0.2 rounded-full bg-[#21262d] border border-[#30363d] text-[#e6edf3]">{tag}</span>
                      ))}
                      {msg.spf_status === "pass" && msg.dkim_status === "pass" && msg.dmarc_status === "pass" && (
                        <span className="text-[9px] font-mono text-[#2ea043] flex items-center gap-0.5 ml-auto">
                          <ShieldCheck className="w-3 h-3" /> VERIFIED
                        </span>
                      )}
                    </div>
                    {(msg as unknown as TriageMessage).scheduled_for && String(msg.status) === "scheduled" && (
                      <div className="mt-1 text-[10px] text-[#d29922]">Sends {new Date(`${(msg as unknown as TriageMessage).scheduled_for}Z`).toLocaleString()}</div>
                    )}
                  </div>
                );
              })
            )}
            {nextCursor && messages.length > 0 && (
              <div className="p-3 text-center">
                <button
                  onClick={() => loadMessages(nextCursor, true)}
                  disabled={loadingMore || loading}
                  className="px-3 py-1.5 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] disabled:opacity-50 text-[#e6edf3] text-xs"
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Right: Message Detail & Thread */}
        <div className="lg:col-span-7 card-simple flex flex-col overflow-hidden">
          {selectedMessage ? (
            <div className="flex-1 flex flex-col h-full">
              {/* Header */}
              <div className="p-4 border-b border-[#30363d] space-y-3">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-semibold text-[#e6edf3]">
                      {selectedMessage.subject || "(No Subject)"}
                    </h3>
                    <div className="text-[11px] text-[#8b949e] font-mono mt-0.5">
                      Thread: {selectedMessage.thread_id} ·{" "}
                      {serverDate(selectedMessage.created_at).toLocaleString()}
                    </div>
                  </div>

                  <span
                    className={`text-[10px] font-mono px-2 py-0.5 rounded border capitalize ${statusClasses(selectedMessage.status)}`}
                  >
                    {selectedMessage.status.replace("_", " ")}
                  </span>
                </div>

                <div className="bg-[#0d1117] p-2.5 rounded border border-[#30363d] space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="text-[#8b949e]">From:</span>
                    <span className="font-mono text-[#e6edf3]">{selectedMessage.sender}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#8b949e]">To:</span>
                    <span className="font-mono text-blue-400">{selectedMessage.recipient}</span>
                  </div>
                  <div className="flex justify-between text-[11px] text-[#8b949e]">
                    <span>Message-ID:</span>
                    <span className="font-mono truncate max-w-xs">{selectedMessage.rfc_message_id}</span>
                  </div>
                </div>

                {/* Authentication Tags */}
                <div className="flex items-center gap-2 text-[10px] font-mono">
                  <span className="px-2 py-0.5 rounded border border-[#30363d] text-[#8b949e]">
                    SPF: <strong className={selectedMessage.spf_status === "pass" ? "text-[#2ea043]" : "text-[#f85149]"}>{selectedMessage.spf_status.toUpperCase()}</strong>
                  </span>
                  <span className="px-2 py-0.5 rounded border border-[#30363d] text-[#8b949e]">
                    DKIM: <strong className={selectedMessage.dkim_status === "pass" ? "text-[#2ea043]" : "text-[#f85149]"}>{selectedMessage.dkim_status.toUpperCase()}</strong>
                  </span>
                  <span className="px-2 py-0.5 rounded border border-[#30363d] text-[#8b949e]">
                    DMARC: <strong className={selectedMessage.dmarc_status === "pass" ? "text-[#2ea043]" : "text-[#f85149]"}>{selectedMessage.dmarc_status.toUpperCase()}</strong>
                  </span>
                </div>

                {isTrashed && (
                  <p role="status" className="rounded border border-[#30363d] bg-[#0d1117] p-2 text-[11px] text-[#d29922]">
                    This message is in the trash. Restore it to reply or forward. Trashed messages are removed for good after the retention period.
                  </p>
                )}

                <TriageBar
                  trashed={isTrashed}
                  scheduled={String(selectedMessage.status) === "scheduled"}
                  onTrash={() => handleTrash(selectedMessage.id)}
                  onRestore={() => handleRestore(selectedMessage.id)}
                  onDeleteForever={() => handleDeleteForever(selectedMessage.id)}
                />

                <LabelEditor
                  labels={(selectedMessage as unknown as TriageMessage).labels ?? []}
                  disabled={isTrashed}
                  onAdd={(label) => handleLabelChange(selectedMessage.id, { add_labels: [label] })}
                  onRemove={(label) => handleLabelChange(selectedMessage.id, { remove_labels: [label] })}
                />

                <div>
                  <button
                    type="button"
                    onClick={() => setShowHistory((open) => !open)}
                    aria-expanded={showHistory}
                    className="text-[11px] text-[#58a6ff] hover:underline"
                  >
                    {showHistory ? "Hide history" : "Show history"}
                  </button>
                  {showHistory && (
                    <div className="mt-2">
                      <LifecycleTimeline agentEmail={selectedAgentEmail} messageId={selectedMessage.id} />
                    </div>
                  )}
                </div>
              </div>

              {/* Conversation */}
              <div className="p-4 flex-1 overflow-y-auto space-y-3">
                {(threadMessages.length && !isTrashed ? threadMessages : [selectedMessage]).map((message) => {
                  const messageAttachments = threadAttachments[message.id] || [];
                  return (
                    <article key={message.id} className={`rounded border p-3 ${message.id === selectedMessage.id ? "border-blue-500/50 bg-[#161b22]" : "border-[#30363d] bg-[#0d1117]"}`}>
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[10px] text-[#8b949e]">
                        <span className="font-mono">
                          {message.direction === "inbound" ? message.sender : `Agent → ${message.recipient}`}
                        </span>
                        <span>{serverDate(message.created_at).toLocaleString()} · {String(message.status).replace("_", " ")}</span>
                      </div>
                      <div className="mb-2 text-xs font-semibold text-[#e6edf3]">{message.subject || "(No Subject)"}</div>
                      <div className="text-xs text-[#c9d1d9] whitespace-pre-wrap leading-relaxed font-sans">{message.body_text}</div>
                      {message.delivery_error && (
                        <div className="mt-3 rounded border border-[#f85149]/30 bg-[#f85149]/10 p-2 text-xs text-[#f85149]">
                          Delivery failed: {message.delivery_error}
                        </div>
                      )}
                      {message.provider_delivery_status && (
                        <div className={`mt-3 rounded border p-2 text-xs ${["bounced", "complained"].includes(message.provider_delivery_status) ? "border-[#f85149]/30 bg-[#f85149]/10 text-[#f85149]" : "border-[#30363d] bg-[#0d1117] text-[#8b949e]"}`}>
                          Provider delivery: {message.provider_delivery_status}
                          {message.provider_delivery_error ? ` · ${message.provider_delivery_error}` : ""}
                        </div>
                      )}
                      {messageAttachments.length > 0 && (
                        <AttachmentList attachments={messageAttachments} agentEmail={selectedAgentEmail} messageId={message.id} />
                      )}
                    </article>
                  );
                })}
              </div>

              {!isTrashed && <form onSubmit={handleHandoff} className="p-3 border-t border-[#30363d] bg-[#0d1117] space-y-2">
                <div className="text-[11px] text-[#e6edf3] flex items-center gap-1.5">
                  <Forward className="w-3 h-3 text-amber-400" /> Hand off to a person
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                  <input type="email" required value={handoffRecipient} onChange={(e) => setHandoffRecipient(e.target.value)} placeholder="Teammate email address" className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] placeholder-[#8b949e]" />
                  <input type="text" value={handoffNote} onChange={(e) => setHandoffNote(e.target.value)} placeholder="Context for your teammate (optional)" className="w-full bg-[#161b22] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] placeholder-[#8b949e]" />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[10px] text-[#8b949e]">Forwards the original email and your context. Agent approval settings still apply.</p>
                  <button type="submit" disabled={sendingHandoff || !handoffRecipient.trim()} className="shrink-0 px-3 py-1.5 rounded border border-[#30363d] bg-[#21262d] hover:bg-[#30363d] disabled:opacity-50 text-[#e6edf3] text-xs flex items-center gap-1.5">
                    <Forward className="w-3 h-3" /> {sendingHandoff ? "Forwarding…" : "Forward"}
                  </button>
                </div>
                {handoffResult && <p className="text-[11px] text-[#d29922]">{handoffResult}</p>}
              </form>}

              {/* In-Thread Reply */}
              {!isTrashed && <div className="p-3 border-t border-[#30363d] bg-[#161b22] space-y-2">
                <div className="text-[11px] text-[#8b949e] flex items-center justify-between">
                  <span className="flex items-center gap-1 text-[#e6edf3]">
                    <Reply className="w-3 h-3 text-blue-400" /> Reply in-thread
                  </span>
                  <span className="font-mono">
                    To: {selectedMessage.direction === "inbound" ? selectedMessage.sender : selectedMessage.recipient}
                  </span>
                </div>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                  <select
                    aria-label="Reply template"
                    value={replyTemplateId}
                    onChange={(event) => { setReplyTemplateId(event.target.value); setReplyTemplatePreview(null); setReplyTemplateError(""); }}
                    className="w-full rounded border border-[#30363d] bg-[#0d1117] p-2 text-xs text-[#e6edf3]"
                  >
                    <option value="">Use direct reply text</option>
                    {emailTemplates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
                  </select>
                  {replyTemplateId && <button type="button" onClick={() => void previewReplyTemplate()} className="rounded border border-[#30363d] px-3 py-2 text-xs text-[#e6edf3] hover:bg-[#21262d]">Preview template</button>}
                </div>
                {replyTemplateId && <div className="space-y-2">
                  <label className="block text-[10px] text-[#8b949e]">Template data (JSON object)</label>
                  <textarea rows={2} value={replyTemplateData} onChange={(event) => { setReplyTemplateData(event.target.value); setReplyTemplatePreview(null); setReplyTemplateError(""); }} className="w-full rounded border border-[#30363d] bg-[#0d1117] p-2 font-mono text-[11px] text-[#e6edf3]" />
                  {replyTemplateError && <p role="alert" className="text-[11px] text-red-400">{replyTemplateError}</p>}
                  {replyTemplatePreview && <div className="max-h-48 space-y-1 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2 text-[10px] text-[#c9d1d9]">
                    <p><span className="text-[#8b949e]">Subject:</span> {replyTemplatePreview.subject.toLowerCase().startsWith("re:") ? replyTemplatePreview.subject : `Re: ${replyTemplatePreview.subject}`}</p>
                    <p className="text-[#8b949e]">Rendered text:</p>
                    <pre className="whitespace-pre-wrap">{replyTemplatePreview.body_text}</pre>
                    {replyTemplatePreview.body_html && <><p className="text-[#8b949e]">Rendered HTML source:</p><pre className="whitespace-pre-wrap">{replyTemplatePreview.body_html}</pre></>}
                  </div>}
                </div>}
                {!replyTemplateId && <MessageSnippets inboxEmail={selectedAgentEmail} value={replyText} onApply={(body) => {
                  setReplyText(body);
                  if (replyDraftKey) {
                    try { window.localStorage.setItem(replyDraftKey, body); } catch { /* Draft remains available while this view is open. */ }
                  }
                }} />}
                <textarea
                  rows={3}
                  value={replyText}
                  disabled={Boolean(replyTemplateId)}
                  required={!replyTemplateId}
                  onChange={(e) => {
                    const value = e.target.value;
                    setReplyText(value);
                    if (replyDraftKey) {
                      try {
                        if (value) window.localStorage.setItem(replyDraftKey, value);
                        else window.localStorage.removeItem(replyDraftKey);
                      } catch { /* Draft remains available while this view is open. */ }
                    }
                  }}
                  placeholder="Type in-thread response..."
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2.5 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-zinc-500 resize-none disabled:opacity-50"
                />
                <ScheduleField value={replySchedule} onChange={setReplySchedule} />
                <div className="flex justify-end">
                  {replyText && !replyTemplateId && <span className="mr-auto self-center text-[10px] text-[#8b949e]">Draft saved on this device</span>}
                  <button
                    onClick={handleSendReply}
                    disabled={sendingReply || Boolean(scheduleProblem(replySchedule)) || (!replyTemplateId && !replyText.trim())}
                    className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white text-xs font-medium flex items-center gap-1.5 transition-colors"
                  >
                    <Send className="w-3 h-3" />
                    <span>{sendingReply ? "Sending..." : replySchedule ? "Schedule Reply" : "Send Reply"}</span>
                  </button>
                </div>
              </div>}
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-xs text-[#8b949e]">
              <Mail className="w-8 h-8 text-[#8b949e] mb-2" />
              <div>Select an email from the left list to read.</div>
            </div>
          )}
        </div>
      </div>

      {/* Compose Outbound Modal */}
      {isComposeOpen && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-[#161b22] border border-[#30363d] rounded-lg w-full max-w-lg p-5 space-y-3 shadow-xl">
            <div className="flex items-center justify-between pb-2 border-b border-[#30363d]">
              <h4 className="text-xs font-semibold text-[#e6edf3]">Compose Outbound Email</h4>
              <button
                onClick={() => setIsComposeOpen(false)}
                className="text-[#8b949e] hover:text-[#e6edf3] text-xs"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleComposeSend} className="space-y-3 text-xs">
              <div>
                <label className="text-[#8b949e] block mb-1">From Agent</label>
                <input
                  type="text"
                  disabled
                  value={selectedAgentEmail}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#8b949e] font-mono"
                />
              </div>

              <div>
                <label className="text-[#8b949e] block mb-1">Recipient</label>
                <input
                  type="email"
                  required
                  placeholder="recipient@example.com"
                  value={composeRecipient}
                  onChange={(e) => setComposeRecipient(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500"
                />
              </div>

              <div className="space-y-2">
                <label className="text-[#8b949e] block mb-1">Saved template (optional)</label>
                <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                  <select
                    aria-label="Compose template"
                    value={composeTemplateId}
                    onChange={(event) => { setComposeTemplateId(event.target.value); setComposeTemplatePreview(null); setComposeTemplateError(""); }}
                    className="w-full rounded border border-[#30363d] bg-[#0d1117] p-2 text-xs text-[#e6edf3]"
                  >
                    <option value="">Write direct content</option>
                    {emailTemplates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
                  </select>
                  {composeTemplateId && <button type="button" onClick={() => void previewComposeTemplate()} className="rounded border border-[#30363d] px-3 py-2 text-xs text-[#e6edf3] hover:bg-[#21262d]">Preview template</button>}
                </div>
                {composeTemplateId && <>
                  <label className="block text-[10px] text-[#8b949e]">Template data (JSON object)</label>
                  <textarea rows={2} value={composeTemplateData} onChange={(event) => { setComposeTemplateData(event.target.value); setComposeTemplatePreview(null); setComposeTemplateError(""); }} className="w-full rounded border border-[#30363d] bg-[#0d1117] p-2 font-mono text-[11px] text-[#e6edf3]" />
                  {composeTemplateError && <p role="alert" className="text-[11px] text-red-400">{composeTemplateError}</p>}
                  {composeTemplatePreview && <div className="max-h-48 space-y-1 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2 text-[10px] text-[#c9d1d9]">
                    <p><span className="text-[#8b949e]">Subject:</span> {composeTemplatePreview.subject}</p>
                    <p className="text-[#8b949e]">Rendered text:</p>
                    <pre className="whitespace-pre-wrap">{composeTemplatePreview.body_text}</pre>
                    {composeTemplatePreview.body_html && <><p className="text-[#8b949e]">Rendered HTML source:</p><pre className="whitespace-pre-wrap">{composeTemplatePreview.body_html}</pre></>}
                  </div>}
                </>}
              </div>

              <div>
                <label className="text-[#8b949e] block mb-1">Subject</label>
                <input
                  type="text"
                  required={!composeTemplateId}
                  disabled={Boolean(composeTemplateId)}
                  placeholder="Subject"
                  value={composeSubject}
                  onChange={(e) => setComposeSubject(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500 disabled:opacity-50"
                />
              </div>

              <div>
                <label className="text-[#8b949e] block mb-1">Body</label>
                {!composeTemplateId && <MessageSnippets inboxEmail={selectedAgentEmail} value={composeBody} onApply={setComposeBody} />}
                <textarea
                  rows={4}
                  required={!composeTemplateId}
                  disabled={Boolean(composeTemplateId)}
                  placeholder="Message body..."
                  value={composeBody}
                  onChange={(e) => setComposeBody(e.target.value)}
                  className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] focus:outline-none focus:border-zinc-500 resize-none font-sans disabled:opacity-50"
                />
              </div>

              <div>
                <label className="text-[#8b949e] block mb-1">Attachments (up to 10 files, 10 MiB total)</label>
                <input
                  type="file"
                  multiple
                  onChange={(event) => { void handleComposeFiles(event.target.files); event.target.value = ""; }}
                  className="w-full text-[11px] text-[#8b949e] file:mr-2 file:rounded file:border-0 file:bg-[#21262d] file:px-2 file:py-1 file:text-[#e6edf3]"
                />
                {composeAttachmentError && <p role="alert" className="mt-1 text-red-400">{composeAttachmentError}</p>}
                {composeAttachments.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {composeAttachments.map((attachment, index) => (
                      <li key={`${attachment.filename}-${index}`} className="flex items-center justify-between gap-2 text-[#8b949e]">
                        <span className="truncate">{attachment.filename}</span>
                        <button type="button" onClick={() => setComposeAttachments((items) => items.filter((_, itemIndex) => itemIndex !== index))} className="text-red-400 hover:text-red-300">Remove</button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <ScheduleField value={composeSchedule} onChange={setComposeSchedule} />

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setIsComposeOpen(false)}
                  className="px-3 py-1.5 rounded border border-[#30363d] text-[#8b949e] hover:text-[#e6edf3]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={sendingCompose || Boolean(scheduleProblem(composeSchedule))}
                  className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] disabled:opacity-50 text-white font-medium"
                >
                  {sendingCompose ? "Sending..." : composeSchedule ? "Schedule" : "Send"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
