import type { Agent, RunRequest, SessionStatus, SessionSummary, ChatMessage } from "@/types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
): Promise<{ chat_id: string; response: string; history: ChatMessage[] }> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, chat_id: chatId ?? "", model }),
  });
  if (!res.ok) throw new Error("Chat request failed");
  return res.json();
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
