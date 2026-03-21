export interface Agent {
  name: string;
  role: string;
  icon: string;
  description: string;
  color: string;
  system_prompt: string;
}

export interface FeedLine {
  id: number;
  timestamp: string;
  agent: string;
  agentColor: string;
  agentIcon: string;
  type: "progress" | "result" | "error" | "done" | "info";
  message: string;
}

export interface PipelineStage {
  id: string;
  label: string;
  icon: string;
  status: "pending" | "active" | "done" | "error";
}

export interface RunRequest {
  task: string;
  dry_run?: boolean;
  skip_tests?: boolean;
}

export interface SessionStatus {
  session_id: string;
  status: "running" | "done" | "error";
  result?: Record<string, unknown>;
}

export interface WsEvent {
  type: "progress" | "result" | "error" | "done";
  agent?: string;
  message?: string;
  data?: Record<string, unknown>;
}
