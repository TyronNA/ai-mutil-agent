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
  status: "starting" | "running" | "done" | "error" | "audit" | "improve" | "stopping";
  pipeline_type: string;
  pr_url?: string;
  files_count: number;
  created_at: string;
  subtasks?: SubtaskInfo[];
  calls?: number;
  cost_usd?: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type ChatCharacter = "tech_expert" | "mate";

export interface ChatResponse {
  chat_id: string;
  response: string;
  history: ChatMessage[];
  character?: ChatCharacter | string;
  requested_model?: "flash" | "pro" | string;
  effective_model?: string;
  downgraded_to_flash?: boolean;
}

export interface SessionTokenUsage {
  session_id: string;
  calls: number;
  flash_calls: number;
  pro_calls: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  cost_usd: number;
  task?: string;
  status?: string;
  created_at?: string;
  pricing: {
    flash_input_per_1m: number;
    flash_output_per_1m: number;
    flash_cached_per_1m: number;
    pro_input_per_1m?: number;
    pro_output_per_1m?: number;
  };
}

export interface AnalyticsData {
  aggregate: SessionTokenUsage;
  sessions: SessionTokenUsage[];
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

export interface SubtaskInfo {
  id: number;
  description: string;
  files_to_touch: string[];
  status: "pending" | "in_progress" | "qa_review" | "revision" | "done" | "failed" | string;
  revision_count: number;
  qa_passed?: boolean | null;
}

export interface AgentUsage {
  session_id?: string | null;
  agent_name: string;
  calls: number;
  prompt_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface AgentAnalyticsData {
  agents: AgentUsage[];
  total_cost_usd: number;
  session_id?: string;
}

export interface QueueItem {
  id: number;
  task: string;
  pipeline_type: string;
  status: "pending" | "waiting" | "running" | "done" | "failed" | "blocked" | "skipped";
  source: "manual" | "audit" | "improve" | string;
  priority: number;
  session_id?: string | null;
  branch?: string | null;
  created_at: string;
  updated_at: string;
}

export interface PreviewInfo {
  game_dir: string;
  current_branch: string;
  branches: string[];
}

export interface SchedulerStatus {
  running: boolean;
  last_run: string | null;
  next_run: string | null;
  enabled: boolean;
  interval_hours: number;
}
