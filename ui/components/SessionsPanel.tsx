"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, GitPullRequest, FileCode, ChevronDown, ChevronRight, CheckCircle2, XCircle, Clock, Loader2 } from "lucide-react";
import { fetchSessions } from "@/lib/api";
import type { SessionSummary, SubtaskInfo } from "@/types";

const AGENT_COLORS: Record<string, string> = {
  tech_expert: "#a78bfa", dev: "#34d399", qa: "#60a5fa",
  git: "#f59e0b", notifier: "#fb923c",
};

const STATUS_STYLES: Record<string, string> = {
  running:  "bg-primary/20 text-primary animate-pulse-dot",
  starting: "bg-primary/20 text-primary animate-pulse-dot",
  stopping: "bg-orange-500/20 text-orange-400",
  done:     "bg-emerald-500/20 text-emerald-400",
  error:    "bg-red-500/20 text-red-400",
  audit:    "bg-amber-500/20 text-amber-400",
  improve:  "bg-purple-500/20 text-purple-400",
};
const TYPE_ICON: Record<string, string> = { game: "🎮", audit: "🔍", improve: "✨" };

const VND_RATE = 25_000;
function fmtCost(usd: number): string {
  if (!usd) return "";
  const vnd = usd * VND_RATE;
  if (vnd >= 1_000) return `${(vnd / 1_000).toFixed(0)}K ₫`;
  return `${Math.round(vnd)} ₫`;
}
function fmtUsd(v: number): string {
  if (v < 0.001) return `$${(v * 1000).toFixed(3)}m`;
  return `$${v.toFixed(4)}`;
}

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)   return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function SubtaskStatusIcon({ status }: { status: string }) {
  if (status === "done")         return <CheckCircle2 className="h-3 w-3 text-emerald-400 shrink-0" />;
  if (status === "failed")       return <XCircle className="h-3 w-3 text-red-400 shrink-0" />;
  if (status === "in_progress")  return <Loader2 className="h-3 w-3 text-primary shrink-0 animate-spin" />;
  if (status === "qa_review")    return <Loader2 className="h-3 w-3 text-blue-400 shrink-0 animate-spin" />;
  return <Clock className="h-3 w-3 text-muted-foreground/50 shrink-0" />;
}

function SubtaskList({ subtasks }: { subtasks: SubtaskInfo[] }) {
  if (!subtasks || subtasks.length === 0) return null;
  const done    = subtasks.filter(s => s.status === "done").length;
  const failed  = subtasks.filter(s => s.status === "failed").length;
  return (
    <div className="mt-2 space-y-1">
      <div className="text-[10px] text-muted-foreground mb-1.5 flex items-center gap-2">
        <span>{subtasks.length} subtask{subtasks.length !== 1 ? "s" : ""}</span>
        {done > 0    && <span className="text-emerald-400">✓ {done} done</span>}
        {failed > 0  && <span className="text-red-400">✗ {failed} failed</span>}
      </div>
      {subtasks.map((st) => (
        <div key={st.id} className="flex items-start gap-2 rounded px-2 py-1.5 bg-muted/20 border border-border/30">
          <SubtaskStatusIcon status={st.status} />
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <p className="text-[11px] text-foreground leading-snug line-clamp-2">{st.description}</p>
              <div className="flex items-center gap-1.5 shrink-0">
                {st.revision_count > 0 && (
                  <span className="text-[9px] text-muted-foreground/70 font-mono">rev×{st.revision_count}</span>
                )}
                {st.qa_passed === true  && <span className="text-[9px] text-emerald-400 border border-emerald-500/30 rounded px-1 py-0.5">QA ✓</span>}
                {st.qa_passed === false && <span className="text-[9px] text-red-400 border border-red-500/30 rounded px-1 py-0.5">QA ✗</span>}
              </div>
            </div>
            {st.files_to_touch.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-0.5">
                {st.files_to_touch.slice(0, 3).map((f) => (
                  <span key={f} className="text-[9px] font-mono text-muted-foreground/60 bg-muted/30 rounded px-1">
                    {f.split("/").slice(-1)[0]}
                  </span>
                ))}
                {st.files_to_touch.length > 3 && (
                  <span className="text-[9px] text-muted-foreground/40">+{st.files_to_touch.length - 3}</span>
                )}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentBadges({ subtasks }: { subtasks?: SubtaskInfo[] }) {
  if (!subtasks || subtasks.length === 0) return null;
  const agents = ["dev", "qa", "tech_expert"];
  // Show agents that have at least one associated subtask (any status)
  const activeAgents = subtasks.length > 0 ? agents : [];
  if (activeAgents.length === 0) return null;
  return (
    <div className="flex items-center gap-1">
      {activeAgents.map(a => (
        <div key={a} className="h-2 w-2 rounded-full" style={{ backgroundColor: AGENT_COLORS[a] ?? "#94a3b8" }} title={a} />
      ))}
    </div>
  );
}

export function SessionsPanel({ fullscreen = false }: { fullscreen?: boolean }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading]   = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const refresh = useCallback(() => {
    setLoading(true);
    fetchSessions()
      .then(setSessions)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    const hasActive = sessions.some(s => s.status === "running" || s.status === "starting");
    if (!hasActive) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [sessions, refresh]);

  const toggle = (id: string) => setExpanded(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  if (sessions.length === 0 && !loading) {
    return <p className="px-1 text-[11px] text-muted-foreground italic">No sessions yet.</p>;
  }

  return (
    <div className="space-y-1.5">
      <div className="mb-1 flex items-center justify-between px-1">
        <span className="text-[10px] text-muted-foreground">{sessions.length} session{sessions.length !== 1 ? "s" : ""}</span>
        <button onClick={refresh} className="rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors" title="Refresh">
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {sessions.map((s) => {
        const isExpanded = expanded.has(s.session_id);
        const hasSubtasks = (s.subtasks?.length ?? 0) > 0;
        const doneCount = s.subtasks?.filter(st => st.status === "done").length ?? 0;
        const totalCount = s.subtasks?.length ?? 0;

        return (
          <div key={s.session_id} className={`rounded-md border border-border/50 bg-muted/20 hover:bg-muted/30 transition-colors ${fullscreen ? "px-4 py-3" : "px-2.5 py-2"}`}>
            {/* Top row */}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 min-w-0">
                {hasSubtasks && (
                  <button onClick={() => toggle(s.session_id)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                    {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                  </button>
                )}
                <span className={`font-mono text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
                  {TYPE_ICON[s.pipeline_type] ?? "🤖"} {s.session_id}
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {totalCount > 0 && (
                  <span className={`font-mono text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[9px]"}`}>
                    {doneCount}/{totalCount}
                  </span>
                )}
                <AgentBadges subtasks={s.subtasks} />
                <span className={`rounded-full px-1.5 py-px font-medium ${fullscreen ? "text-[10px]" : "text-[9px]"} ${STATUS_STYLES[s.status] ?? "bg-muted text-muted-foreground"}`}>
                  {s.status}
                </span>
              </div>
            </div>

            {/* Task description */}
            <p className={`mt-0.5 text-foreground leading-snug ${fullscreen ? "text-sm" : "text-[11px] line-clamp-2"}`}>
              {s.task}
            </p>

            {/* Bottom row */}
            <div className="flex items-center gap-3 pt-1">
              <span className={`text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>{timeAgo(s.created_at)}</span>
              {s.files_count > 0 && (
                <span className={`flex items-center gap-0.5 text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
                  <FileCode className="h-3 w-3" />
                  {s.files_count} file{s.files_count !== 1 ? "s" : ""}
                </span>
              )}
              {s.cost_usd != null && s.cost_usd > 0 && (
                <span className={`font-mono flex items-center gap-1 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
                  <span className="text-emerald-400">{fmtUsd(s.cost_usd)}</span>
                  <span className="text-amber-400 text-[9px]">{fmtCost(s.cost_usd)}</span>
                </span>
              )}
              {s.pr_url && (
                <a href={s.pr_url} target="_blank" rel="noopener noreferrer" className={`flex items-center gap-1 text-emerald-400 hover:text-emerald-300 transition-colors ${fullscreen ? "text-xs" : "text-[10px]"}`}>
                  <GitPullRequest className="h-3 w-3" />
                  PR
                </a>
              )}
            </div>

            {/* Subtask list (expanded) */}
            {isExpanded && hasSubtasks && (
              <SubtaskList subtasks={s.subtasks!} />
            )}
          </div>
        );
      })}
    </div>
  );
}
