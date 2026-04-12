"""State definitions for the multi-agent system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Phase(str, Enum):
    ANALYZING = "analyzing"
    PLANNING = "planning"
    CODING = "coding"
    REVIEWING = "reviewing"
    PUSHING = "pushing"
    NOTIFYING = "notifying"
    DONE = "done"
    FAILED = "failed"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class Message:
    role: MessageRole
    content: str
    agent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Subtask:
    id: int
    description: str
    files_to_touch: list[str] = field(default_factory=list)  # file paths involved
    status: str = "pending"   # pending | in_progress | done | failed
    code_diff: str = ""       # summary of what was written
    review_feedback: str = ""
    revision_count: int = 0   # revisions for THIS subtask (thread-safe: each subtask owns one thread)
    code_cache_name: str = "" # Gemini context cache name for this subtask's coding loop (empty = no cache)


@dataclass
class AgentState:
    """Shared state flowing through the entire orchestration pipeline."""

    # ── Input ───────────────────────────────────────────
    task: str
    project_dir: str = ""        # absolute path to the target Expo project

    # ── Analysis (pre-planning) ─────────────────────────
    codebase_context: str = ""   # conventions, patterns, libraries — shared with all agents
    existing_errors: str = ""    # tsc errors found before any changes

    # ── Planning ────────────────────────────────────────
    subtasks: list[Subtask] = field(default_factory=list)
    plan_summary: str = ""

    # ── Files (virtual + written to disk) ───────────────
    files_written: list[str] = field(default_factory=list)   # relative paths

    # ── Git ─────────────────────────────────────────────
    branch: str = ""
    commit_sha: str = ""
    pr_url: str = ""
    git_enabled: bool = True

    # ── Run metadata ────────────────────────────────────
    messages: list[Message] = field(default_factory=list)
    current_phase: Phase = Phase.PLANNING
    max_revisions: int = 3
    error: Optional[str] = None

    # ── Progress callback (used by web UI) ──────────────
    # Not serialized — set at runtime
    progress_cb: Optional[Any] = field(default=None, repr=False)

    def log(self, msg: str, agent: str = "") -> None:
        """Append to message log AND call progress_cb if set."""
        self.messages.append(Message(MessageRole.ASSISTANT, msg, agent=agent))
        if self.progress_cb:
            self.progress_cb({"agent": agent, "message": msg, "phase": self.current_phase.value})

    def project_file_list(self, max_files: int = 60) -> str:
        """Return a compact list of existing project files for LLM context."""
        from pathlib import Path
        if not self.project_dir:
            return "No project directory set."
        base = Path(self.project_dir)
        if not base.exists():
            return f"Project dir does not exist: {self.project_dir}"
        files = [
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file()
            and not any(skip in p.parts for skip in [
                "node_modules", ".git", ".expo", "dist", "build", "__pycache__"
            ])
        ]
        files.sort()
        if len(files) > max_files:
            files = files[:max_files] + [f"... ({len(files) - max_files} more)"]
        return "\n".join(files) if files else "Empty project directory."

