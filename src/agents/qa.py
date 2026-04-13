"""QA agent — static code verifier for Mộng Võ Lâm.

Performs static analysis of Dev-written code against:
  - Game-specific rules (combat formula, status effects, Zustand store contract)
  - Architecture invariants (types, Tailwind design tokens, GameBridge protocol)
  - TechExpert test scenarios
  - Common Next.js/React patterns (hooks, Zustand, component hierarchy)

Does NOT run the browser or execute TypeScript — analysis is purely textual.
Uses thinking_budget=1024 for rule-focused verification.

Output format:
  {"passed": bool, "issues": [{"file":"...","severity":"critical|warning|suggestion","description":"..."}],
   "summary": "one-line verdict"}
"""

from __future__ import annotations

import difflib

from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools.filesystem import read_multiple_files
from src.tools.game_tools import run_js_linter


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class _QAIssue(BaseModel):
    file: str
    severity: str          # 'critical' | 'warning' | 'suggestion'
    description: str


class _QAResponse(BaseModel):
    passed: bool
    issues: list[_QAIssue]
    summary: str
    queue_suggestions: list[str] = []  # out-of-scope issues → add as new queue tasks


# ── Agent ─────────────────────────────────────────────────────────────────────

# Inline game rules — embedded in system prompt so QA always has them,
# regardless of whether the context cache contains them.
_GAME_RULES = """
## Combat rules to verify
- Damage formula: rawDmg = ATK * skill.multiplier; final = rawDmg * (DEF_K / (DEF_K + DEF)); crit * 1.5×
- Element advantage: +25% dmg; disadvantage: −15% dmg (Kim>Mộc>Thổ>Thủy>Hỏa>Kim cycle)
- Fury: all heroes start at 25; attacker gains 3–20 per hit (scaled to % HP damage); ultimate costs 100
- Status effects stored as: { type: 'stun'|'poison'|..., remaining: N } matching EffectType in src/types/game.ts
- CombatPayload must include teamA, teamB (HeroSlot[]), stanceA, optional stanceB/stageId/seed
- CombatResult must have: winner ('A'|'B'|'draw'), turns, teamAFinalHp, teamBFinalHp
- Grid slots 0–8: col = slotIndex % 3, row = Math.floor(slotIndex / 3)

## Architecture rules to verify
- All TypeScript types MUST come from src/types/game.ts — flag CRITICAL if a duplicate interface is defined ad-hoc
- Zustand store (useGameStore) is the only place for shared game state — never useState for collection/team/gold
- Tailwind design tokens only: panel, header, gold, gold-dim, label, sub, dim, ok, warn, tier.* — no arbitrary hex colors
- Component hierarchy must be respected: atoms imported by molecules/organisms, NOT the other way around
- All API calls go through src/lib/api/client.ts — flag CRITICAL if fetch() is used directly in a component
- GameBridge communication must use GameBridge.getInstance().sendCommand() and onGameEvent() — no raw postMessage calls
- Next.js App Router routing: useRouter() from next/navigation, redirect() for server redirects — never window.location.href

## Vietnamese UI text rules
- Player-facing strings MUST have full Vietnamese diacritics
- Examples of WRONG: 'Chon', 'Trang bi', 'Doi hinh', 'Khong'
- Examples of CORRECT: 'Chọn', 'Trang bị', 'Đội hình', 'Không'

## React/Next.js patterns
- useCallback for handlers passed to child components or used in useEffect deps
- useMemo for derived state computed from Zustand store slices
- Dynamic imports (next/dynamic with ssr: false) for iframe-dependent components (GameView)
- Keep 'use client' at top of every file using React hooks or browser APIs
"""


class QAAgent(BaseAgent):
    name = "qa"
    system_prompt = (
        "You are a Senior QA Engineer for Mộng Võ Lâm, a Next.js 16 + TypeScript + React + Tailwind + Zustand wuxia card battle RPG.\n"
        "You perform STATIC code analysis — you do not execute code.\n\n"
        "## YOUR PRIMARY JOB\n"
        "Verify that the Dev's changes correctly implement THE SUBTASK DESCRIPTION — nothing more.\n"
        "Stay TIGHTLY SCOPED to what the subtask asked for.\n\n"
        "## Blocking issues (critical/warning)\n"
        "Only flag as critical or warning if the issue is DIRECTLY related to the subtask goal:\n"
        "  - Did Dev implement what was asked?\n"
        "  - Does the changed code break a core architecture rule (types from game.ts, Tailwind tokens, Zustand store, etc.)?\n"
        "  - Does the changed logic have a clear bug in the area being modified?\n\n"
        "## Out-of-scope issues → queue_suggestions\n"
        "If you notice other valid issues OUTSIDE the scope of this subtask (in untouched code,\n"
        "pre-existing patterns, unrelated features), do NOT block on them.\n"
        "Instead, put a concise task description in queue_suggestions[] so they can be fixed\n"
        "in a future task. Example: 'Fix hardcoded hex color in HeroCard.tsx'\n\n"
        + _GAME_RULES
        + "\n\n"
        "## Severity levels\n"
        "- critical: Will cause a crash, game-breaking bug, or convention violation (must fix)\n"
        "- warning: Logic bug or pattern violation that degrades quality (should fix)\n"
        "- suggestion: Minor improvement (optional)\n\n"
        "## Output format\n"
        'Respond ONLY in JSON:\n'
        '{"passed": true/false, '
        '"issues": [{"file":"...","severity":"critical|warning|suggestion","description":"..."}], '
        '"summary": "one-line verdict", '
        '"queue_suggestions": ["concise task description for out-of-scope issues"]}\n\n'
        "Pass = zero critical issues. Warnings are reported to Dev for awareness but do NOT block passing. Suggestions and queue_suggestions do NOT block passing."
    )

    def run(
        self,
        state: GameAgentState,
        subtask: GameSubtask,
        **kwargs,
    ) -> GameAgentState:
        state.current_phase = GamePhase.QA
        state.log(
            f"[Subtask {subtask.id}] QA review — files: {', '.join(subtask.files_to_touch)}",
            agent=self.name,
        )

        # Run objective linter on the files Dev just wrote — gives the LLM
        # concrete syntax/style errors to anchor its analysis.
        linter_output: str = ""
        if state.game_project_dir and subtask.files_to_touch:
            linter_output = run_js_linter(
                state.game_project_dir,
                files=[f for f in subtask.files_to_touch if f.endswith((".ts", ".tsx", ".js"))],
            )
            if "no issues" not in linter_output and "not found" not in linter_output:
                state.log(
                    f"[Subtask {subtask.id}] Linter: {linter_output[:120]}",
                    agent=self.name,
                )

        prompt = self._build_prompt(state, subtask, linter_output=linter_output)
        result = self._call_json(
            prompt,
            response_schema=_QAResponse,
            thinking_budget=1024,  # rule-checking, not deep reasoning
        )

        subtask.qa_issues         = [dict(i) for i in result.get("issues", [])]
        subtask.qa_summary        = result.get("summary", "")
        subtask.queue_suggestions = list(result.get("queue_suggestions", []))

        critical = [i for i in subtask.qa_issues if i.get("severity") == "critical"]
        warnings = [i for i in subtask.qa_issues if i.get("severity") == "warning"]
        blocking_warnings = [w for w in warnings if self._is_blocking_warning(w)]
        too_many_warnings = len(warnings) >= 3
        # Critical always blocks. Warnings can block when high-risk or excessive.
        subtask.qa_passed = (
            len(critical) == 0
            and len(blocking_warnings) == 0
            and not too_many_warnings
        )

        state.log(
            f"[Subtask {subtask.id}] QA {'PASSED' if subtask.qa_passed else 'FAILED'} — "
            f"{len(critical)} critical, {len(warnings)} warning"
            f" ({len(blocking_warnings)} blocking) — {subtask.qa_summary[:80]}",
            agent=self.name,
        )
        if not subtask.qa_passed and len(critical) == 0 and (blocking_warnings or too_many_warnings):
            reasons: list[str] = []
            if blocking_warnings:
                reasons.append(f"{len(blocking_warnings)} high-risk warning(s)")
            if too_many_warnings:
                reasons.append("warning count threshold reached")
            subtask.qa_summary = (
                f"Escalated fail due to warning policy: {', '.join(reasons)}. "
                f"{subtask.qa_summary}"
            ).strip()
        if subtask.queue_suggestions:
            state.log(
                f"[Subtask {subtask.id}] {len(subtask.queue_suggestions)} out-of-scope suggestion(s) queued.",
                agent=self.name,
            )
        return state

    @staticmethod
    def _is_blocking_warning(issue: dict) -> bool:
        """Treat warning as blocking when it maps to a high-risk architecture/runtime break."""
        text = f"{issue.get('file', '')} {issue.get('description', '')}".lower()
        high_risk_markers = (
            "combatengine",
            "phaser import",
            "savemanager",
            "localstorage",
            "scene.start",
            "crisptext",
            "ui_theme",
            "syntax",
            "lint",
            "build",
            "undefined",
        )
        return any(marker in text for marker in high_risk_markers)

    # Required abstract method
    def _call(self, user: str, temperature: float = 0.3, thinking_budget: int = 0) -> str:  # type: ignore[override]
        from src.llm import call as llm_call
        return llm_call(self.system_prompt, user, temperature=temperature, thinking_budget=thinking_budget)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_prompt(self, state: GameAgentState, subtask: GameSubtask, linter_output: str = "") -> str:
        # Prefer in-memory written_files: DevAgent populates this immediately after
        # writing, so it is always in sync with disk.  Using it avoids a disk round-
        # trip and guarantees QA sees exactly what Dev produced — not stale on-disk
        # content from a previous run.
        file_contents = ""
        if subtask.written_files:
            if subtask.original_files:
                # Show unified diff (much smaller than full file) — QA only needs to
                # see what changed to verify rule compliance.
                file_contents = self._make_diff(subtask)
            else:
                # No originals (e.g. brand-new files) — show full content
                file_contents = "\n\n".join(
                    f"=== {path} ===\n{content}"
                    for path, content in subtask.written_files.items()
                )
        elif subtask.files_to_touch and state.game_project_dir:
            # Fallback: files_to_touch but Dev didn't populate written_files
            file_contents = read_multiple_files(
                state.game_project_dir,
                subtask.files_to_touch,
                max_total=80_000,
            )

        # Test scenarios from Tech Expert
        scenarios_block = ""
        if state.test_scenarios:
            scenarios_block = (
                "## Test scenarios to verify\n"
                + "\n".join(f"- {s}" for s in state.test_scenarios)
                + "\n\n"
            )

        constraints_block = ""
        if state.global_constraints:
            constraints_block = (
                "## Constraints from Tech Expert\n"
                + "\n".join(f"- {c}" for c in state.global_constraints)
                + "\n\n"
            )

        # Previous QA issues (on revision rounds)
        # Explicit scope reminder — keep QA tightly focused
        scope_block = (
            f"## Scope of this review\n"
            f"ONLY verify that the changes correctly implement: **{subtask.description}**\n"
            f"Flag issues in CHANGED lines only, unless they are a hard architecture violation.\n"
            f"Pre-existing issues in unchanged code → put in queue_suggestions, not issues[].\n\n"
        )

        prev_issues_block = ""
        if subtask.revision_count > 0 and subtask.qa_issues:
            prev_lines = [
                f"  - [{i['severity'].upper()}] {i.get('file','')} — {i['description']}"
                for i in subtask.qa_issues
            ]
            prev_issues_block = (
                f"## Previous QA issues (check if FIXED in new code)\n"
                + "\n".join(prev_lines)
                + "\n\n"
            )

        linter_block = ""
        if linter_output and "no issues" not in linter_output and "not found" not in linter_output:
            linter_block = f"## Objective linter output (syntax/style — already on disk)\n{linter_output}\n\n"

        parts = [
            f"## Subtask description\n{subtask.description}\n\n",
            scope_block,
            scenarios_block,
            constraints_block,
            prev_issues_block,
            linter_block,
            f"## Code changes by Dev (unified diff)\n{file_contents or '[No files written]'}\n\n",
            "Analyze the changes above and return your QA verdict.",
        ]
        return "".join(parts)

    @staticmethod
    def _make_diff(subtask: "GameSubtask") -> str:  # type: ignore[name-defined]
        """Compute unified diffs between originals and written files."""
        parts: list[str] = []
        for path, new_content in subtask.written_files.items():
            original = subtask.original_files.get(path, "")
            if not original:
                # Brand-new file — show first 80 lines as preview
                lines = new_content.splitlines()
                preview = "\n".join(lines[:80])
                suffix = f"\n... [{len(lines) - 80} more lines]" if len(lines) > 80 else ""
                parts.append(f"=== NEW FILE: {path} ===\n{preview}{suffix}")
                continue
            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=5,
            ))
            if diff_lines:
                parts.append(f"=== DIFF {path} ===\n{''.join(diff_lines)}")
            else:
                parts.append(f"=== {path} (no changes) ===")
        return "\n\n".join(parts)
