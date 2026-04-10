"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, GitPullRequest, FileCode } from "lucide-react";
import { fetchSessions } from "@/lib/api";
import type { SessionSummary } from "@/types";

const STATUS_STYLES: Record<string, string> = {
  running:  "bg-primary/20 text-primary animate-pulse-dot",
  starting: "bg-primary/20 text-primary animate-pulse-dot",
  done:     "bg-emerald-500/20 text-emerald-400",
  error:    "bg-red-500/20 text-red-400",
  audit:    "bg-amber-500/20 text-amber-400",
  improve:  "bg-purple-500/20 text-purple-400",
};

const TYPE_ICON: Record<string, string> = {
  game:    "🎮",
  audit:   "🔍",
  improve: "✨",
};

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60)   return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

export function SessionsPanel({ fullscreen = false }: { fullscreen?: boolean }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchSessions()
      .then(setSessions)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Initial load + auto-refresh every 5s when there are active sessions
  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const hasActive = sessions.some(
      (s) => s.status === "running" || s.status === "starting",
    );
    if (!hasActive) return;
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [sessions, refresh]);

  if (sessions.length === 0 && !loading) {
    return (
      <p className="px-1 text-[11px] text-muted-foreground italic">
        No sessions yet.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <div className="mb-1 flex items-center justify-between px-1">
        <span className="text-[10px] text-muted-foreground">
          {sessions.length} session{sessions.length !== 1 ? "s" : ""}
        </span>
        <button
          onClick={refresh}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground transition-colors"
          title="Refresh"
        >
          <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {sessions.map((s) => (
        <div
          key={s.session_id}
          className={`flex flex-col gap-1 rounded-md border border-border/50 bg-muted/20 hover:bg-muted/40 transition-colors ${
            fullscreen ? "px-4 py-3" : "px-2.5 py-2"
          }`}
        >
          {/* Top row */}
          <div className="flex items-center justify-between gap-2">
            <span className={`font-mono text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
              {TYPE_ICON[s.pipeline_type] ?? "🤖"} {s.session_id}
            </span>
            <span
              className={`rounded-full px-1.5 py-px font-medium ${fullscreen ? "text-[10px]" : "text-[9px]"} ${
                STATUS_STYLES[s.status] ?? "bg-muted text-muted-foreground"
              }`}
            >
              {s.status}
            </span>
          </div>

          {/* Task */}
          <p className={`text-foreground leading-snug ${fullscreen ? "text-sm" : "text-[11px] line-clamp-2"}`}>
            {s.task}
          </p>

          {/* Bottom row */}
          <div className="flex items-center gap-3 pt-0.5">
            <span className={`text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
              {timeAgo(s.created_at)}
            </span>
            {s.files_count > 0 && (
              <span className={`flex items-center gap-0.5 text-muted-foreground/60 ${fullscreen ? "text-xs" : "text-[10px]"}`}>
                <FileCode className="h-3 w-3" />
                {s.files_count} file{s.files_count !== 1 ? "s" : ""}
              </span>
            )}
            {s.pr_url && (
              <a
                href={s.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className={`flex items-center gap-1 text-emerald-400 hover:text-emerald-300 transition-colors ${fullscreen ? "text-xs" : "text-[10px]"}`}
              >
                <GitPullRequest className="h-3 w-3" />
                Pull Request
              </a>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
