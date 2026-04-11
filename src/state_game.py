"""Game-specific state for the Mộng Võ Lâm multi-agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class GamePhase(str, Enum):
    LOADING   = "loading"    # Loading game source context
    PLANNING  = "planning"   # TechExpert producing implementation plan
    CODING    = "coding"     # Dev writing code
    QA        = "qa"         # QA verifying code
    REVIEWING = "reviewing"  # TechExpert final review
    PUSHING   = "pushing"    # Git commit + PR
    DONE      = "done"
    FAILED    = "failed"


@dataclass
class GameSubtask:
    """One unit of work assigned to the Dev + QA pair."""

    id: int
    description: str
    files_to_touch: list[str] = field(default_factory=list)

    # Execution state
    status: str = "pending"     # pending | in_progress | qa_review | revision | done | failed
    revision_count: int = 0

    # Dev output
    written_files: dict[str, str] = field(default_factory=dict)   # rel_path → full content after patches
    original_files: dict[str, str] = field(default_factory=dict)  # rel_path → content before any Dev writes
    code_summary: str = ""

    # QA output
    qa_passed: bool = False
    qa_issues: list[dict] = field(default_factory=list)           # [{file, severity, description}]
    qa_summary: str = ""

    # Patch failure tracking — populated by DevAgent when a 'find' doesn't match;
    # injected back into the next revision prompt so Dev can correct the context.
    patch_failures: dict[str, list[dict]] = field(default_factory=dict)  # rel_path → [failed_patch, ...]

    # Gemini context cache (reused across Dev revisions for this subtask)
    code_cache_name: str = ""


@dataclass
class GameAgentState:
    """Shared mutable state flowing through the entire game agent pipeline."""

    # ── Input ────────────────────────────────────────────────────────────────
    task: str
    game_project_dir: str          # absolute path to mong-vo-lam/

    # ── Game context (loaded by GameLoader) ──────────────────────────────────
    game_context: str = ""          # static tier: conventions + config (cached or inline)
    game_dynamic_context: str = ""  # dynamic tier: classes + scenes (inline for TechExpert only)
    context_cache_name: str = ""    # Gemini cache name (empty = no cache, use inline)

    # ── TechExpert plan ───────────────────────────────────────────────────────
    implementation_plan: str = ""
    subtasks: list[GameSubtask] = field(default_factory=list)
    test_scenarios: list[str] = field(default_factory=list)      # for QA agent
    global_constraints: list[str] = field(default_factory=list)  # rules Dev must follow

    # ── Files written to disk ────────────────────────────────────────────────
    files_written: list[str] = field(default_factory=list)       # relative paths

    # ── TechExpert final review ───────────────────────────────────────────────
    review_verdict: str = ""       # 'approved' | 'needs_revision' | 'rejected'
    review_notes: str = ""
    review_specific_issues: list[str] = field(default_factory=list)

    # ── Git / PR ─────────────────────────────────────────────────────────────
    branch: str = ""
    commit_sha: str = ""
    pr_url: str = ""
    git_enabled: bool = True

    # ── Run metadata ─────────────────────────────────────────────────────────
    current_phase: GamePhase = GamePhase.LOADING
    error: Optional[str] = None
    max_revisions: int = 3
    messages: list[dict] = field(default_factory=list)

    # ── Cross-run lessons (loaded from disk before pipeline starts) ───────────
    # Contains lessons captured from previous runs: frequently violated rules,
    # files that needed many revisions, patch failure patterns, etc.
    lessons_context: str = ""

    # ── Progress callback (web UI) ────────────────────────────────────────────
    progress_cb: Optional[Any] = field(default=None, repr=False)

    def log(self, msg: str, agent: str = "") -> None:
        """Append to message log and call progress_cb if set."""
        entry = {"agent": agent, "message": msg, "phase": self.current_phase.value}
        self.messages.append(entry)
        if self.progress_cb:
            self.progress_cb(entry)

    def game_file_list(self, max_files: int = 80) -> str:
        """Return a compact file list of the game project for LLM context."""
        from pathlib import Path
        if not self.game_project_dir:
            return "No game project directory set."
        base = Path(self.game_project_dir)
        if not base.exists():
            return f"Game project dir does not exist: {self.game_project_dir}"
        skip = {
            "node_modules", ".git", "dist", "build", "__pycache__",
            ".vite", ".turbo", ".cache", "coverage", ".nyc_output",
        }
        files = sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file() and not any(s in p.parts for s in skip)
        )
        if len(files) > max_files:
            files = files[:max_files] + [f"... ({len(files) - max_files} more)"]
        return "\n".join(files)
