"""QA agent — static code verifier for Mộng Võ Lâm.

Performs static analysis of Dev-written code against:
  - Game-specific rules (combat formula, status effects, passives, SaveManager contract)
  - Architecture invariants from CLAUDE.md
  - TechExpert test scenarios
  - Common Phaser 4 patterns (tween lifecycle, container usage, crispText)

Does NOT run the browser or execute JavaScript — analysis is purely textual.
Uses thinking_budget=4096 so reasoning traces catch subtle logic bugs.

Output format:
  {"passed": bool, "issues": [{"file":"...","severity":"critical|warning|suggestion","description":"..."}],
   "summary": "one-line verdict"}
"""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools.filesystem import read_multiple_files


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class _QAIssue(BaseModel):
    file: str
    severity: str          # 'critical' | 'warning' | 'suggestion'
    description: str


class _QAResponse(BaseModel):
    passed: bool
    issues: list[_QAIssue]
    summary: str


# ── Agent ─────────────────────────────────────────────────────────────────────

# Inline game rules — embedded in system prompt so QA always has them,
# regardless of whether the context cache contains them.
_GAME_RULES = """
## Combat rules to verify
- Damage formula: rawDmg = ATK * skill.multiplier; final = rawDmg * (DEF_K / (DEF_K + DEF)); crit * 1.5×
- Element advantage: +25% dmg; disadvantage: −15% dmg (Kim>Mộc>Thổ>Thủy>Hỏa>Kim cycle)
- Fury: all heroes start at 25; attacker gains 3–20 per hit (scaled to % HP damage); ultimate costs 100
- Status effects stored as: { type: 'stun'|'poison'|..., remaining: N } — ticked at turn START
- Passive triggers: onTurnStart, onHit, onAllyDeath, onKill — via PassiveRegistry.trigger()
- statMods are multiplicative: atk * (1 + statMods.atk). Never mutate hero.atk directly.
- SaveManager.load() → modify → SaveManager.save(data). No direct localStorage access.
- Targeting: melee=front row same col, ranged=random enemy, assassin=back row same col
- Taunt forces single-target to taunter (assassin and ranged bypass)
- actionResult must always have: attacker, target, damage, isDead, winner, allTargetResults

## Architecture rules to verify
- CombatEngine.js: ZERO Phaser imports — if you see 'import.*Phaser' or 'Phaser\\.', flag CRITICAL
- UI_THEME must be used for all panel/button colors — no bare hex like 0x0000ff (blue/navy/teal)
- crispText(scene, x, y, text, style) must be used — not scene.add.text()
- gotoScene(this, 'Key', data) must be used — not this.scene.start()
- All new scenes must be registered in src/config.js scene array
- Status effects must go through StatusProcessor.applySkillEffect() — not direct array.push()
- new passives must be added to PassiveRegistry.HANDLERS — not inlined in CombatEngine

## Vietnamese UI text rules
- Player-facing strings MUST have full Vietnamese diacritics
- Examples of WRONG: 'Chon', 'Trang bi', 'Doi hinh', 'Khong'
- Examples of CORRECT: 'Chọn', 'Trang bị', 'Đội hình', 'Không'

## Phaser 4 patterns
- Tweens: always kill before recreating — scene.tweens.killTweensOf(obj) first
- Containers: destroy cleanly in scene shutdown — override scene.shutdown or destroy event
- Particles: pre-allocate on scene create, reuse via emitParticleAt()
- Graphics: fill then stroke (fillStyle before lineStyle in same block)
"""


class QAAgent(BaseAgent):
    name = "qa"
    system_prompt = (
        "You are a Senior QA Engineer for Mộng Võ Lâm, a Phaser 4 H5 wuxia card battle RPG.\n"
        "You perform STATIC code analysis — you do not execute code.\n\n"
        "Your job: review code written by the Dev agent and determine if it:\n"
        "1. Correctly implements the subtask description\n"
        "2. Follows all game rules (combat formula, status effects, turn logic)\n"
        "3. Follows all architecture conventions (CLAUDE.md)\n"
        "4. Passes the test scenarios provided by the Tech Expert\n\n"
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
        '"summary": "one-line verdict"}\n\n'
        "Pass = zero critical + zero warning issues. Suggestions do NOT block passing."
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

        prompt = self._build_prompt(state, subtask)
        result = self._call_json(
            prompt,
            response_schema=_QAResponse,
            thinking_budget=4096,
        )

        subtask.qa_passed  = result.get("passed", False)
        subtask.qa_issues  = [dict(i) for i in result.get("issues", [])]
        subtask.qa_summary = result.get("summary", "")

        critical = [i for i in subtask.qa_issues if i.get("severity") == "critical"]
        warnings = [i for i in subtask.qa_issues if i.get("severity") == "warning"]

        state.log(
            f"[Subtask {subtask.id}] QA {'PASSED' if subtask.qa_passed else 'FAILED'} — "
            f"{len(critical)} critical, {len(warnings)} warning — {subtask.qa_summary[:80]}",
            agent=self.name,
        )
        return state

    # Required abstract method
    def _call(self, user: str, temperature: float = 0.3, thinking_budget: int = 0) -> str:  # type: ignore[override]
        from src.llm import call as llm_call
        return llm_call(self.system_prompt, user, temperature=temperature, thinking_budget=thinking_budget)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_prompt(self, state: GameAgentState, subtask: GameSubtask) -> str:
        # Read the files written by Dev (from disk — authoritative version)
        file_contents = ""
        if subtask.files_to_touch and state.game_project_dir:
            file_contents = read_multiple_files(
                state.game_project_dir,
                subtask.files_to_touch,
                max_total=80_000,
            )
        elif subtask.written_files:
            # Fallback: use in-memory content if files weren't written to disk
            file_contents = "\n\n".join(
                f"=== {path} ===\n{content}"
                for path, content in subtask.written_files.items()
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

        parts = [
            f"## Subtask description\n{subtask.description}\n\n",
            scenarios_block,
            constraints_block,
            prev_issues_block,
            f"## Code written by Dev\n{file_contents or '[No files written]'}\n\n",
            "Analyze the code above and return your QA verdict.",
        ]
        return "".join(parts)
