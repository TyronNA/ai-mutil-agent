"""Dev agent — game-aware coder for Mộng Võ Lâm.

Writes or modifies JavaScript/Phaser 4 source files according to:
  - The TechExpert implementation plan
  - CLAUDE.md conventions (injected via context cache or inline)
  - Feedback from the QA agent on revision rounds

Output format:
  {"files": {"relative/path.js": "full file content"}, "summary": "what was done"}

Uses Gemini Context Cache on the first attempt so that file contents +
game context are NOT re-sent on each revision — only the QA delta is sent.
"""

from __future__ import annotations

import re

from src.agents.base import BaseAgent
from src.llm import create_cache
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools.filesystem import read_multiple_files, write_file


class DevAgent(BaseAgent):
    name = "dev"
    system_prompt = (
        "You are an expert JavaScript/Phaser 4 game developer working on Mộng Võ Lâm.\n"
        "Mộng Võ Lâm is an H5 wuxia card battle RPG: Phaser 4 + Vite, mobile-first 390×844, no backend.\n\n"
        "## Mandatory conventions (enforce on every file you touch)\n"
        "1. CombatEngine.js and all classes in src/classes/Combat* — ZERO Phaser imports. Pure JavaScript only.\n"
        "2. UI_THEME from src/constants.js for ALL panel/button/text colors. No ad-hoc hex. No blue/navy/teal.\n"
        "3. crispText(scene, x, y, text, style) instead of scene.add.text() for ALL scene text.\n"
        "4. gotoScene(this, 'SceneKey', data) for ALL scene transitions. Never this.scene.start().\n"
        "5. SaveManager.load() → modify object → SaveManager.save(data). Never read localStorage directly.\n"
        "6. statMods for temporary buffs: hero.statMods.atk += value. Never mutate hero.atk directly.\n"
        "7. Vietnamese UI strings with full diacritics: 'Chọn đội hình', NOT 'Chon doi hinh'.\n"
        "8. actionResult contract: keep attacker/target/damage/isDead/winner/allTargetResults structure.\n"
        "9. Grid slots 0–8: col = slotIndex % 3, row = Math.floor(slotIndex / 3). Never hardcode coords.\n"
        "10. Status effects as objects: { type: 'stun', remaining: 2 }. Ticked in StatusProcessor.js.\n\n"
        "## Output format\n"
        'Respond ONLY in JSON: {"files": {"relative/path.js": "full file content"}, "summary": "..."}\n'
        "- Always return the COMPLETE file content (not a diff)\n"
        "- Do NOT wrap code in markdown fences inside JSON\n"
        "- Relative paths must match the actual project structure (e.g. src/classes/Foo.js)\n"
        "- Write production-quality, commented code\n"
    )

    def run(
        self,
        state: GameAgentState,
        subtask: GameSubtask,
        **kwargs,
    ) -> GameAgentState:
        state.current_phase = GamePhase.CODING
        subtask.status = "in_progress"
        state.log(
            f"[Subtask {subtask.id}] Coding — attempt {subtask.revision_count + 1}: {subtask.description[:70]}",
            agent=self.name,
        )

        # ── Build / reuse context cache ──────────────────────────────────────
        # First attempt: cache (system prompt + game context + current file contents).
        # Revision attempts: only QA feedback is sent as new content.
        if not subtask.code_cache_name:
            subtask.code_cache_name = self._create_subtask_cache(state, subtask)

        # ── Build per-call prompt ────────────────────────────────────────────
        call_content = self._build_call_content(state, subtask)

        # ── Call LLM ─────────────────────────────────────────────────────────
        if subtask.code_cache_name:
            result = self._call_json(
                call_content,
                cached_content=subtask.code_cache_name,
                max_output_tokens=32_768,
            )
        else:
            # Cache miss — send full context inline
            full_prompt = self._build_full_prompt(state, subtask) + "\n\n" + call_content
            result = self._call_json(full_prompt, max_output_tokens=32_768)

        # ── Write files to disk ───────────────────────────────────────────────
        files: dict[str, str] = result.get("files", {})
        subtask.code_summary = result.get("summary", "")

        for rel_path, content in files.items():
            # Strip accidental markdown fences from within JSON strings
            content = re.sub(r"^```[\w]*\n?", "", content.strip())
            content = re.sub(r"\n?```$", "", content.strip())

            if state.game_project_dir:
                write_file(state.game_project_dir, rel_path, content)

            subtask.written_files[rel_path] = content
            if rel_path not in state.files_written:
                state.files_written.append(rel_path)

        state.log(
            f"[Subtask {subtask.id}] Wrote {len(files)} file(s): {', '.join(files.keys())}",
            agent=self.name,
        )
        return state

    # ── Private helpers ───────────────────────────────────────────────────────

    def _create_subtask_cache(self, state: GameAgentState, subtask: GameSubtask) -> str:
        """Try to cache: system prompt + game context + current file contents + plan."""
        existing_files = ""
        if subtask.files_to_touch and state.game_project_dir:
            existing_files = read_multiple_files(
                state.game_project_dir,
                subtask.files_to_touch,
                max_total=60_000,
            )

        constraints_block = ""
        if state.global_constraints:
            constraints_block = (
                "## Additional constraints from Tech Expert\n"
                + "\n".join(f"- {c}" for c in state.global_constraints)
                + "\n"
            )

        static_ctx = (
            f"## Overall task\n{state.task}\n\n"
            f"## Implementation plan\n{state.implementation_plan}\n\n"
            f"## This subtask (id={subtask.id})\n{subtask.description}\n\n"
            f"## Files to create/modify\n{', '.join(subtask.files_to_touch) or 'Decide based on task'}\n\n"
            f"{constraints_block}"
        )

        # Embed game context if not already in a shared cache
        if state.context_cache_name:
            # Game context already warm in Gemini — don't embed again
            pass
        elif state.game_context:
            static_ctx += f"## Game source context\n{state.game_context}\n\n"

        if existing_files:
            static_ctx += f"## Current file contents\n{existing_files}\n"

        # create_cache returns None if content is below Gemini's min token threshold
        cache_name = create_cache(self.system_prompt, static_ctx, ttl_seconds=1200) or ""
        return cache_name

    def _build_call_content(self, state: GameAgentState, subtask: GameSubtask) -> str:
        """The per-revision delta sent on each call (cache hit path)."""
        if subtask.revision_count == 0:
            return f"Attempt 1. Implement the subtask now. Follow all mandatory conventions."

        issues_md = ""
        if subtask.qa_issues:
            lines = [
                f"  - [{i['severity'].upper()}] {i.get('file','')} — {i['description']}"
                for i in subtask.qa_issues
            ]
            issues_md = "\n".join(lines)

        return (
            f"Attempt {subtask.revision_count + 1}.\n\n"
            f"## ⚠️ QA REJECTED — YOU MUST FIX ALL ISSUES BELOW\n"
            f"{issues_md or subtask.qa_summary}\n\n"
            "Fix every issue listed above before returning your response. "
            "Return the COMPLETE updated file content."
        )

    def _build_full_prompt(self, state: GameAgentState, subtask: GameSubtask) -> str:
        """Full prompt used when context cache is unavailable."""
        existing_files = ""
        if subtask.files_to_touch and state.game_project_dir:
            existing_files = read_multiple_files(
                state.game_project_dir,
                subtask.files_to_touch,
                max_total=60_000,
            )

        parts = [
            f"## Overall task\n{state.task}\n",
            f"## Implementation plan\n{state.implementation_plan}\n",
            f"## This subtask (id={subtask.id})\n{subtask.description}\n",
            f"## Files to create/modify\n{', '.join(subtask.files_to_touch) or 'Decide based on task'}\n",
        ]
        if state.global_constraints:
            parts.append(
                "## Constraints from Tech Expert\n"
                + "\n".join(f"- {c}" for c in state.global_constraints)
            )
        if state.game_context:
            parts.append(f"## Game source context\n{state.game_context}\n")
        if existing_files:
            parts.append(f"## Current file contents\n{existing_files}\n")

        return "\n".join(parts)
