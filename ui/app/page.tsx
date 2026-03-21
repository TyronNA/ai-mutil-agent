"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Activity, ListFilter, AlertCircle, CheckCircle2, LayoutDashboard } from "lucide-react";
import { AgentCard } from "@/components/AgentCard";
import { PipelineStages } from "@/components/PipelineStages";
import { ActivityFeed } from "@/components/ActivityFeed";
import { TaskForm } from "@/components/TaskForm";
import { ResultBar } from "@/components/ResultBar";
import { fetchAgents, startRun, createWebSocket } from "@/lib/api";
import type { Agent, FeedLine, RunRequest, WsEvent } from "@/types";

type FeedFilter = "all" | "progress" | "result" | "error";

const AGENT_COLOR_MAP: Record<string, string> = {
  git: "#f59e0b",
  planner: "#a78bfa",
  coder: "#34d399",
  reviewer: "#60a5fa",
  tester: "#f472b6",
  notifier: "#fb923c",
};

const AGENT_ICON_MAP: Record<string, string> = {
  git: "🔧",
  planner: "🧠",
  coder: "💻",
  reviewer: "🔍",
  tester: "🧪",
  notifier: "📬",
};

// Stage keywords to detect current pipeline stage from agent messages
const STAGE_KEYWORDS: Record<string, string> = {
  planner: "plan",
  git: "checkout",
  coder: "code",
  reviewer: "review",
  tester: "test",
  notifier: "notify",
};

let feedIdCounter = 0;
function nextId() {
  return ++feedIdCounter;
}

function nowTs() {
  return new Date().toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export default function DashboardPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentsLoading, setAgentsLoading] = useState(true);

  const [isRunning, setIsRunning] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [feedLines, setFeedLines] = useState<FeedLine[]>([]);
  const [feedFilter, setFeedFilter] = useState<FeedFilter>("all");

  const [activeAgent, setActiveAgent] = useState<string | undefined>();
  const [activeStage, setActiveStage] = useState<string | undefined>();
  const [completedStages, setCompletedStages] = useState<string[]>([]);
  const [errorStage, setErrorStage] = useState<string | undefined>();

  const [prUrl, setPrUrl] = useState<string | undefined>();
  const [filesWritten, setFilesWritten] = useState<string[] | undefined>();
  const [pipelineStatus, setPipelineStatus] = useState<"running" | "done" | "error" | undefined>();

  const wsRef = useRef<WebSocket | null>(null);

  // Load agents on mount
  useEffect(() => {
    fetchAgents()
      .then(setAgents)
      .catch(() =>
        setAgents([
          { name: "Planner", role: "Task Analysis & Planning", icon: "🧠", color: "#a78bfa", description: "Analyzes tasks and creates subtask plans.", system_prompt: "" },
          { name: "Coder", role: "Code Implementation", icon: "💻", color: "#34d399", description: "Writes code based on the plan.", system_prompt: "" },
          { name: "Reviewer", role: "Code Review", icon: "🔍", color: "#60a5fa", description: "Reviews code for quality and correctness.", system_prompt: "" },
          { name: "Tester", role: "Test Execution", icon: "🧪", color: "#f472b6", description: "Runs tests and validates changes.", system_prompt: "" },
          { name: "Notifier", role: "Notifications & PR", icon: "📬", color: "#fb923c", description: "Creates PRs and sends notifications.", system_prompt: "" },
          { name: "Git", role: "Version Control", icon: "🔧", color: "#f59e0b", description: "Manages git operations.", system_prompt: "" },
        ])
      )
      .finally(() => setAgentsLoading(false));
  }, []);

  const addFeedLine = useCallback((partial: Omit<FeedLine, "id" | "timestamp">) => {
    setFeedLines((prev) => [
      ...prev,
      { ...partial, id: nextId(), timestamp: nowTs() },
    ]);
  }, []);

  const handleWsEvent = useCallback((event: WsEvent) => {
    const agentKey = event.agent?.toLowerCase() ?? "system";
    const color = AGENT_COLOR_MAP[agentKey] ?? "#94a3b8";
    const icon = AGENT_ICON_MAP[agentKey] ?? "🤖";

    setActiveAgent(agentKey);

    // Track pipeline stage
    const stage = STAGE_KEYWORDS[agentKey];
    if (stage) {
      setActiveStage(stage);
    }

    if (event.type === "progress") {
      addFeedLine({
        agent: event.agent ?? "system",
        agentColor: color,
        agentIcon: icon,
        type: "progress",
        message: event.message ?? "",
      });
    } else if (event.type === "result") {
      // Mark current stage as done
      if (stage) {
        setCompletedStages((prev) => (prev.includes(stage) ? prev : [...prev, stage]));
      }

      const data = event.data ?? {};
      if (typeof data.pr_url === "string") setPrUrl(data.pr_url);
      if (Array.isArray(data.files_written)) setFilesWritten(data.files_written as string[]);

      addFeedLine({
        agent: event.agent ?? "system",
        agentColor: color,
        agentIcon: icon,
        type: "result",
        message: event.message ?? JSON.stringify(data),
      });
    } else if (event.type === "error") {
      if (stage) setErrorStage(stage);
      addFeedLine({
        agent: event.agent ?? "system",
        agentColor: color,
        agentIcon: icon,
        type: "error",
        message: event.message ?? "Unknown error",
      });
    } else if (event.type === "done") {
      setIsRunning(false);
      setActiveAgent(undefined);
      setActiveStage(undefined);
      setPipelineStatus("done");
      addFeedLine({
        agent: "system",
        agentColor: "#94a3b8",
        agentIcon: "✅",
        type: "done",
        message: "Pipeline completed successfully",
      });
    }
  }, [addFeedLine]);

  const connectWs = useCallback((sid: string) => {
    if (wsRef.current) {
      wsRef.current.close();
    }

    const ws = createWebSocket(sid);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data: WsEvent = JSON.parse(ev.data);
        handleWsEvent(data);
      } catch {
        // ignore non-JSON
      }
    };

    ws.onerror = () => {
      setPipelineStatus("error");
      setIsRunning(false);
      addFeedLine({
        agent: "system",
        agentColor: "#ef4444",
        agentIcon: "⚠️",
        type: "error",
        message: "WebSocket connection error",
      });
    };

    ws.onclose = () => {
      if (isRunning) {
        setIsRunning(false);
      }
    };
  }, [handleWsEvent, addFeedLine, isRunning]);

  const handleRun = async (req: RunRequest) => {
    // Reset state
    setFeedLines([]);
    setPrUrl(undefined);
    setFilesWritten(undefined);
    setPipelineStatus("running");
    setActiveAgent(undefined);
    setActiveStage("init");
    setCompletedStages([]);
    setErrorStage(undefined);
    setIsRunning(true);

    try {
      const { session_id } = await startRun(req);
      setSessionId(session_id);
      addFeedLine({
        agent: "system",
        agentColor: "#94a3b8",
        agentIcon: "🚀",
        type: "info",
        message: `Pipeline started — session ${session_id}`,
      });
      connectWs(session_id);
    } catch (err) {
      setIsRunning(false);
      setPipelineStatus("error");
      addFeedLine({
        agent: "system",
        agentColor: "#ef4444",
        agentIcon: "⚠️",
        type: "error",
        message: err instanceof Error ? err.message : "Failed to start pipeline",
      });
    }
  };

  // Cleanup WS on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  const filterTabs: { label: string; value: FeedFilter; icon: React.ReactNode }[] = [
    { label: "All", value: "all", icon: <ListFilter className="h-3 w-3" /> },
    { label: "Progress", value: "progress", icon: <Activity className="h-3 w-3" /> },
    { label: "Results", value: "result", icon: <CheckCircle2 className="h-3 w-3" /> },
    { label: "Errors", value: "error", icon: <AlertCircle className="h-3 w-3" /> },
  ];

  const errorCount = feedLines.filter((l) => l.type === "error").length;

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* ── LEFT SIDEBAR ── */}
      <aside className="flex w-64 flex-shrink-0 flex-col border-r border-border bg-card/30">
        {/* Brand */}
        <div className="flex items-center gap-2.5 border-b border-border px-4 py-3.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/20">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <div>
            <div className="text-sm font-bold text-foreground">AI Multi-Agent</div>
            <div className="text-[10px] text-muted-foreground">Operations Dashboard</div>
          </div>
        </div>

        {/* Pipeline stages */}
        <div className="border-b border-border px-4 py-3">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            Pipeline
          </p>
          <PipelineStages
            activeStage={activeStage}
            completedStages={completedStages}
            errorStage={errorStage}
          />
        </div>

        {/* Agents list */}
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-1.5">
          <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            Agents
          </p>
          {agentsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="h-12 animate-pulse rounded-lg bg-muted" />
              ))}
            </div>
          ) : (
            agents.map((agent) => (
              <AgentCard
                key={agent.name}
                agent={agent}
                isActive={activeAgent === agent.name.toLowerCase()}
              />
            ))
          )}
        </div>
      </aside>

      {/* ── MAIN CONTENT ── */}
      <main className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center gap-3 border-b border-border px-6 py-3">
          <LayoutDashboard className="h-4 w-4 text-muted-foreground" />
          <h1 className="text-sm font-semibold text-foreground">Pipeline Control</h1>

          {/* Status badge */}
          {pipelineStatus && (
            <span
              className={
                pipelineStatus === "done"
                  ? "ml-auto rounded-full bg-emerald-500/20 px-2.5 py-0.5 text-xs text-emerald-400 border border-emerald-500/30"
                  : pipelineStatus === "error"
                  ? "ml-auto rounded-full bg-red-500/20 px-2.5 py-0.5 text-xs text-red-400 border border-red-500/30"
                  : "ml-auto rounded-full bg-primary/20 px-2.5 py-0.5 text-xs text-primary border border-primary/30 animate-pulse-dot"
              }
            >
              {pipelineStatus === "done"
                ? "✓ Done"
                : pipelineStatus === "error"
                ? "✗ Error"
                : "⟳ Running"}
            </span>
          )}

          {sessionId && (
            <span className="text-[10px] text-muted-foreground font-mono">
              {sessionId.slice(0, 8)}…
            </span>
          )}
        </header>

        <div className="flex flex-1 overflow-hidden gap-0">
          {/* Task form + result column */}
          <div className="flex w-80 flex-shrink-0 flex-col gap-3 border-r border-border p-4 overflow-y-auto">
            <TaskForm onSubmit={handleRun} isRunning={isRunning} />

            {(prUrl || filesWritten || sessionId) && (
              <ResultBar
                prUrl={prUrl}
                filesWritten={filesWritten}
                sessionId={sessionId}
                status={pipelineStatus}
              />
            )}
          </div>

          {/* Activity feed */}
          <div className="flex flex-1 flex-col overflow-hidden">
            {/* Feed tabs */}
            <div className="flex items-center gap-1 border-b border-border px-4 py-2">
              {filterTabs.map((tab) => (
                <button
                  key={tab.value}
                  onClick={() => setFeedFilter(tab.value)}
                  className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    feedFilter === tab.value
                      ? "bg-primary/20 text-primary"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                  {tab.value === "error" && errorCount > 0 && (
                    <span className="rounded-full bg-red-500/20 px-1.5 text-[10px] text-red-400">
                      {errorCount}
                    </span>
                  )}
                </button>
              ))}

              <span className="ml-auto text-[10px] text-muted-foreground">
                {feedLines.length} event{feedLines.length !== 1 ? "s" : ""}
              </span>
            </div>

            {/* Feed content */}
            <div className="flex-1 overflow-hidden p-2">
              <ActivityFeed lines={feedLines} filter={feedFilter} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
