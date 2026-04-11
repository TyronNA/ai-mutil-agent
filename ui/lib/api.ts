import type { Agent, RunRequest, SessionStatus, SessionSummary, ChatMessage, ChatResponse, ChatCharacter, SessionTokenUsage, AnalyticsData, AgentAnalyticsData, QueueItem, SchedulerStatus, PreviewInfo } from "@/types";

export type AuthStatus = {
  authenticated: boolean;
  configured: boolean;
};

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function resolveEnvApiBase(envBase: string): string {
  const trimmed = envBase.trim().replace(/\/$/, "");
  if (!trimmed) return trimmed;

  if (typeof window === "undefined") {
    return trimmed;
  }

  try {
    const parsed = new URL(trimmed);
    const browserHost = window.location.hostname;

    // Mobile devices cannot resolve the dev machine through localhost.
    if (isLoopbackHost(parsed.hostname) && !isLoopbackHost(browserHost)) {
      parsed.hostname = browserHost;
    }

    return parsed.toString().replace(/\/$/, "");
  } catch {
    return trimmed;
  }
}

function resolveApiBase(): string {
  const envBase = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (envBase) return resolveEnvApiBase(envBase);

  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }

  return "http://localhost:8000";
}

export const API_BASE = resolveApiBase();

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  return res;
}

export async function checkAuth(): Promise<AuthStatus> {
  const res = await apiFetch("/auth/me", { method: "GET" });
  if (!res.ok) {
    return { authenticated: false, configured: false };
  }
  return res.json();
}

export async function loginWithApiKey(apiKey: string): Promise<void> {
  const res = await apiFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Login failed" }));
    throw new Error(err.error ?? "Login failed");
  }
}

export async function logout(): Promise<void> {
  await apiFetch("/auth/logout", { method: "POST" });
}

export async function fetchAgents(pipeline?: "expo" | "game"): Promise<Agent[]> {
  const url = pipeline ? `/agents?pipeline=${pipeline}` : "/agents";
  const res = await apiFetch(url);
  if (!res.ok) throw new Error("Failed to fetch agents");
  return res.json();
}

export async function startRun(req: RunRequest): Promise<{ session_id: string }> {
  const res = await apiFetch("/run", {
    method: "POST",
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error("Failed to start run");
  return res.json();
}

export async function fetchStatus(sessionId: string): Promise<SessionStatus> {
  const res = await apiFetch(`/status/${sessionId}`);
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const res = await apiFetch("/sessions");
  if (!res.ok) throw new Error("Failed to fetch sessions");
  return res.json();
}

export async function sendChat(
  message: string,
  chatId?: string,
  character: ChatCharacter = "tech_expert",
  model: "flash" | "pro" = "flash",
): Promise<ChatResponse> {
  try {
    const res = await apiFetch("/chat", {
      method: "POST",
      body: JSON.stringify({ message, chat_id: chatId ?? "", character, model }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: "Chat request failed" }));
      throw new Error(err.error ?? "Chat request failed");
    }
    return res.json();
  } catch (err) {
    if (err instanceof Error) {
      throw err;
    }
    throw new Error("Chat request failed");
  }
}

export async function stopSession(sessionId: string): Promise<void> {
  await apiFetch(`/stop/${sessionId}`, { method: "POST" });
}

export async function startAudit(
  auditType: "audit" | "improve",
  gameProjectDir?: string,
): Promise<{ session_id: string }> {
  const res = await apiFetch("/audit", {
    method: "POST",
    body: JSON.stringify({ audit_type: auditType, game_project_dir: gameProjectDir ?? "" }),
  });
  if (!res.ok) throw new Error("Audit request failed");
  return res.json();
}

export function createWebSocket(sessionId: string): WebSocket {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return new WebSocket(`${wsBase}/ws/${sessionId}`);
}

export async function fetchAnalytics(sessionId?: string): Promise<AnalyticsData> {
  const url = sessionId
    ? `/analytics/${sessionId}`
    : "/analytics";
  const res = await apiFetch(url);
  if (!res.ok) throw new Error("Failed to fetch analytics");
  if (sessionId) {
    // Single-session response — wrap in aggregate format
    const u = await res.json() as SessionTokenUsage;
    return { aggregate: u, sessions: [u] };
  }
  return res.json();
}

export async function fetchAgentAnalytics(sessionId?: string): Promise<AgentAnalyticsData> {
  const url = sessionId
    ? `/analytics/agents/${sessionId}`
    : "/analytics/agents";
  const res = await apiFetch(url);
  if (!res.ok) throw new Error("Failed to fetch agent analytics");
  return res.json();
}

// ── Task Queue ────────────────────────────────────────────────────────────────

export async function fetchQueue(): Promise<QueueItem[]> {
  const res = await apiFetch("/queue");
  if (!res.ok) throw new Error("Failed to fetch queue");
  return res.json();
}

export async function addQueueTask(
  task: string,
  priority = 5,
  pipeline_type = "game",
): Promise<QueueItem> {
  const res = await apiFetch("/queue", {
    method: "POST",
    body: JSON.stringify({ task, priority, pipeline_type }),
  });
  if (!res.ok) throw new Error("Failed to add queue task");
  return res.json();
}

export async function deleteQueueTask(taskId: number): Promise<void> {
  const res = await apiFetch(`/queue/${taskId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete queue task");
}

export async function cancelQueueTask(taskId: number): Promise<void> {
  const res = await apiFetch(`/queue/${taskId}/cancel`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to cancel task");
  }
}

export async function runQueueTask(taskId: number): Promise<{ session_id: string; ws_url: string }> {
  const res = await apiFetch(`/queue/${taskId}/run`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to start task");
  }
  return res.json();
}

export async function resumeQueueTask(taskId: number, errorLog = ""): Promise<{ ok: boolean; status: string }> {
  const res = await apiFetch(`/queue/${taskId}/resume`, {
    method: "POST",
    body: JSON.stringify({ error_log: errorLog }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to resume task");
  }
  return res.json();
}

export async function clearAllQueue(): Promise<{ removed: number }> {
  const res = await apiFetch("/queue/clear-all", { method: "POST" });
  if (!res.ok) throw new Error("Failed to clear queue");
  return res.json();
}

export async function clearDoneQueue(): Promise<{ removed: number }> {
  const res = await apiFetch("/queue/clear-done", { method: "POST" });
  if (!res.ok) throw new Error("Failed to clear done tasks");
  return res.json();
}

// ── Scheduler ─────────────────────────────────────────────────────────────────

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await apiFetch("/scheduler/status");
  if (!res.ok) throw new Error("Failed to fetch scheduler status");
  return res.json();
}

export async function toggleScheduler(): Promise<{ enabled: boolean }> {
  const res = await apiFetch("/scheduler/toggle", { method: "POST" });
  if (!res.ok) throw new Error("Failed to toggle scheduler");
  return res.json();
}

export async function triggerSchedulerNow(): Promise<void> {
  const res = await apiFetch("/scheduler/trigger", { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to trigger scheduler");
  }
}

// ── Game Preview ──────────────────────────────────────────────────────────────

export async function fetchPreviewInfo(): Promise<PreviewInfo> {
  const res = await apiFetch("/preview/info");
  if (!res.ok) throw new Error("Failed to fetch preview info");
  return res.json();
}

export async function checkoutPreviewBranch(branch: string): Promise<void> {
  const res = await apiFetch("/preview/checkout", {
    method: "POST",
    body: JSON.stringify({ branch }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to checkout branch");
  }
}
