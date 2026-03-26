"""Analyzer agent — audits the codebase BEFORE planning begins.

Responsibilities:
1. Read actual source files (components, hooks, lib, utils, screens) to extract
   real coding conventions — not assumed ones.
2. Run `tsc --noEmit` to detect pre-existing TypeScript errors so the Coder knows
   what was already broken before it touched anything.
3. Store results in AgentState.codebase_context and AgentState.existing_errors so
   ALL downstream agents (Planner, Coder, Reviewer) receive grounded context.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.state import AgentState, Phase
from src.tools import filesystem

_SOURCE_EXTENSIONS = {".tsx", ".ts", ".js", ".jsx"}
# Directories whose files best represent conventions and patterns
_PRIORITY_DIRS = ("components", "hooks", "lib", "utils", "services", "store", "context", "screens")
_MAX_SOURCE_FILES = 15
_MAX_SOURCE_CHARS = 80_000
_CONFIG_FILES = [
    "package.json", "tsconfig.json", "app.json", "app.config.ts",
    ".eslintrc.js", ".eslintrc.json", "eslint.config.js",
]


class _AnalysisResponse(BaseModel):
    conventions: str
    libraries: list[str]
    patterns: str
    architecture_notes: str


class AnalyzerAgent(BaseAgent):
    name = "analyzer"
    system_prompt = (
        "You are a senior software architect auditing an existing codebase.\n"
        "Given source files and config, extract:\n"
        "1. Coding conventions: naming style, file structure, TypeScript patterns, import aliases\n"
        "2. Libraries actively imported (from actual import statements, not just package.json)\n"
        "3. Architecture patterns: routing, state management, data fetching, styling approach\n"
        "4. Architecture notes: non-obvious facts any developer writing new code MUST know\n\n"
        'Respond in JSON: {"conventions": "...", "libraries": [...], "patterns": "...", "architecture_notes": "..."}'
    )

    def run(self, state: AgentState, **kwargs) -> AgentState:
        state.current_phase = Phase.ANALYZING
        state.log("Analyzing codebase — extracting conventions and checking for errors...", agent=self.name)

        if not state.project_dir:
            state.log("No project_dir set — skipping analysis", agent=self.name)
            return state

        # ── 1. Detect pre-existing TypeScript errors ─────────────────────────
        state.existing_errors = self._detect_errors(state.project_dir)
        if state.existing_errors:
            state.log("Pre-existing TypeScript errors detected (logged to codebase_context)", agent=self.name)

        # ── 2. Read source files for convention extraction ────────────────────
        source_files = self._pick_source_files(state.project_dir)
        file_contents = filesystem.read_multiple_files(
            state.project_dir, source_files, max_total=_MAX_SOURCE_CHARS
        )

        config_contents = filesystem.read_multiple_files(
            state.project_dir, _CONFIG_FILES, max_total=20_000
        )

        if not file_contents.strip() and not config_contents.strip():
            state.log("No readable source files — skipping convention extraction", agent=self.name)
            return state

        # ── 3. Ask Gemini to extract conventions ─────────────────────────────
        prompt = (
            f"## Config files\n{config_contents}\n\n"
            f"## Source files\n{file_contents}\n\n"
            "Extract coding conventions, active libraries, architecture patterns, and key notes."
        )
        result = self._call_json(prompt, response_schema=_AnalysisResponse, thinking_budget=2048)

        conventions = result.get("conventions", "")
        libraries = result.get("libraries", [])
        patterns = result.get("patterns", "")
        arch_notes = result.get("architecture_notes", "")

        # ── 4. Build the shared codebase_context string ───────────────────────
        parts: list[str] = []
        if conventions:
            parts.append(f"### Coding conventions\n{conventions}")
        if libraries:
            parts.append(f"### Libraries in active use\n{', '.join(libraries)}")
        if patterns:
            parts.append(f"### Architecture patterns\n{patterns}")
        if arch_notes:
            parts.append(f"### Architecture notes\n{arch_notes}")
        if state.existing_errors:
            parts.append(
                f"### ⚠️ Pre-existing TypeScript errors (present BEFORE any changes)\n"
                f"{state.existing_errors}"
            )

        state.codebase_context = "\n\n".join(parts)

        state.log(
            f"Analysis complete — {len(source_files)} source files read, "
            f"{len(libraries)} libraries found"
            + (", pre-existing TS errors noted" if state.existing_errors else ""),
            agent=self.name,
        )
        return state

    def _pick_source_files(self, project_dir: str) -> list[str]:
        """Pick the most representative source files for convention extraction.

        Priority: files in components/, hooks/, lib/, utils/, store/, etc.
        Then app/ screens. Then everything else. Cap at _MAX_SOURCE_FILES.
        """
        base = Path(project_dir)
        skip_dirs = {"node_modules", ".git", ".expo", "dist", "build", "__pycache__", ".next"}

        priority: list[str] = []
        secondary: list[str] = []

        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix not in _SOURCE_EXTENSIONS:
                continue
            if any(skip in p.parts for skip in skip_dirs):
                continue
            rel = str(p.relative_to(base))
            if any(d in p.parts for d in _PRIORITY_DIRS):
                priority.append(rel)
            else:
                secondary.append(rel)

        combined = priority + secondary
        return combined[:_MAX_SOURCE_FILES]

    def _detect_errors(self, project_dir: str) -> str:
        """Run tsc --noEmit to capture pre-existing TypeScript errors."""
        tsconfig = Path(project_dir) / "tsconfig.json"
        if not tsconfig.exists():
            return ""
        try:
            result = subprocess.run(
                ["npx", "--yes", "tsc", "--noEmit", "--pretty", "false"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=45,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0 and output:
                lines = output.splitlines()
                truncated = "\n".join(lines[:50])
                if len(lines) > 50:
                    truncated += f"\n... ({len(lines) - 50} more lines)"
                return truncated
            return ""
        except Exception:
            return ""
