"""TechExpert agent — game architect using Gemini models.

Responsibilities:
1. PLAN  — decompose the task into concrete subtasks with file assignments,
           global constraints Dev must follow, and test scenarios for QA.
2. REVIEW — final review of all written code after Dev + QA loops finish;
            either approves or flags remaining issues.

Uses Gemini Flash by default, and Gemini Pro when pro planning is enabled.
Context cache (built by GameLoader) is reused here to save tokens.
"""

from __future__ import annotations

from pydantic import BaseModel

from src.tools.search import extract_task_keywords, search_code

from src.agents.base import BaseAgent
from src.state_game import GameAgentState, GamePhase, GameSubtask


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class _SubtaskSpec(BaseModel):
    id: int
    description: str
    files_to_touch: list[str] = []


class _PlanResponse(BaseModel):
    implementation_plan: str
    subtasks: list[_SubtaskSpec]
    test_scenarios: list[str]
    global_constraints: list[str]


class _ReviewResponse(BaseModel):
    verdict: str              # 'approved' | 'needs_revision' | 'rejected'
    notes: str
    specific_issues: list[str]


# ── Agent ─────────────────────────────────────────────────────────────────────

class TechExpertAgent(BaseAgent):
    """Senior game architect — plans and reviews, never writes code directly.

    Args:
        pro_planning: If True, requests pro mode for plan().
                      review() always uses Flash — it's reading diff output, not complex reasoning.
    """

    name = "tech_expert"
    system_prompt = (
        "You are the Lead Technical Expert for Mộng Võ Lâm, a Phaser 4 + Vite H5 wuxia card battle RPG.\n"
        "Your role: game architect and technical reviewer — you DO NOT write code.\n\n"
        "## Your responsibilities\n"
        "When PLANNING a task:\n"
        "- Decompose into at most 5 ordered subtasks (each must be independently implementable)\n"
        "- Identify EXACTLY which files need to be created or modified per subtask\n"
        "- List global constraints the Dev agent MUST enforce (conventions, invariants, edge cases)\n"
        "- Write specific test scenarios the QA agent will check\n\n"
        "When REVIEWING code:\n"
        "- Check against CLAUDE.md conventions and architecture rules\n"
        "- Verify CombatEngine isolation (zero Phaser imports allowed)\n"
        "- Verify UI_THEME palette usage (no blue/navy/teal in game UI)\n"
        "- Verify Vietnamese UI text has full diacritics\n"
        "- Verify SaveManager.load() → modify → save() pattern (no direct localStorage)\n"
        "- Verify crispText() used for all scene text (not scene.add.text())\n"
        "- Verify gotoScene() used for transitions (not this.scene.start())\n"
        "- Flag any logic bugs in combat, gacha, or save migration\n\n"
        "## Key architecture rules you enforce\n"
        "- CombatEngine.js = pure JavaScript ONLY, zero Phaser imports ever\n"
        "- statMods for temporary buffs (multiplicative), never mutate base stats\n"
        "- actionResult contract must remain stable (attacker/target/damage/isDead/winner structure)\n"
        "- SaveManager is the ONLY source of truth — never read localStorage directly\n"
        "- All UI panels use UI_THEME from constants.js — no ad-hoc hex colors\n"
        "- crispText() for all scene.add.text() calls\n"
        "- gotoScene(this, 'Key', data) for all scene transitions\n\n"
        'When PLANNING respond in JSON:\n'
        '{"implementation_plan":"...","subtasks":[{"id":1,"description":"...","files_to_touch":["src/..."]}],'
        '"test_scenarios":["..."],"global_constraints":["..."]}\n\n'
        'When REVIEWING respond in JSON:\n'
        '{"verdict":"approved|needs_revision|rejected","notes":"...","specific_issues":[\n'
        '  "src/path/File.js > functionName() line ~N: <what is wrong> — fix: <exact change needed>"\n'
        ']}\n'
        "Each entry in specific_issues MUST include: file path, function/line location, description, and a concrete fix.\n"
        'Example: "src/scenes/BattleScene.js > applyDamage() line ~87: imports Phaser directly — fix: remove import Phaser line and use local ref instead"'
    )

    chat_system_prompt = (
        "You are the Lead Technical Expert for Mộng Võ Lâm, a Phaser 4 + Vite H5 wuxia card battle RPG.\n"
        "In chat mode, discuss like a senior technical architect speaking to engineers.\n"
        "Use clear natural language, practical trade-offs, and concrete reasoning.\n"
        "Do not answer in JSON, YAML, or rigid template format unless the user explicitly asks for it.\n"
        "When asked for a plan, provide a structured but human-readable plan with priorities, risks, and next actions.\n"
        "Always respect project invariants: CombatEngine purity, UI_THEME usage, SaveManager load-modify-save flow, "
        "crispText(), gotoScene(), and full Vietnamese diacritics."
    )

    def __init__(self, pro_planning: bool = False) -> None:
        super().__init__()
        self.pro_planning = pro_planning

    # ── Public API ────────────────────────────────────────────────────────────

    def plan(self, state: GameAgentState) -> GameAgentState:
        """Phase 1: decompose task → subtasks + constraints + test scenarios."""
        state.current_phase = GamePhase.PLANNING
        state.log("Producing implementation plan...", agent=self.name)

        prompt = self._build_plan_prompt(state)
        result = self._call_json(
            prompt,
            response_schema=_PlanResponse,
            cached_content=state.context_cache_name or None,
            thinking_budget=8192 if self.pro_planning else 4096,
            pro=self.pro_planning,
        )

        state.implementation_plan  = result.get("implementation_plan", "")
        state.test_scenarios       = result.get("test_scenarios", [])
        state.global_constraints   = result.get("global_constraints", [])
        state.subtasks = [
            GameSubtask(
                id=s["id"],
                description=s["description"],
                files_to_touch=s.get("files_to_touch", []),
            )
            for s in result.get("subtasks", [])
        ]

        state.log(
            f"Plan ready — {len(state.subtasks)} subtasks, "
            f"{len(state.test_scenarios)} test scenarios, "
            f"{len(state.global_constraints)} constraints.",
            agent=self.name,
        )
        return state

    def review(self, state: GameAgentState) -> GameAgentState:
        """Phase 4: final review of all written code."""
        state.current_phase = GamePhase.REVIEWING
        state.log("Final review of all written files...", agent=self.name)

        prompt = self._build_review_prompt(state)
        result = self._call_json(
            prompt,
            response_schema=_ReviewResponse,
            cached_content=state.context_cache_name or None,
            thinking_budget=1024,  # needs reasoning to catch logic bugs and verify task completion
            pro=False,
        )

        state.review_verdict         = result.get("verdict", "approved")
        state.review_notes           = result.get("notes", "")
        state.review_specific_issues = result.get("specific_issues", [])

        state.log(
            f"Review verdict: {state.review_verdict} — {state.review_notes[:100]}",
            agent=self.name,
        )
        return state

    # Required by BaseAgent but not used directly
    def run(self, state, **kwargs):
        raise NotImplementedError("Use .plan() or .review() directly")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_plan_prompt(self, state: GameAgentState) -> str:
        parts = [f"## Task\n{state.task}\n"]

        # File tree is always small and essential — always include
        parts.append(f"## Project file tree\n{state.game_file_list()}\n")

        if not state.context_cache_name:
            # No cache — include static conventions inline (small: CLAUDE.md + constants)
            if state.game_context:
                parts.append(f"## Project conventions & config\n{state.game_context}\n")

        # Targeted search FIRST — task-relevant snippets are far cheaper than full source dump.
        # Dynamic context (~120K chars) is only sent as fallback when search returns nothing.
        search_results: list[str] = []
        if state.game_project_dir:
            keywords = extract_task_keywords(state.task)
            if keywords:
                for kw in keywords[:3]:  # top-3 keywords max
                    hits = search_code(state.game_project_dir, kw, max_results=15)
                    if "[no matches" not in hits:
                        search_results.append(f"### search: `{kw}`\n{hits}")
        if search_results:
            parts.append("## Code search results (relevant to task)\n" + "\n\n".join(search_results) + "\n")
        elif state.game_dynamic_context:
            # Fallback: no search hits — include full source so TechExpert can locate files
            parts.append(f"## Current source code (classes & scenes)\n{state.game_dynamic_context}\n")

        # Cross-run lessons — inject known problem patterns from previous runs
        if state.lessons_context:
            parts.append(
                "## Lessons from previous runs (known pitfalls — plan to avoid these)\n"
                + state.lessons_context
                + "\n"
            )

        max_st = getattr(state, "max_subtasks", 5)
        parts.append(
            f"Decompose this task into AT MOST {max_st} subtask(s) — "
            f"prefer fewer subtasks when possible (combine related changes into one). "
            "List the exact files that must be created or modified per subtask. "
            "Follow CLAUDE.md conventions strictly."
        )
        return "\n".join(parts)

    def _build_review_prompt(self, state: GameAgentState) -> str:
        from src.tools.filesystem import read_multiple_files

        # Read all files written during this run for review
        written_content = read_multiple_files(
            state.game_project_dir,
            state.files_written,
            max_total=100_000,
        ) if state.files_written else "[No files written]"

        # Summarise QA outcomes per subtask
        qa_summary_lines = []
        for st in state.subtasks:
            status = "✅ passed" if st.qa_passed else f"⚠️ {len(st.qa_issues)} issue(s)"
            qa_summary_lines.append(f"  Subtask {st.id}: {status} — {st.qa_summary[:80]}")
        qa_summary = "\n".join(qa_summary_lines) or "  (no QA data)"

        parts = [
            f"## Original task\n{state.task}\n",
            f"## Implementation plan\n{state.implementation_plan}\n",
            f"## QA results per subtask\n{qa_summary}\n",
            f"## Written files\n{written_content}\n",
            "## Review instructions\n"
            "1. Was the ORIGINAL TASK actually solved by these changes? If no, verdict=rejected.\n"
            "2. Are there architecture rule violations (UI_THEME, CombatEngine purity, crispText, etc.)?\n"
            "3. Are there logic bugs in the changed code?\n"
            "Return verdict=approved only if the task is solved AND no critical violations exist.",
        ]

        if not state.context_cache_name and state.game_context:
            parts.insert(2, f"## Game source context\n{state.game_context}\n")

        return "\n".join(parts)
