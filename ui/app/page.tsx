"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, Activity, ListFilter, AlertCircle, CheckCircle2, LayoutDashboard, MessageSquare, Bug, Sparkles, Loader2, BarChart2, ListChecks, Menu, X } from "lucide-react";
import { AgentCard } from "@/components/AgentCard";
import { PipelineStages } from "@/components/PipelineStages";
import { ActivityFeed } from "@/components/ActivityFeed";
import { TaskForm } from "@/components/TaskForm";
import { ResultBar } from "@/components/ResultBar";
import { SessionsPanel } from "@/components/SessionsPanel";
import { TechChat } from "@/components/TechChat";
import { AnalyticsPanel } from "@/components/AnalyticsPanel";
import { TaskQueuePanel } from "@/components/TaskQueuePanel";
import { fetchAgents, startRun, startAudit, stopSession, createWebSocket } from "@/lib/api";
import type { Agent, FeedLine, RunRequest, WsEvent } from "@/types";

type FeedFilter = "all" | "progress" | "result" | "error";

const AGENT_COLOR_MAP: Record<string, string> = {
  git:         "#f59e0b",
  tech_expert: "#a78bfa",
  dev:         "#34d399",
  qa:          "#60a5fa",
  notifier:    "#fb923c",
};

const AGENT_ICON_MAP: Record<string, string> = {
  git:         "🌿",
  tech_expert: "🏛",
  dev:         "⚔",
  qa:          "🧪",
  notifier:    "🔔",
};

const STAGE_KEYWORDS: Record<string, string> = {
  git:         "checkout",
  tech_expert: "plan",   // first call = planning; second = review (handled below)
  dev:         "code",
  qa:          "code",
  notifier:    "notify",
};

let feedIdCounter = 0;
function nextId() { return ++feedIdCounter; }
function nowTs() {
  return new Date().toLocaleTimeString("en-US", {
    hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit",
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

  const [chatOpen, setChatOpen] = useState(false);
  const [auditLoading, setAuditLoading] = useState<"audit" | "improve" | null>(null);
  const [mainView, setMainView] = useState<"pipeline" | "tasks" | "queue" | "analytics">("pipeline");
  const [prefillTask, setPrefillTask] = useState<string | undefined>();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [mobilePipelineTab, setMobilePipelineTab] = useState<"form" | "feed">("form");

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setAgentsLoading(true);
    fetchAgents("game")
      .then(setAgents)
      .catch(() => setAgents(getFallbackAgents()))
      .finally(() => setAgentsLoading(false));
  }, []);

  const addFeedLine = useCallback((partial: Omit<FeedLine, "id" | "timestamp">) => {
    setFeedLines((prev) => [...prev, { ...partial, id: nextId(), timestamp: nowTs() }]);
  }, []);

  const handleWsEvent = useCallback((event: WsEvent) => {
    const agentKey = event.agent?.toLowerCase() ?? "system";
    const color = AGENT_COLOR_MAP[agentKey] ?? "#94a3b8";
    const icon = AGENT_ICON_MAP[agentKey] ?? "🤖";

    setActiveAgent(agentKey);

    const stage = STAGE_KEYWORDS[agentKey];

    // tech_expert second appearance (after code stage) = arch review
    if (agentKey === "tech_expert" && completedStages.includes("code")) {
      setActiveStage("review");
    } else if (stage) {
      setActiveStage(stage);
    }

    // Load context stage
    if (event.message?.toLowerCase().includes("load") && agentKey === "tech_expert") {
      setActiveStage("load");
    }

    if (event.type === "progress") {
      addFeedLine({ agent: event.agent ?? "system", agentColor: color, agentIcon: icon, type: "progress", message: event.message ?? "" });
    } else if (event.type === "result") {
      if (stage) setCompletedStages((prev) => (prev.includes(stage) ? prev : [...prev, stage]));

      const data = event.data ?? {};
      if (typeof data.pr_url === "string") setPrUrl(data.pr_url);
      if (Array.isArray(data.files_written)) setFilesWritten(data.files_written as string[]);
      // Also handle top-level pr_url/files sent directly by server
      if (event.pr_url) setPrUrl(event.pr_url);
      if (event.files) setFilesWritten(event.files);

      addFeedLine({ agent: event.agent ?? "system", agentColor: color, agentIcon: icon, type: "result", message: event.message ?? JSON.stringify(data) });
    } else if (event.type === "error") {
      if (stage) setErrorStage(stage);
      addFeedLine({ agent: event.agent ?? "system", agentColor: color, agentIcon: icon, type: "error", message: event.message ?? "Unknown error" });
    } else if (event.type === "done") {
      setIsRunning(false);
      setActiveAgent(undefined);
      setActiveStage(undefined);
      setPipelineStatus("done");
      addFeedLine({ agent: "system", agentColor: "#94a3b8", agentIcon: "✅", type: "done", message: "Pipeline completed successfully" });
    }
  }, [addFeedLine, completedStages]);

  const connectWs = useCallback((sid: string) => {
    wsRef.current?.close();
    const ws = createWebSocket(sid);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data: WsEvent = JSON.parse(ev.data);
        handleWsEvent(data);
      } catch { /* ignore non-JSON */ }
    };

    ws.onerror = () => {
      setPipelineStatus("error");
      setIsRunning(false);
      addFeedLine({ agent: "system", agentColor: "#ef4444", agentIcon: "⚠️", type: "error", message: "WebSocket connection error" });
    };

    ws.onclose = () => {
      if (isRunning) setIsRunning(false);
    };
  }, [handleWsEvent, addFeedLine, isRunning]);

  const handleRun = async (req: RunRequest) => {
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
      addFeedLine({ agent: "system", agentColor: "#94a3b8", agentIcon: "🚀", type: "info", message: `Pipeline started — session ${session_id}` });
      connectWs(session_id);
    } catch (err) {
      setIsRunning(false);
      setPipelineStatus("error");
      addFeedLine({ agent: "system", agentColor: "#ef4444", agentIcon: "⚠️", type: "error", message: err instanceof Error ? err.message : "Failed to start pipeline" });
    }
  };

  const handleAudit = useCallback(async (type: "audit" | "improve") => {
    if (isRunning || auditLoading) return;
    setAuditLoading(type);
    setFeedLines([]);
    setPrUrl(undefined);
    setFilesWritten(undefined);
    setPipelineStatus("running");
    setActiveAgent(undefined);
    setActiveStage("load");
    setCompletedStages([]);
    setErrorStage(undefined);
    setIsRunning(true);
    try {
      const { session_id } = await startAudit(type);
      setSessionId(session_id);
      addFeedLine({ agent: "system", agentColor: "#94a3b8", agentIcon: type === "audit" ? "🔍" : "✨", type: "info", message: `${type === "audit" ? "Bug Audit" : "Improvement Scan"} started — session ${session_id}` });
      connectWs(session_id);
    } catch (err) {
      setIsRunning(false);
      setPipelineStatus("error");
      addFeedLine({ agent: "system", agentColor: "#ef4444", agentIcon: "⚠️", type: "error", message: err instanceof Error ? err.message : "Failed to start audit" });
    } finally {
      setAuditLoading(null);
    }
  }, [isRunning, auditLoading, addFeedLine, connectWs]);

  const handleStop = useCallback(() => {
    if (sessionId) stopSession(sessionId).catch(() => {});
    setIsRunning(false);
    setPipelineStatus("error");
    addFeedLine({ agent: "system", agentColor: "#f59e0b", agentIcon: "⏹", type: "error", message: "Stopped by user." });
  }, [sessionId, addFeedLine]);

  const handleCreateTask = useCallback((taskText: string) => {
    setPrefillTask(taskText);
    setMainView("pipeline");
    setChatOpen(false);
  }, []);

  useEffect(() => { return () => { wsRef.current?.close(); }; }, []);

  const filterTabs: { label: string; value: FeedFilter; icon: React.ReactNode }[] = [
    { label: "All",      value: "all",      icon: <ListFilter className="h-3 w-3" /> },
    { label: "Progress", value: "progress", icon: <Activity className="h-3 w-3" /> },
    { label: "Results",  value: "result",   icon: <CheckCircle2 className="h-3 w-3" /> },
    { label: "Errors",   value: "error",    icon: <AlertCircle className="h-3 w-3" /> },
  ];

  const errorCount = feedLines.filter((l) => l.type === "error").length;

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-background">
      {/* ── MOBILE SIDEBAR OVERLAY ── */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── LEFT SIDEBAR ── */}
      <aside
        className={`
          fixed inset-y-0 left-0 z-50 flex w-72 flex-shrink-0 flex-col border-r border-border bg-card/95 backdrop-blur-sm
          transform transition-transform duration-300 ease-in-out
          md:static md:w-64 md:translate-x-0 md:z-auto md:bg-card/30 md:backdrop-blur-none
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {/* Brand */}
        <div className="flex items-center gap-2.5 border-b border-border px-4 py-3.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/20">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-foreground">AI Multi-Agent</div>
            <div className="text-[10px] text-muted-foreground">🎮 Game Pipeline</div>
          </div>
          <button
            className="md:hidden rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="h-4 w-4" />
          </button>
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
              {[1, 2, 3].map((i) => (
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
      <main className="flex flex-1 flex-col overflow-hidden min-w-0">
        {/* Top bar */}
        <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:px-4 md:py-2.5">
          {/* Hamburger — mobile only */}
          <button
            className="md:hidden flex-shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="h-4 w-4" />
          </button>

          {/* View tabs — desktop */}
          <div className="hidden md:flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5">
            <button
              onClick={() => setMainView("pipeline")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "pipeline"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <LayoutDashboard className="h-3 w-3" />
              Pipeline
            </button>
            <button
              onClick={() => setMainView("tasks")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "tasks"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Activity className="h-3 w-3" />
              Tasks
            </button>
            <button
              onClick={() => setMainView("queue")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "queue"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <ListChecks className="h-3 w-3" />
              Queue
            </button>
            <button
              onClick={() => setMainView("analytics")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "analytics"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <BarChart2 className="h-3 w-3" />
              Analytics
            </button>
          </div>

          {/* Mobile: current view title */}
          <span className="md:hidden text-sm font-semibold text-foreground capitalize">
            {mainView}
          </span>

          {/* Action buttons */}
          <div className="ml-auto flex items-center gap-1 md:gap-1.5">
            <button
              onClick={() => handleAudit("audit")}
              disabled={isRunning || !!auditLoading}
              className="flex items-center gap-1 md:gap-1.5 rounded-md border border-border bg-muted/30 px-1.5 md:px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-amber-400 hover:border-amber-500/40 hover:bg-amber-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title="TechExpert scans codebase for bugs"
            >
              {auditLoading === "audit" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Bug className="h-3 w-3" />
              )}
              <span className="hidden sm:inline">Audit Bugs</span>
            </button>

            <button
              onClick={() => handleAudit("improve")}
              disabled={isRunning || !!auditLoading}
              className="flex items-center gap-1 md:gap-1.5 rounded-md border border-border bg-muted/30 px-1.5 md:px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-purple-400 hover:border-purple-500/40 hover:bg-purple-500/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              title="TechExpert suggests improvements"
            >
              {auditLoading === "improve" ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Sparkles className="h-3 w-3" />
              )}
              <span className="hidden sm:inline">Improve</span>
            </button>

            <div className="hidden sm:block mx-0.5 h-4 w-px bg-border" />

            <button
              onClick={() => setChatOpen((o) => !o)}
              className={`flex items-center gap-1 md:gap-1.5 rounded-md border px-1.5 md:px-2.5 py-1 text-xs font-medium transition-colors ${
                chatOpen
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border bg-muted/30 text-muted-foreground hover:text-foreground hover:bg-muted"
              }`}
              title="Chat with TechExpert"
            >
              <MessageSquare className="h-3 w-3" />
              <span className="hidden sm:inline">Chat</span>
            </button>

            {isRunning && (
              <button
                onClick={handleStop}
                className="flex items-center gap-1 md:gap-1.5 rounded-md border border-red-500/40 bg-red-500/10 px-1.5 md:px-2.5 py-1 text-xs font-medium text-red-400 hover:bg-red-500/20 transition-colors"
                title="Stop after current subtask"
              >
                <span className="h-2 w-2 rounded-sm bg-red-400" />
                <span className="hidden sm:inline">Stop</span>
              </button>
            )}

            {pipelineStatus && (
              <span
                className={
                  pipelineStatus === "done"
                    ? "rounded-full bg-emerald-500/20 px-2 py-0.5 text-xs text-emerald-400 border border-emerald-500/30"
                    : pipelineStatus === "error"
                    ? "rounded-full bg-red-500/20 px-2 py-0.5 text-xs text-red-400 border border-red-500/30"
                    : "rounded-full bg-primary/20 px-2 py-0.5 text-xs text-primary border border-primary/30 animate-pulse-dot"
                }
              >
                {pipelineStatus === "done" ? "✓ Done" : pipelineStatus === "error" ? "✗ Err" : "⟳"}
              </span>
            )}

            {sessionId && (
              <span className="hidden sm:inline text-[10px] text-muted-foreground font-mono">
                {sessionId.slice(0, 8)}…
              </span>
            )}
          </div>
        </header>

        {/* Tasks dashboard view */}
        {mainView === "tasks" && (
          <div className="flex-1 overflow-y-auto p-4 md:p-6">
            <div className="max-w-3xl mx-auto">
              <h2 className="mb-4 text-sm font-semibold text-foreground">All Sessions</h2>
              <SessionsPanel fullscreen />
            </div>
          </div>
        )}

        {/* Queue view */}
        {mainView === "queue" && (
          <div className="flex-1 overflow-hidden">
            <TaskQueuePanel />
          </div>
        )}

        {/* Analytics view */}
        {mainView === "analytics" && (
          <div className="flex-1 overflow-hidden">
            <AnalyticsPanel currentSessionId={isRunning ? sessionId : undefined} />
          </div>
        )}

        {/* Pipeline view */}
        <div className={`flex flex-1 overflow-hidden ${mainView !== "pipeline" ? "hidden" : ""}`}>

          {/* ── DESKTOP: side-by-side columns ── */}
          {/* Task form + result column */}
          <div className="hidden md:flex w-80 flex-shrink-0 flex-col gap-3 border-r border-border p-4 overflow-y-auto">
            <TaskForm onSubmit={handleRun} isRunning={isRunning} prefillTask={prefillTask} />
            {(prUrl || filesWritten || sessionId) && (
              <ResultBar
                prUrl={prUrl}
                filesWritten={filesWritten}
                sessionId={sessionId}
                status={pipelineStatus}
              />
            )}
          </div>

          {/* Activity feed — desktop */}
          <div className="hidden md:flex flex-1 flex-col overflow-hidden">
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
            <div className="flex-1 overflow-hidden p-2">
              <ActivityFeed lines={feedLines} filter={feedFilter} />
            </div>
          </div>

          {/* TechExpert chat drawer — desktop */}
          {chatOpen && (
            <div className="hidden md:flex w-96 flex-shrink-0 flex-col border-l border-border bg-card/40">
              <TechChat onClose={() => setChatOpen(false)} onCreateTask={handleCreateTask} />
            </div>
          )}

          {/* ── MOBILE: tabbed single column ── */}
          <div className="flex md:hidden flex-1 flex-col overflow-hidden">
            {/* Mobile sub-tabs: Form | Feed */}
            <div className="flex border-b border-border">
              <button
                onClick={() => setMobilePipelineTab("form")}
                className={`flex-1 py-2 text-xs font-medium transition-colors ${
                  mobilePipelineTab === "form"
                    ? "text-primary border-b-2 border-primary"
                    : "text-muted-foreground"
                }`}
              >
                Task
              </button>
              <button
                onClick={() => setMobilePipelineTab("feed")}
                className={`flex-1 py-2 text-xs font-medium transition-colors relative ${
                  mobilePipelineTab === "feed"
                    ? "text-primary border-b-2 border-primary"
                    : "text-muted-foreground"
                }`}
              >
                Activity
                {feedLines.length > 0 && (
                  <span className="ml-1 rounded-full bg-primary/30 px-1.5 text-[10px] text-primary">
                    {feedLines.length}
                  </span>
                )}
              </button>
            </div>

            {/* Form tab */}
            {mobilePipelineTab === "form" && (
              <div className="flex-1 overflow-y-auto p-4 space-y-3">
                <TaskForm onSubmit={handleRun} isRunning={isRunning} prefillTask={prefillTask} />
                {(prUrl || filesWritten || sessionId) && (
                  <ResultBar
                    prUrl={prUrl}
                    filesWritten={filesWritten}
                    sessionId={sessionId}
                    status={pipelineStatus}
                  />
                )}
              </div>
            )}

            {/* Feed tab */}
            {mobilePipelineTab === "feed" && (
              <div className="flex flex-1 flex-col overflow-hidden">
                <div className="flex items-center gap-1 border-b border-border px-3 py-1.5 overflow-x-auto">
                  {filterTabs.map((tab) => (
                    <button
                      key={tab.value}
                      onClick={() => setFeedFilter(tab.value)}
                      className={`flex flex-shrink-0 items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                        feedFilter === tab.value
                          ? "bg-primary/20 text-primary"
                          : "text-muted-foreground"
                      }`}
                    >
                      {tab.icon}
                      {tab.label}
                      {tab.value === "error" && errorCount > 0 && (
                        <span className="rounded-full bg-red-500/20 px-1 text-[10px] text-red-400">
                          {errorCount}
                        </span>
                      )}
                    </button>
                  ))}
                  <span className="ml-auto flex-shrink-0 text-[10px] text-muted-foreground">
                    {feedLines.length}
                  </span>
                </div>
                <div className="flex-1 overflow-hidden p-2">
                  <ActivityFeed lines={feedLines} filter={feedFilter} />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── MOBILE BOTTOM NAVIGATION ── */}
        <nav className="md:hidden flex items-center border-t border-border bg-card/60 backdrop-blur-sm">
          {[
            { view: "pipeline" as const, icon: <LayoutDashboard className="h-5 w-5" />, label: "Pipeline" },
            { view: "tasks"    as const, icon: <Activity className="h-5 w-5" />,        label: "Tasks" },
            { view: "queue"    as const, icon: <ListChecks className="h-5 w-5" />,       label: "Queue" },
            { view: "analytics"as const, icon: <BarChart2 className="h-5 w-5" />,        label: "Analytics" },
          ].map(({ view, icon, label }) => (
            <button
              key={view}
              onClick={() => setMainView(view)}
              className={`flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium transition-colors ${
                mainView === view ? "text-primary" : "text-muted-foreground"
              }`}
            >
              {icon}
              {label}
            </button>
          ))}
        </nav>
      </main>

      {/* ── MOBILE CHAT FULL-SCREEN OVERLAY ── */}
      {chatOpen && (
        <div className="md:hidden fixed inset-0 z-50 flex flex-col bg-background">
          <TechChat onClose={() => setChatOpen(false)} onCreateTask={handleCreateTask} />
        </div>
      )}
    </div>
  );
}

function getFallbackAgents(): Agent[] {
  return [
    { name: "tech_expert", role: "Tech Expert / Architect", icon: "🏛", color: "#a78bfa", description: "Plans subtasks and reviews final implementation.", system_prompt: "", pipeline: "game" },
    { name: "dev",         role: "Game Developer",          icon: "⚔", color: "#34d399", description: "Writes complete Phaser 4 JS files to disk.",    system_prompt: "", pipeline: "game" },
    { name: "qa",          role: "QA Engineer",             icon: "🧪", color: "#60a5fa", description: "Static analysis against game invariants.",       system_prompt: "", pipeline: "game" },
    { name: "git",         role: "Git Operations",          icon: "🌿", color: "#f59e0b", description: "Commits, pushes, creates GitHub PR.",            system_prompt: "", pipeline: "game" },
    { name: "notifier",    role: "Notifier",                icon: "🔔", color: "#fb923c", description: "macOS notification + webhook.",                  system_prompt: "", pipeline: "game" },
  ];
}
