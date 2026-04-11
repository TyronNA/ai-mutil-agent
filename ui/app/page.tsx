"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  BarChart2,
  Bot,
  KeyRound,
  ListChecks,
  Loader2,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  ShieldAlert,
  X,
} from "lucide-react";
import { SessionsPanel } from "@/components/SessionsPanel";
import { ChatHistoryPanel } from "@/components/ChatHistoryPanel";
import { TechChat } from "@/components/TechChat";
import { MateChat } from "@/components/MateChat";
import { AnalyticsPanel } from "@/components/AnalyticsPanel";
import { TaskQueuePanel } from "@/components/TaskQueuePanel";
import { GamePreviewPanel } from "@/components/GamePreviewPanel";
import { checkAuth, createWebSocket, loginWithApiKey, logout, startAudit, stopSession } from "@/lib/api";
import type { ChatCharacter, ChatMessage, WsEvent } from "@/types";

const CHAT_HISTORY_STORAGE_KEY = "ai-multi-agent:tech-chat-history:v1";

const CHAT_CHARACTER_KEY = "ai-multi-agent:chat-character:v1";

type SavedChatThread = {
  chatId: string;
  title: string;
  updatedAt: string;
  history: ChatMessage[];
  character?: ChatCharacter;
};

function getInitialCharacter(): ChatCharacter {
  if (typeof window === "undefined") return "mate";
  try {
    const saved = window.localStorage.getItem(CHAT_CHARACTER_KEY);
    if (saved === "mate" || saved === "tech_expert") return saved;
  } catch {
    // ignore
  }
  return "mate";
}

function deriveChatTitle(history: ChatMessage[]): string {
  const firstUserMsg = history.find((m) => m.role === "user")?.content?.trim();
  if (!firstUserMsg) return "Untitled chat";
  return firstUserMsg.length > 56 ? `${firstUserMsg.slice(0, 56)}...` : firstUserMsg;
}

export default function DashboardPage() {
  const [authLoading, setAuthLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isAuthConfigured, setIsAuthConfigured] = useState(true);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);

  const [isRunning, setIsRunning] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [pipelineStatus, setPipelineStatus] = useState<"running" | "done" | "error" | undefined>();

  const [chatId, setChatId] = useState<string | undefined>();
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [savedChats, setSavedChats] = useState<SavedChatThread[]>([]);
  const [auditLoading, setAuditLoading] = useState<"audit" | "improve" | null>(null);

  const [mainView, setMainView] = useState<"chat" | "tasks" | "queue" | "analytics" | "preview">("chat");
  const [chatCharacter, setChatCharacterState] = useState<ChatCharacter>(getInitialCharacter);
  const [previewBranch, setPreviewBranch] = useState<string | undefined>();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const setChatCharacter = useCallback((c: ChatCharacter) => {
    setChatCharacterState(c);
    try { window.localStorage.setItem(CHAT_CHARACTER_KEY, c); } catch { /* ignore */ }
    setChatId(undefined);
    setChatHistory([]);
  }, []);

  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(CHAT_HISTORY_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as SavedChatThread[];
      if (!Array.isArray(parsed)) return;
      setSavedChats(
        parsed.filter((item) => item && typeof item.chatId === "string" && Array.isArray(item.history)),
      );
    } catch {
      // ignore malformed saved chat data
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(savedChats));
    } catch {
      // ignore quota/storage errors
    }
  }, [savedChats]);

  useEffect(() => {
    let mounted = true;

    checkAuth()
      .then((result) => {
        if (!mounted) return;
        setIsAuthenticated(result.authenticated);
        setIsAuthConfigured(result.configured);
      })
      .catch(() => {
        if (!mounted) return;
        setIsAuthenticated(false);
        setIsAuthConfigured(false);
      })
      .finally(() => {
        if (mounted) setAuthLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  const handleChatHistoryChange = useCallback((newChatId: string | undefined, newHistory: ChatMessage[]) => {
    setChatId(newChatId);
    setChatHistory(newHistory);

    if (!newChatId || newHistory.length === 0) return;

    const updated: SavedChatThread = {
      chatId: newChatId,
      title: deriveChatTitle(newHistory),
      updatedAt: new Date().toISOString(),
      history: newHistory,
      character: chatCharacter,
    };

    setSavedChats((prev) => {
      const next = [updated, ...prev.filter((item) => item.chatId !== newChatId)];
      return next.slice(0, 30);
    });
  }, [chatCharacter]);

  const handleSelectSavedChat = useCallback((selectedChatId: string) => {
    const selected = savedChats.find((item) => item.chatId === selectedChatId);
    if (!selected) return;
    setChatId(selected.chatId);
    setChatHistory(selected.history);
    if (selected.character) setChatCharacterState(selected.character);
    setMainView("chat");
    setSidebarOpen(false);
  }, [savedChats]);

  const handleStartNewChat = useCallback(() => {
    setChatId(undefined);
    setChatHistory([]);
    setMainView("chat");
    setSidebarOpen(false);
  }, []);

  const handleDeleteSavedChat = useCallback((targetChatId: string) => {
    setSavedChats((prev) => prev.filter((item) => item.chatId !== targetChatId));
    if (chatId === targetChatId) {
      setChatId(undefined);
      setChatHistory([]);
    }
  }, [chatId]);

  useEffect(() => {
    if (!sidebarOpen) return;

    const prevOverflow = document.body.style.overflow;
    const prevOverscroll = document.body.style.overscrollBehavior;
    document.body.style.overflow = "hidden";
    document.body.style.overscrollBehavior = "none";

    return () => {
      document.body.style.overflow = prevOverflow;
      document.body.style.overscrollBehavior = prevOverscroll;
    };
  }, [sidebarOpen]);

  const handleWsEvent = useCallback((event: WsEvent) => {
    if (event.type === "error") {
      setPipelineStatus("error");
      setIsRunning(false);
      return;
    }

    if (event.type === "done") {
      setPipelineStatus("done");
      setIsRunning(false);
    }
  }, []);

  const connectWs = useCallback((sid: string) => {
    wsRef.current?.close();
    const ws = createWebSocket(sid);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const data: WsEvent = JSON.parse(ev.data);
        handleWsEvent(data);
      } catch {
        // ignore malformed message
      }
    };

    ws.onerror = () => {
      setPipelineStatus("error");
      setIsRunning(false);
    };

    ws.onclose = () => {
      if (isRunning) setIsRunning(false);
    };
  }, [handleWsEvent, isRunning]);

  const handleAudit = useCallback(async (type: "audit" | "improve") => {
    if (isRunning || auditLoading) return;

    setAuditLoading(type);
    setPipelineStatus("running");
    setIsRunning(true);

    try {
      const { session_id } = await startAudit(type);
      setSessionId(session_id);
      connectWs(session_id);
    } catch {
      setPipelineStatus("error");
      setIsRunning(false);
    } finally {
      setAuditLoading(null);
    }
  }, [isRunning, auditLoading, connectWs]);

  const handleStop = useCallback(() => {
    if (sessionId) stopSession(sessionId).catch(() => {});
    setIsRunning(false);
    setPipelineStatus("error");
  }, [sessionId]);

  const handleCreateTask = useCallback((_taskText: string) => {
    setMainView("queue");
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  const handleLogin = useCallback(async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setAuthError(null);

    try {
      await loginWithApiKey(apiKeyInput.trim());
      const status = await checkAuth();
      setIsAuthenticated(status.authenticated);
      setIsAuthConfigured(status.configured);
      setApiKeyInput("");
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "Login failed");
      setIsAuthenticated(false);
    }
  }, [apiKeyInput]);

  const handleLogout = useCallback(async () => {
    await logout().catch(() => {});
    setIsAuthenticated(false);
    setPipelineStatus(undefined);
    setSessionId(undefined);
    setIsRunning(false);
  }, []);

  if (authLoading) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-background px-4">
        <div className="rounded-xl border border-border bg-card/70 px-5 py-4 text-sm text-muted-foreground">
          Checking authentication...
        </div>
      </div>
    );
  }

  if (!isAuthConfigured) {
    return (
      <div className="flex min-h-[100dvh] items-center justify-center bg-background px-4">
        <div className="w-full max-w-md rounded-2xl border border-border bg-card/70 p-6 shadow-xl backdrop-blur-sm">
          <div className="mb-3 flex items-center gap-2 text-amber-400">
            <ShieldAlert className="h-5 w-5" />
            <h1 className="text-base font-semibold text-foreground">Auth Not Configured</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Server chưa có API key. Hãy set biến môi trường <span className="font-mono">WEB_API_KEY</span> trong file .env rồi restart backend.
          </p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="relative flex min-h-[100dvh] items-center justify-center overflow-hidden bg-background px-4">
        <div className="pointer-events-none absolute inset-0 opacity-60 [background:radial-gradient(circle_at_20%_20%,rgba(59,130,246,0.18),transparent_40%),radial-gradient(circle_at_80%_0%,rgba(16,185,129,0.16),transparent_38%)]" />
        <form
          onSubmit={handleLogin}
          className="relative z-10 w-full max-w-md rounded-2xl border border-border bg-card/80 p-6 shadow-xl backdrop-blur-sm"
        >
          <div className="mb-5 flex items-center gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/20">
              <Bot className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-foreground">AI Multi-Agent Login</h1>
              <p className="text-xs text-muted-foreground">Nhập API key để truy cập dashboard</p>
            </div>
          </div>

          <label className="mb-2 block text-xs font-medium text-muted-foreground">API Key</label>
          <div className="relative mb-3">
            <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              autoFocus
              className="h-11 w-full rounded-lg border border-border bg-background/80 pl-10 pr-3 text-sm text-foreground outline-none transition focus:border-primary"
              placeholder="Enter server API key"
              required
            />
          </div>

          {authError && (
            <div className="mb-3 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
              {authError}
            </div>
          )}

          <button
            type="submit"
            className="flex h-10 w-full items-center justify-center rounded-lg bg-primary text-xs font-semibold text-primary-foreground transition hover:opacity-90"
          >
            Login
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-background">
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 md:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      <aside
        className={`
          fixed inset-y-0 left-0 z-50 flex w-72 flex-shrink-0 flex-col border-r border-border bg-card/95 backdrop-blur-sm
          transform transition-transform duration-300 ease-in-out
          md:static md:w-64 md:translate-x-0 md:z-auto md:bg-card/30 md:backdrop-blur-none
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        <div className="flex items-center gap-2.5 border-b border-border px-4 py-3.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/20">
            <Bot className="h-4 w-4 text-primary" />
          </div>
          <div className="flex-1">
            <div className="text-sm font-bold text-foreground">AI Multi-Agent</div>
            <div className="text-[10px] text-muted-foreground">Game Pipeline</div>
          </div>
          <button
            className="md:hidden rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            onClick={() => setSidebarOpen(false)}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          <p className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            Chat History
          </p>
          <ChatHistoryPanel
            items={savedChats.map((item) => ({
              chatId: item.chatId,
              title: item.title,
              updatedAt: item.updatedAt,
              historyLength: item.history.length,
            }))}
            activeChatId={chatId}
            onSelect={handleSelectSavedChat}
            onDelete={handleDeleteSavedChat}
            onStartNew={handleStartNewChat}
          />
        </div>
      </aside>

      <main className="flex flex-1 flex-col overflow-hidden min-w-0">
        <header className="flex items-center gap-2 border-b border-border px-3 py-2 md:px-4 md:py-2.5">
          <button
            className="md:hidden flex-shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="h-4 w-4" />
          </button>

          <div className="hidden md:flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5">
            <button
              onClick={() => setMainView("chat")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "chat"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <MessageSquare className="h-3 w-3" />
              Chat
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
            <button
              onClick={() => setMainView("preview")}
              className={`flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium transition-colors ${
                mainView === "preview"
                  ? "bg-card shadow-sm text-foreground border border-border/50"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Monitor className="h-3 w-3" />
              Preview
            </button>
          </div>

          <span className="md:hidden text-sm font-semibold text-foreground capitalize">
            {mainView}
          </span>

          <div className="ml-auto flex items-center gap-1 md:gap-1.5">
            <button
              onClick={handleLogout}
              className="flex items-center gap-1 rounded-md border border-border bg-muted/30 px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
              title="Logout"
            >
              <LogOut className="h-3 w-3" />
              <span className="hidden sm:inline">Logout</span>
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
                {pipelineStatus === "done" ? "Done" : pipelineStatus === "error" ? "Err" : "Running"}
              </span>
            )}

            {sessionId && (
              <span className="hidden sm:inline text-[10px] text-muted-foreground font-mono">
                {sessionId.slice(0, 8)}...
              </span>
            )}
          </div>
        </header>

        {mainView === "chat" && (
          <div className="flex-1 overflow-hidden flex flex-col min-h-0">
            {/* Character switcher — always visible, separate from the chat components */}
            <div className="flex items-center gap-2 border-b border-border bg-card/30 px-3 py-1.5">
              <div className="flex items-center rounded-md border border-border bg-muted/30 p-0.5 gap-0.5">
                <button
                  onClick={() => setChatCharacter("mate")}
                  className={`flex items-center gap-1.5 rounded-sm px-3 py-1 text-xs font-semibold transition-all ${
                    chatCharacter === "mate"
                      ? "bg-card shadow-sm text-foreground border border-border/50"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  😄 Mate
                </button>
                <button
                  onClick={() => setChatCharacter("tech_expert")}
                  className={`flex items-center gap-1.5 rounded-sm px-3 py-1 text-xs font-semibold transition-all ${
                    chatCharacter === "tech_expert"
                      ? "bg-card shadow-sm text-foreground border border-border/50"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  🏛 TechExpert
                </button>
              </div>
              <span className="text-[10px] text-muted-foreground">
                {chatCharacter === "mate" ? "Brainstorm & Q&A" : "Kiến trúc & pipeline"}
              </span>
            </div>
            {/* Chat panel */}
            <div className="flex-1 overflow-hidden min-h-0">
              {chatCharacter === "mate" ? (
                <MateChat
                  initialChatId={chatId}
                  initialHistory={chatHistory}
                  onHistoryChange={handleChatHistoryChange}
                />
              ) : (
                <TechChat
                  onCreateTask={handleCreateTask}
                  initialChatId={chatId}
                  initialHistory={chatHistory}
                  onHistoryChange={handleChatHistoryChange}
                />
              )}
            </div>
          </div>
        )}

        {mainView === "tasks" && (
          <div className="flex-1 overflow-y-auto p-4 md:p-6">
            <div className="max-w-3xl mx-auto">
              <h2 className="mb-4 text-sm font-semibold text-foreground">All Sessions</h2>
              <SessionsPanel fullscreen />
            </div>
          </div>
        )}

        {mainView === "queue" && (
          <div className="flex-1 overflow-hidden">
            <TaskQueuePanel
              onPreview={(branch) => {
                setPreviewBranch(branch);
                setMainView("preview");
              }}
            />
          </div>
        )}

        {mainView === "analytics" && (
          <div className="flex-1 overflow-hidden">
            <AnalyticsPanel currentSessionId={isRunning ? sessionId : undefined} />
          </div>
        )}

        {mainView === "preview" && (
          <div className="flex-1 overflow-hidden">
            <GamePreviewPanel initialBranch={previewBranch} />
          </div>
        )}

        <nav className="md:hidden flex items-center border-t border-border bg-card/60 backdrop-blur-sm [@supports(padding-bottom:env(safe-area-inset-bottom))]:pb-[env(safe-area-inset-bottom)]">
          {[
            { view: "chat" as const, icon: <MessageSquare className="h-5 w-5" />, label: "Chat" },
            { view: "tasks" as const, icon: <Activity className="h-5 w-5" />, label: "Tasks" },
            { view: "queue" as const, icon: <ListChecks className="h-5 w-5" />, label: "Queue" },
            { view: "analytics" as const, icon: <BarChart2 className="h-5 w-5" />, label: "Analytics" },
            { view: "preview" as const, icon: <Monitor className="h-5 w-5" />, label: "Preview" },
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
    </div>
  );
}
