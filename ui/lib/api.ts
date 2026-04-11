import type { Agent, RunRequest, SessionStatus, SessionSummary, ChatMessage, ChatResponse, SessionTokenUsage, AnalyticsData, AgentAnalyticsData, QueueItem, SchedulerStatus, PreviewInfo } from "@/types";

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

export async function fetchAgents(pipeline?: "expo" | "game"): Promise<Agent[]> {
  const url = pipeline ? `${API_BASE}/agents?pipeline=${pipeline}` : `${API_BASE}/agents`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch agents");
  return res.json();
}

export async function startRun(req: RunRequest): Promise<{ session_id: string }> {
  const res = await fetch(`${API_BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw new Error("Failed to start run");
  return res.json();
}

export async function fetchStatus(sessionId: string): Promise<SessionStatus> {
  const res = await fetch(`${API_BASE}/status/${sessionId}`);
  if (!res.ok) throw new Error("Failed to fetch status");
  return res.json();
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const res = await fetch(`${API_BASE}/sessions`);
  if (!res.ok) throw new Error("Failed to fetch sessions");
  return res.json();
}

export async function sendChat(
  message: string,
  chatId?: string,
  model: "flash" | "pro" = "flash",
): Promise<ChatResponse> {
  const browserProxy = typeof window !== "undefined";
  const chatUrl = browserProxy ? "/api/chat/" : `${API_BASE}/chat`;

  try {
    const res = await fetch(chatUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, chat_id: chatId ?? "", model }),
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
  await fetch(`${API_BASE}/stop/${sessionId}`, { method: "POST" });
}

export async function startAudit(
  auditType: "audit" | "improve",
  gameProjectDir?: string,
): Promise<{ session_id: string }> {
  const res = await fetch(`${API_BASE}/audit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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
    ? `${API_BASE}/analytics/${sessionId}`
    : `${API_BASE}/analytics`;
  const res = await fetch(url);
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
    ? `${API_BASE}/analytics/agents/${sessionId}`
    : `${API_BASE}/analytics/agents`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch agent analytics");
  return res.json();
}

// ── Task Queue ────────────────────────────────────────────────────────────────

export async function fetchQueue(): Promise<QueueItem[]> {
  const res = await fetch(`${API_BASE}/queue`);
  if (!res.ok) throw new Error("Failed to fetch queue");
  return res.json();
}

export async function addQueueTask(
  task: string,
  priority = 5,
  pipeline_type = "game",
): Promise<QueueItem> {
  const res = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, priority, pipeline_type }),
  });
  if (!res.ok) throw new Error("Failed to add queue task");
  return res.json();
}

export async function deleteQueueTask(taskId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/queue/${taskId}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Failed to delete queue task");
}

export async function cancelQueueTask(taskId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/queue/${taskId}/cancel`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to cancel task");
  }
}

export async function runQueueTask(taskId: number): Promise<{ session_id: string; ws_url: string }> {
  const res = await fetch(`${API_BASE}/queue/${taskId}/run`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to start task");
  }
  return res.json();
}

export async function clearAllQueue(): Promise<{ removed: number }> {
  const res = await fetch(`${API_BASE}/queue/clear-all`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to clear queue");
  return res.json();
}

export async function clearDoneQueue(): Promise<{ removed: number }> {
  const res = await fetch(`${API_BASE}/queue/clear-done`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to clear done tasks");
  return res.json();
}

// ── Scheduler ─────────────────────────────────────────────────────────────────

export async function fetchSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await fetch(`${API_BASE}/scheduler/status`);
  if (!res.ok) throw new Error("Failed to fetch scheduler status");
  return res.json();
}

export async function toggleScheduler(): Promise<{ enabled: boolean }> {
  const res = await fetch(`${API_BASE}/scheduler/toggle`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to toggle scheduler");
  return res.json();
}

export async function triggerSchedulerNow(): Promise<void> {
  const res = await fetch(`${API_BASE}/scheduler/trigger`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to trigger scheduler");
  }
}

// ── Game Preview ──────────────────────────────────────────────────────────────

export async function fetchPreviewInfo(): Promise<PreviewInfo> {
  const res = await fetch(`${API_BASE}/preview/info`);
  if (!res.ok) throw new Error("Failed to fetch preview info");
  return res.json();
}

export async function checkoutPreviewBranch(branch: string): Promise<void> {
  const res = await fetch(`${API_BASE}/preview/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ branch }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: "Unknown error" }));
    throw new Error(err.error ?? "Failed to checkout branch");
  }
}
