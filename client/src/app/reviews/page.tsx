"use client";

import { useEffect, useState } from "react";
import {
  ShieldAlert,
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw
} from "lucide-react";
import { api } from "@/lib/api";
import { ReviewItem } from "@/lib/types";

export default function ReviewsPage() {
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [selectedReview, setSelectedReview] = useState<ReviewItem | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("pending");
  const [reviewerNotes, setReviewerNotes] = useState<string>("");
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  const loadReviews = async () => {
    try {
      setLoading(true);
      const items = await api.getReviews(statusFilter || undefined);
      setReviews(items);
      if (items.length > 0) {
        setSelectedReview(items[0]);
      } else {
        setSelectedReview(null);
      }
    } catch (err) {
      console.error("Failed to fetch reviews:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadReviews();
  }, [statusFilter]);

  const handleApprove = async () => {
    if (!selectedReview) return;
    try {
      setActionLoading(true);
      const updated = await api.approveReview(
        selectedReview.id,
        reviewerNotes || "Approved by supervisor"
      );
      setReviewerNotes("");
      await loadReviews();
      setSelectedReview(updated);
    } catch (err: any) {
      alert(`Approval error: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async () => {
    if (!selectedReview) return;
    try {
      setActionLoading(true);
      const updated = await api.rejectReview(
        selectedReview.id,
        reviewerNotes || "Quarantined"
      );
      setReviewerNotes("");
      await loadReviews();
      setSelectedReview(updated);
    } catch (err: any) {
      alert(`Rejection error: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Top Bar */}
      <div className="card-simple p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[#e6edf3]">
            Human-in-the-Loop Review Queue
          </h2>
          <p className="text-xs text-[#8b949e]">
            Messages held for supervisor clearance before delivery to AI agents.
          </p>
        </div>

        {/* Status Tabs */}
        <div className="flex items-center gap-1 bg-[#0d1117] p-1 rounded border border-[#30363d] text-xs">
          <button
            onClick={() => setStatusFilter("pending")}
            className={`px-3 py-1 rounded transition-colors ${
              statusFilter === "pending"
                ? "bg-[#21262d] text-[#e6edf3] font-medium"
                : "text-[#8b949e] hover:text-[#e6edf3]"
            }`}
          >
            Pending
          </button>
          <button
            onClick={() => setStatusFilter("approved")}
            className={`px-3 py-1 rounded transition-colors ${
              statusFilter === "approved"
                ? "bg-[#21262d] text-[#e6edf3] font-medium"
                : "text-[#8b949e] hover:text-[#e6edf3]"
            }`}
          >
            Approved
          </button>
          <button
            onClick={() => setStatusFilter("rejected")}
            className={`px-3 py-1 rounded transition-colors ${
              statusFilter === "rejected"
                ? "bg-[#21262d] text-[#e6edf3] font-medium"
                : "text-[#8b949e] hover:text-[#e6edf3]"
            }`}
          >
            Quarantined
          </button>
        </div>
      </div>

      {/* Main Review Workbench */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 min-h-[580px]">
        {/* Left: Queue List */}
        <div className="lg:col-span-5 card-simple flex flex-col overflow-hidden">
          <div className="p-3 border-b border-[#30363d] flex items-center justify-between text-xs text-[#8b949e]">
            <span className="font-medium text-[#e6edf3]">Items ({reviews.length})</span>
            <button onClick={loadReviews} className="flex items-center gap-1 hover:text-[#e6edf3]">
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-[#30363d]">
            {reviews.length === 0 ? (
              <div className="p-10 text-center text-xs text-[#8b949e]">
                No items in this queue.
              </div>
            ) : (
              reviews.map((rev) => {
                const isSelected = selectedReview?.id === rev.id;
                return (
                  <div
                    key={rev.id}
                    onClick={() => setSelectedReview(rev)}
                    className={`p-3 cursor-pointer text-xs transition-colors ${
                      isSelected
                        ? "bg-[#21262d] border-l-2 border-amber-400"
                        : "hover:bg-[#1c2128]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="font-medium text-[#e6edf3] truncate">
                        {rev.agent_email}
                      </span>
                      <span className="text-[10px] font-mono text-[#8b949e]">
                        {new Date(rev.created_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>

                    <div className="text-[#e6edf3] font-medium truncate mb-1">
                      {rev.message?.subject || "Message Review"}
                    </div>

                    <p className="text-[11px] text-[#8b949e] line-clamp-2">
                      {rev.reason}
                    </p>

                    <div className="flex items-center gap-2 mt-2">
                      <span className="text-[10px] font-mono px-1.5 py-0.2 rounded border border-[#30363d] text-[#8b949e] uppercase">
                        {rev.status}
                      </span>
                      <span className="text-[10px] font-mono text-[#d29922]">
                        Score {Math.round(rev.threat_score * 100)}%
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Message Details & Actions */}
        <div className="lg:col-span-7 card-simple flex flex-col overflow-hidden">
          {selectedReview ? (
            <div className="flex-1 flex flex-col h-full">
              {/* Header */}
              <div className="p-4 border-b border-[#30363d] space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono text-[#8b949e]">
                    ID: {selectedReview.id}
                  </span>
                  <span className="text-xs font-mono font-medium text-[#d29922]">
                    Threat Score: {Math.round(selectedReview.threat_score * 100)}%
                  </span>
                </div>
                <h3 className="text-sm font-semibold text-[#e6edf3]">
                  {selectedReview.message?.subject || "(No Subject)"}
                </h3>
                <div className="bg-[#0d1117] p-2.5 rounded border border-[#30363d] text-xs text-[#8b949e] space-y-0.5">
                  <div>Sender: <span className="text-[#e6edf3] font-mono">{selectedReview.message?.sender}</span></div>
                  <div>Target Agent: <span className="text-blue-400 font-mono">{selectedReview.agent_email}</span></div>
                  <div>Reason: <span className="text-[#d29922]">{selectedReview.reason}</span></div>
                </div>
              </div>

              {/* Raw Body Content */}
              <div className="p-4 flex-1 overflow-y-auto space-y-2">
                <div className="text-[11px] font-medium text-[#8b949e]">Original Email Content:</div>
                <div className="p-3 rounded bg-[#0d1117] border border-[#30363d] font-mono text-xs text-[#e6edf3] whitespace-pre-wrap leading-relaxed select-text">
                  {selectedReview.message?.body_text}
                </div>
              </div>

              {/* Action Buttons */}
              <div className="p-3 border-t border-[#30363d] bg-[#161b22] space-y-2">
                {selectedReview.status === "pending" ? (
                  <>
                    <input
                      type="text"
                      placeholder="Supervisor notes..."
                      value={reviewerNotes}
                      onChange={(e) => setReviewerNotes(e.target.value)}
                      className="w-full bg-[#0d1117] border border-[#30363d] rounded p-2 text-xs text-[#e6edf3] placeholder-[#8b949e] focus:outline-none focus:border-zinc-500 font-sans"
                    />
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={handleReject}
                        disabled={actionLoading}
                        className="px-3 py-1.5 rounded border border-[#da3633]/40 bg-[#da3633]/10 hover:bg-[#da3633]/20 text-[#f85149] text-xs font-medium flex items-center gap-1 transition-colors"
                      >
                        <XCircle className="w-3.5 h-3.5" />
                        <span>Reject & Quarantine</span>
                      </button>

                      <button
                        onClick={handleApprove}
                        disabled={actionLoading}
                        className="px-3 py-1.5 rounded bg-[#238636] hover:bg-[#2ea043] text-white text-xs font-medium flex items-center gap-1 transition-colors"
                      >
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        <span>Approve & Deliver</span>
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="text-xs text-[#8b949e] flex justify-between">
                    <span>Status: <strong className="text-[#e6edf3] capitalize">{selectedReview.status}</strong></span>
                    <span>Resolved</span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-xs text-[#8b949e]">
              <ShieldAlert className="w-8 h-8 text-[#8b949e] mb-2" />
              <div>Select an item to inspect.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
