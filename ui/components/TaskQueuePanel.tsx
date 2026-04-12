"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Hourglass,
  PauseCircle,
  Loader2,
  MessageSquarePlus,
  Monitor,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  X,
  Zap,
  Bug,
  Sparkles,
  ListChecks,
  FileText,
} from "lucide-react";
import type { QueueItem, SchedulerStatus } from "@/types";
import {
  addQueueTask,
  cancelQueueTask,
  clearAllQueue,
  clearDoneQueue,
  deleteQueueTask,
  fetchQueue,
  fetchSchedulerStatus,
  resumeQueueTask,
  runQueueTask,
  toggleScheduler,
  triggerSchedulerNow,
} from "@/lib/api";

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtRelative(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const abs = Math.abs(diff);
  if (abs < 60_000) return diff > 0 ? "just now" : "in <1m";
  if (abs < 3_600_000) {
    const m = Math.round(abs / 60_000);
    return diff > 0 ? `${m}m ago` : `in ${m}m`;
  }
  const h = Math.round(abs / 3_600_000);
  return diff > 0 ? `${h}h ago` : `in ${h}h`;
}

const STATUS_CONFIG: Record<
  string,
  { icon: React.ReactNode; label: string; color: string; bg: string }
> = {
  pending: {
    icon: <Clock className="h-3.5 w-3.5" />,
    label: "Pending",
    color: "text-amber-400",
    bg: "bg-amber-500/10 border-amber-500/30",
  },
  waiting: {
    icon: <Hourglass className="h-3.5 w-3.5" />,
    label: "Waiting",
    color: "text-purple-400",
    bg: "bg-purple-500/10 border-purple-500/30",
  },
  running: {
    icon: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
    label: "Running",
    color: "text-blue-400",
    bg: "bg-blue-500/10 border-blue-500/30",
  },
  done: {
    icon: <CheckCircle2 className="h-3.5 w-3.5" />,
    label: "Done",
    color: "text-emerald-400",
    bg: "bg-emerald-500/10 border-emerald-500/30",
  },
  failed: {
    icon: <AlertCircle className="h-3.5 w-3.5" />,
    label: "Failed",
    color: "text-red-400",
    bg: "bg-red-500/10 border-red-500/30",
  },
  blocked: {
    icon: <PauseCircle className="h-3.5 w-3.5" />,
    label: "Blocked",
    color: "text-orange-400",
    bg: "bg-orange-500/10 border-orange-500/30",
  },
  skipped: {
    icon: <X className="h-3.5 w-3.5" />,
    label: "Skipped",
    color: "text-muted-foreground",
    bg: "bg-muted/30 border-border",
  },
};

const SOURCE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  manual:  { icon: <Plus className="h-3 w-3" />,     label: "manual",  color: "text-primary" },
  audit:   { icon: <Bug className="h-3 w-3" />,      label: "audit",   color: "text-amber-400" },
  improve: { icon: <Sparkles className="h-3 w-3" />, label: "improve", color: "text-purple-400" },
};

function PriorityDot({ p }: { p: number }) {
  const color =
    p >= 9 ? "bg-red-500" : p >= 7 ? "bg-amber-500" : p >= 5 ? "bg-blue-500" : "bg-muted-foreground";
  return (
    <span
      className={`inline-block h-1.5 w-1.5 rounded-full ${color}`}
      title={`Priority ${p}`}
    />
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function TaskQueuePanel({ onPreview }: { onPreview?: (branch: string) => void }) {
  const [items, setItems]     = useState<QueueItem[]>([]);
  const [sched, setSched]     = useState<SchedulerStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  const [taskInput, setTaskInput]   = useState("");
  const [priority, setPriority]     = useState(5);
  const [adding, setAdding]         = useState(false);
  const [running, setRunning]       = useState<number | null>(null);
  const [cancelling, setCancelling] = useState<number | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [clearing, setClearing]     = useState(false);
  const [replyingTo, setReplyingTo] = useState<number | null>(null);
  const [replyLog, setReplyLog]     = useState("");
  const [replying, setReplying]     = useState(false);
  const [detailItem, setDetailItem] = useState<QueueItem | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [q, s] = await Promise.all([fetchQueue(), fetchSchedulerStatus()]);
      setItems(q);
      setSched(s);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    intervalRef.current = setInterval(refresh, 5000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [refresh]);

  useEffect(() => {
    if (!detailItem) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetailItem(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [detailItem]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = taskInput.trim();
    if (!t) return;
    setAdding(true);
    try {
      await addQueueTask(t, priority);
      setTaskInput("");
      setPriority(5);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add task");
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteQueueTask(id);
      setItems((prev) => prev.filter((i) => i.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  const handleCancel = async (id: number) => {
    setCancelling(id);
    try {
      await cancelQueueTask(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to cancel task");
    } finally {
      setCancelling(null);
    }
  };

  const handleRun = async (id: number) => {
    setRunning(id);
    try {
      await runQueueTask(id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start task");
    } finally {
      setRunning(null);
    }
  };

  const handleClearAll = async () => {
    if (!confirm("Remove ALL tasks from queue (except running)?\nThis cannot be undone.")) return;
    setClearing(true);
    try {
      await clearAllQueue();
      await refresh();
    } finally {
      setClearing(false);
    }
  };

  const handleReply = async (item: QueueItem) => {
    const log = replyLog.trim();
    setReplying(true);
    try {
      await resumeQueueTask(item.id, log);
      setReplyingTo(null);
      setReplyLog("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to resume task");
    } finally {
      setReplying(false);
    }
  };

  const handleClearDone = async () => {
    setClearing(true);
    try {
      await clearDoneQueue();
      await refresh();
    } finally {
      setClearing(false);
    }
  };

  const handleToggle = async () => {
    try {
      await toggleScheduler();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to toggle scheduler");
    }
  };

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await triggerSchedulerNow();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to trigger scheduler");
    } finally {
      setTriggering(false);
    }
  };

  const pendingCount = items.filter((i) => i.status === "pending").length;
  const waitingCount = items.filter((i) => i.status === "waiting").length;
  const runningCount = items.filter((i) => i.status === "running").length;
  const done    = items.filter((i) => i.status === "done").length;
  const blocked = items.filter((i) => i.status === "blocked").length;
  const failed  = items.filter((i) => i.status === "failed").length;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      <div className="mx-auto w-full max-w-3xl space-y-4">

        {/* ── Header ── */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" />
            <h2 className="text-sm font-semibold text-foreground">Task Queue</h2>
            {runningCount > 0 && (
              <span className="rounded-full bg-blue-500/20 border border-blue-500/30 px-2 py-0.5 text-[10px] text-blue-400 animate-pulse">
                {runningCount} running
              </span>
            )}
            {waitingCount > 0 && (
              <span className="rounded-full bg-purple-500/20 border border-purple-500/30 px-2 py-0.5 text-[10px] text-purple-400">
                {waitingCount} waiting
              </span>
            )}
            {pendingCount > 0 && (
              <span className="rounded-full bg-amber-500/20 border border-amber-500/30 px-2 py-0.5 text-[10px] text-amber-400">
                {pendingCount} pending
              </span>
            )}
          </div>
          <button
            onClick={refresh}
            className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className="h-3 w-3" />
            Refresh
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
            {error}
          </div>
        )}

        {/* ── Scheduler card ── */}
        <div className="rounded-lg border border-border bg-card/50 p-4">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold text-foreground">Auto Scheduler</span>
              {sched && (
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] border ${
                    sched.enabled
                      ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
                      : "bg-muted/30 border-border text-muted-foreground"
                  }`}
                >
                  {sched.enabled ? "enabled" : "disabled"}
                </span>
              )}
              {sched?.running && (
                <span className="flex items-center gap-1 rounded-full bg-blue-500/20 border border-blue-500/30 px-2 py-0.5 text-[10px] text-blue-400">
                  <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  scanning...
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 self-end sm:self-auto">
              <button
                onClick={handleTrigger}
                disabled={triggering || sched?.running}
                className="flex items-center gap-1 rounded-md border border-border bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground hover:text-purple-400 hover:border-purple-500/40 hover:bg-purple-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                title="Run audit+improve now and add tasks to queue"
              >
                {triggering ? <Loader2 className="h-3 w-3 animate-spin" /> : <Zap className="h-3 w-3" />}
                Scan Now
              </button>
              <button
                onClick={handleToggle}
                className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                  sched?.enabled
                    ? "border-border bg-muted/40 text-emerald-300 hover:bg-emerald-500/10"
                    : "border-border bg-muted/30 text-muted-foreground hover:text-foreground"
                }`}
              >
                {sched?.enabled ? "Disable" : "Enable"}
              </button>
            </div>
          </div>
          {sched && (
            <div className="grid grid-cols-3 gap-3 text-[11px]">
              <div>
                <p className="text-muted-foreground">Interval</p>
                <p className="font-medium text-foreground">{sched.interval_hours}h</p>
              </div>
              <div>
                <p className="text-muted-foreground">Last run</p>
                <p className="font-medium text-foreground">{fmtRelative(sched.last_run)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Next run</p>
                <p className="font-medium text-foreground">{fmtRelative(sched.next_run)}</p>
              </div>
            </div>
          )}
        </div>

        {/* ── Add task form ── */}
        <form
          onSubmit={handleAdd}
          className="rounded-lg border border-border bg-card/50 p-4 space-y-3"
        >
          <p className="text-xs font-semibold text-foreground">Add Task to Queue</p>
          <textarea
            value={taskInput}
            onChange={(e) => setTaskInput(e.target.value)}
            placeholder="Describe what to build or fix… (e.g. 'Add daily login reward with streak counter')"
            rows={2}
            className="w-full resize-none rounded-md border border-border bg-muted/30 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
          />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              Priority
              <select
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                className="rounded-md border border-border bg-muted/30 px-2 py-0.5 text-xs text-foreground focus:outline-none"
              >
                {[10, 9, 8, 7, 6, 5, 4, 3, 2, 1].map((p) => (
                  <option key={p} value={p}>
                    {p} {p >= 9 ? "🔴 critical" : p >= 7 ? "🟠 high" : p >= 5 ? "🔵 normal" : "⚪ low"}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={adding || !taskInput.trim()}
              className="ml-auto flex items-center gap-1.5 rounded-md bg-primary/90 px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {adding ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
              Add to Queue
            </button>
          </div>
        </form>

        {/* ── Stats (between Add Task and Queue) ── */}
        <div className="grid grid-cols-6 gap-2 text-center">
          {[
            { label: "Pending",  val: pendingCount,  color: "text-amber-400" },
            { label: "Waiting",  val: waitingCount,  color: "text-purple-400" },
            { label: "Running",  val: runningCount,  color: "text-blue-400" },
            { label: "Done",     val: done,          color: "text-emerald-400" },
            { label: "Blocked",  val: blocked,       color: "text-orange-400" },
            { label: "Failed",   val: failed,        color: "text-red-400" },
          ].map(({ label, val, color }) => (
            <div key={label} className="rounded-lg border border-border bg-card/30 p-2">
              <p className={`text-lg font-bold ${color}`}>{val}</p>
              <p className="text-[10px] text-muted-foreground">{label}</p>
            </div>
          ))}
        </div>

        {/* ── Queue list ── */}
        <div className="rounded-lg border border-border bg-card/50">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <span className="text-xs font-semibold text-foreground">
              Queue ({items.length} items)
            </span>
            {(done > 0 || blocked > 0 || failed > 0) && (
              <button
                onClick={handleClearDone}
                disabled={clearing}
                className="flex items-center gap-1 text-[1px] text-muted-foreground hover:text-red-400 transition-colors"
              >
                {clearing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
                Clear
              </button>
            )}
            <button
              onClick={handleClearAll}
              disabled={clearing}
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-red-400 transition-colors ml-3"
              title="Remove all non-running tasks"
            >
              {clearing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
              Clear all
            </button>
          </div>

          {loading ? (
            <div className="space-y-2 p-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-md bg-muted" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-10 text-center">
              <ListChecks className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-xs text-muted-foreground">Queue is empty</p>
              <p className="text-[11px] text-muted-foreground/60">
                Add tasks manually or run a scheduler scan
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => {
                const s = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.skipped;
                const src = SOURCE_CONFIG[item.source] ?? { icon: <Play className="h-3 w-3" />, label: item.source, color: "text-muted-foreground" };
                const taskPreview = (item.task || "").split("\n").find((line) => line.trim().length > 0)?.trim() || item.task;

                return (
                  <React.Fragment key={item.id}>
                  <li className="flex items-start gap-3 px-4 py-3">
                    {/* Status icon */}
                    <div className={`mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md border ${s.bg} ${s.color}`}>
                      {s.icon}
                    </div>

                    {/* Task body */}
                    <div className="min-w-0 flex-1">
                      <button
                        onClick={() => setDetailItem(item)}
                        className="w-full text-left"
                        title="View full task details"
                      >
                        <p className="line-clamp-2 text-xs font-medium text-foreground hover:text-primary transition-colors">
                          {taskPreview}
                        </p>
                      </button>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        {/* Source badge */}
                        <span className={`flex items-center gap-1 text-[10px] ${src.color}`}>
                          {src.icon}
                          {src.label}
                        </span>
                        {/* Priority dot */}
                        <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                          <PriorityDot p={item.priority} />
                          p{item.priority}
                        </span>
                        {/* Session link */}
                        {item.session_id && (
                          <span className="font-mono text-[10px] text-muted-foreground">
                            #{item.session_id.slice(0, 8)}
                          </span>
                        )}
                        {/* Timestamp */}
                        <span className="text-[10px] text-muted-foreground/60">
                          {fmtRelative(item.created_at)}
                        </span>
                      </div>
                    </div>

                    {/* Action buttons: Run (pending) | Stop (running) | X (waiting/others) */}
                    {item.status === "pending" ? (
                      <div className="mt-0.5 flex flex-shrink-0 items-center gap-1">
                        <button
                          onClick={() => handleRun(item.id)}
                          disabled={running === item.id}
                          className="flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-400 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
                          title="Start this task now"
                        >
                          {running === item.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Play className="h-3 w-3 fill-current" />
                          )}
                          Run
                        </button>
                        <button
                          onClick={() => setDetailItem(item)}
                          className="flex items-center gap-1 rounded border border-border bg-muted/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/10 transition-colors"
                          title="View full task details"
                        >
                          <FileText className="h-3 w-3" />
                          Details
                        </button>
                        <button
                          onClick={() => handleDelete(item.id)}
                          className="rounded p-1 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Remove from queue"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : item.status === "waiting" ? (
                      <div className="mt-0.5 flex flex-shrink-0 items-center gap-1">
                        <span className="text-[10px] text-purple-400/70 italic">auto</span>
                        <button
                          onClick={() => setDetailItem(item)}
                          className="flex items-center gap-1 rounded border border-border bg-muted/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/10 transition-colors"
                          title="View full task details"
                        >
                          <FileText className="h-3 w-3" />
                          Details
                        </button>
                        <button
                          onClick={() => handleDelete(item.id)}
                          className="rounded p-1 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Remove from queue"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : item.status === "running" ? (
                      <div className="mt-0.5 flex flex-shrink-0 items-center gap-1">
                        <button
                          onClick={() => handleCancel(item.id)}
                          disabled={cancelling === item.id}
                          className="flex items-center gap-1 rounded border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-400 hover:bg-red-500/20 disabled:opacity-50 transition-colors"
                          title="Stop this task after current subtask"
                        >
                          {cancelling === item.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Square className="h-2.5 w-2.5 fill-current" />
                          )}
                          Stop
                        </button>
                        <button
                          onClick={() => setDetailItem(item)}
                          className="flex items-center gap-1 rounded border border-border bg-muted/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/10 transition-colors"
                          title="View full task details"
                        >
                          <FileText className="h-3 w-3" />
                          Details
                        </button>
                      </div>
                    ) : (
                      <div className="mt-0.5 flex flex-shrink-0 items-center gap-1">
                        {onPreview && item.status === "done" && (
                          <button
                            onClick={() => onPreview(item.branch ?? "")}
                            className="flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-400 hover:bg-emerald-500/20 transition-colors"
                            title="Preview game from this branch"
                          >
                            <Monitor className="h-3 w-3" />
                            Preview
                          </button>
                        )}
                        {(item.status === "failed" || item.status === "blocked") && (
                          <button
                            onClick={() => {
                              setReplyingTo(replyingTo === item.id ? null : item.id);
                              setReplyLog("");
                            }}
                            className={`flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-medium transition-colors ${
                              replyingTo === item.id
                                ? "border-primary/40 bg-primary/10 text-primary"
                                : "border-border bg-muted/30 text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/10"
                            }`}
                            title="Resume this task with notes"
                          >
                            <MessageSquarePlus className="h-3 w-3" />
                            Resume
                          </button>
                        )}
                        <button
                          onClick={() => setDetailItem(item)}
                          className="flex items-center gap-1 rounded border border-border bg-muted/30 px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-primary hover:border-primary/40 hover:bg-primary/10 transition-colors"
                          title="View full task details"
                        >
                          <FileText className="h-3 w-3" />
                          Details
                        </button>
                        <button
                          onClick={() => handleDelete(item.id)}
                          className="rounded p-1 text-muted-foreground/50 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                          title="Remove from queue"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </li>
                  {/* ── Inline reply panel ── */}
                  {replyingTo === item.id && (
                    <li className="border-t border-primary/20 bg-primary/5 px-4 py-3">
                      <p className="mb-2 text-[11px] text-primary font-medium">Paste error or log — this same task will be resumed:</p>
                      <textarea
                        autoFocus
                        value={replyLog}
                        onChange={(e) => setReplyLog(e.target.value)}
                        placeholder="Paste error message, stack trace, or log output here…"
                        rows={4}
                        className="w-full resize-none rounded-md border border-primary/30 bg-muted/40 px-3 py-2 font-mono text-[11px] text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/50"
                      />
                      <div className="mt-2 flex items-center gap-2">
                        <button
                          onClick={() => handleReply(item)}
                          disabled={replying}
                          className="flex items-center gap-1.5 rounded-md bg-primary/90 px-3 py-1.5 text-[11px] font-medium text-primary-foreground hover:bg-primary disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          {replying ? <Loader2 className="h-3 w-3 animate-spin" /> : <MessageSquarePlus className="h-3 w-3" />}
                          Resume this task
                        </button>
                        <button
                          onClick={() => { setReplyingTo(null); setReplyLog(""); }}
                          className="text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    </li>
                  )}
                </React.Fragment>
              );
            })}
            </ul>
          )}
        </div>

        {detailItem && (
          <div
            className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-3 sm:items-center sm:p-6"
            onClick={() => setDetailItem(null)}
          >
            <div
              className="w-full max-w-2xl rounded-xl border border-border bg-background shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div className="min-w-0">
                  <p className="text-[11px] uppercase tracking-wide text-muted-foreground">Task details</p>
                  <p className="truncate text-sm font-semibold text-foreground">Queue item #{detailItem.id}</p>
                </div>
                <button
                  onClick={() => setDetailItem(null)}
                  className="rounded p-1 text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors"
                  title="Close"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className="rounded border border-border bg-muted/30 px-2 py-0.5 text-muted-foreground">{detailItem.status}</span>
                  <span className="rounded border border-border bg-muted/30 px-2 py-0.5 text-muted-foreground">{detailItem.source}</span>
                  <span className="rounded border border-border bg-muted/30 px-2 py-0.5 text-muted-foreground">p{detailItem.priority}</span>
                  <span className="text-muted-foreground/70">created {fmtRelative(detailItem.created_at)}</span>
                </div>

                {(detailItem.source === "audit" || detailItem.source === "improve") && (
                  <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-200">
                    Audit-generated task. Review implementation and acceptance criteria before running.
                  </div>
                )}

                <div className="rounded-md border border-border bg-muted/20 p-3">
                  <p className="mb-2 text-[11px] font-medium text-muted-foreground">Full task content</p>
                  <pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-foreground">
                    {detailItem.task}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── Stats footer (removed — moved above) */}
      </div>
    </div>
  );
}
