export interface Agent {
  name: string;
  role: string;
  icon: string;
  description: string;
  color: string;
  system_prompt: string;
  pipeline?: "game";
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
  pipeline_type?: "game";
  project_dir?: string;
  game_project_dir?: string;
  git_enabled?: boolean;
  test_enabled?: boolean;
  max_revisions?: number;
  max_workers?: number;
  tech_expert_pro?: boolean;   // true = Gemini Pro for TechExpert planning
  slow_mode?: boolean;         // true = add delay between subtasks to save quota
}

export interface SessionStatus {
  session_id: string;
  status: "running" | "done" | "error";
  result?: Record<string, unknown>;
}

export interface SessionSummary {
  session_id: string;
  task: string;
  status: "starting" | "running" | "done" | "error" | "audit" | "improve";
  pipeline_type: string;
  pr_url?: string;
  files_count: number;
  created_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface WsEvent {
  type: "progress" | "result" | "error" | "done";
  agent?: string;
  message?: string;
  data?: Record<string, unknown>;
  // top-level fields sent by server on result events
  pr_url?: string;
  files?: string[];
}
