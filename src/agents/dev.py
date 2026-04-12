"""Dev agent — game-aware coder for Mộng Võ Lâm.

Writes or modifies JavaScript/Phaser 4 source files according to:
  - The TechExpert implementation plan
  - CLAUDE.md conventions (injected via context cache or inline)
  - Feedback from the QA agent on revision rounds

Output format:
  {"patches": [{"file": "...", "find": "...", "replace": "..."}], "new_files": {...}, "summary": "..."}

Patches are applied server-side so only changed code blocks are sent back.
Uses Gemini Context Cache on the first attempt so original file contents +
game context are NOT re-sent on each revision.
"""

from __future__ import annotations

import logging
import re

from rich.console import Console as _RichConsole

from src.agents.base import BaseAgent

_console = _RichConsole()

log = logging.getLogger(__name__)
from src.llm import create_cache
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools.filesystem import read_file, read_multiple_files, write_file


class DevAgent(BaseAgent):
    name = "dev"
    system_prompt = (
        "You are an expert JavaScript/Phaser 4 game developer working on Mộng Võ Lâm.\n"
        "Mộng Võ Lâm is an H5 wuxia card battle RPG: Phaser 4 + Vite, mobile-first 390×844, no backend.\n\n"
        "## Mandatory conventions (apply ONLY to new code you write — do NOT refactor pre-existing code outside the subtask scope)\n"
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
        "## SCOPE DISCIPLINE\n"
        "ONLY implement what the subtask asks for. Do NOT fix pre-existing convention violations, refactor\n"
        "unrelated code, or rename variables outside the changed block. Minimal diff = best diff.\n\n"
        "## Output format\n"
        'Respond ONLY in JSON:\n'
        '{"patches": [{"file": "src/path.js", "find": "exact code block to replace (≥3 context lines)", "replace": "new code block"}],\n'
        ' "new_files": {"src/brand-new.js": "full content — ONLY for files that do not yet exist"},\n'
        ' "summary": "brief description"}\n'
        "Rules:\n"
        "  - For EXISTING files: use patches[] — output ONLY the changed block, never the whole file\n"
        "  - Each 'find' must be a unique substring; include ≥3 lines of surrounding context\n"
        "  - Multiple patches per file allowed; list them top-to-bottom\n"
        "  - For genuinely NEW files only: use new_files{} with full content\n"
        "  - ONLY import from files that exist in the project file tree provided — never invent paths\n"
        "  - Write production-quality, commented code\n"
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

        # ── Capture original file content (first attempt only) ──────────────
        # Stored so QAAgent can show a diff instead of the full rewritten file.
        if subtask.revision_count == 0 and not subtask.original_files:
            for rel_path in subtask.files_to_touch:
                if state.game_project_dir:
                    raw = read_file(state.game_project_dir, rel_path, max_chars=200_000)
                    if raw:
                        subtask.original_files[rel_path] = raw

        # ── Build / reuse context cache ──────────────────────────────────────
        # First attempt: cache (system prompt + game context + current file contents).
        # Revision attempts: current written content + QA feedback sent inline.
        if not subtask.code_cache_name:
            subtask.code_cache_name = self._create_subtask_cache(state, subtask)

        # ── Build per-call prompt ────────────────────────────────────────────
        call_content = self._build_call_content(state, subtask)

        # ── Smart model routing ───────────────────────────────────────────────
        # Escalate to Pro when: already failed QA once, OR touching many files.
        use_pro = subtask.revision_count >= 2 or len(subtask.files_to_touch) > 3
        use_thinking = 512 if subtask.revision_count >= 2 else 0
        if use_pro:
            log.info(
                "[dev] Escalating to Pro model — revision=%d files=%d",
                subtask.revision_count, len(subtask.files_to_touch),
            )

        # ── Call LLM ─────────────────────────────────────────────────────────
        if subtask.code_cache_name:
            result = self._call_json(
                call_content,
                cached_content=subtask.code_cache_name,
                max_output_tokens=32_768,
                pro=use_pro,
                thinking_budget=use_thinking,
            )
        else:
            # Cache miss — send full context inline
            full_prompt = self._build_full_prompt(state, subtask) + "\n\n" + call_content
            result = self._call_json(
                full_prompt,
                max_output_tokens=32_768,
                pro=use_pro,
                thinking_budget=use_thinking,
            )

        # ── Apply patches and write files to disk ────────────────────────────
        subtask.code_summary = result.get("summary", "")
        patches_list: list[dict] = result.get("patches", [])
        new_files: dict[str, str] = result.get("new_files", {})

        # Backward compat: model used old 'files' format
        if not patches_list and not new_files and result.get("files"):
            log.warning("[dev] Model used full-file 'files' format — accepting as new_files fallback")
            new_files = result.get("files", {})

        # Group patches by target file
        patches_by_file: dict[str, list[dict]] = {}
        for p in patches_list:
            fp = p.get("file", "")
            if fp:
                patches_by_file.setdefault(fp, []).append(p)

        # Build final content map: apply patches to existing files
        files_to_write: dict[str, str] = {}
        for rel_path, file_patches in patches_by_file.items():
            # Base: prefer already-written content (revision round), else original from disk
            base = (
                subtask.written_files.get(rel_path)
                or subtask.original_files.get(rel_path)
                or (read_file(state.game_project_dir, rel_path, max_chars=200_000) if state.game_project_dir else "")
                or ""
            )
            patched, warnings, failed_patches = self._apply_patches(file_patches, base, rel_path)
            for w in warnings:
                log.warning("[dev] %s", w)
            # ── Patch failure fallback ────────────────────────────────────────
            # If any patch failed to match, the model's context was stale.
            # Record the failed patch context for the next revision prompt so
            # the model knows what went wrong; keep whatever was correctly patched.
            if failed_patches:
                subtask.patch_failures[rel_path] = failed_patches
                state.log(
                    f"[Subtask {subtask.id}] {len(failed_patches)} patch(es) failed to match in {rel_path} "
                    f"— will request correction on next revision.",
                    agent=self.name,
                )
                _console.print(
                    f"    [red]⚠ {len(failed_patches)} patch(es) FAILED to match in {rel_path} "
                    f"— model will retry with corrected find strings[/red]"
                )
            files_to_write[rel_path] = patched

        # Add brand-new files (full content from model)
        for rel_path, content in new_files.items():
            content = re.sub(r"^```[\w]*\n?", "", content.strip())
            content = re.sub(r"\n?```$", "", content.strip())
            files_to_write[rel_path] = content

        for rel_path, content in files_to_write.items():
            if state.game_project_dir:
                write_file(state.game_project_dir, rel_path, content)

            subtask.written_files[rel_path] = content
            if rel_path not in state.files_written:
                state.files_written.append(rel_path)

        state.log(
            f"[Subtask {subtask.id}] Wrote {len(files_to_write)} file(s): {', '.join(files_to_write.keys())}",
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
            f"## Project file tree (ONLY import from files listed here)\n{state.game_file_list()}\n\n"
        )

        # Embed conventions context if not already in a shared cache.
        # Only use game_context (static tier: CLAUDE.md, constants, config) — NOT
        # game_dynamic_context (classes/scenes that Dev will modify and re-read fresh).
        if state.context_cache_name:
            # Static conventions already warm in Gemini cache — don't duplicate
            pass
        elif state.game_context:
            static_ctx += f"## Project conventions & config\n{state.game_context}\n\n"

        if existing_files:
            static_ctx += f"## Current file contents\n{existing_files}\n"

        # create_cache returns None if content is below Gemini's min token threshold
        cache_name = create_cache(self.system_prompt, static_ctx, ttl_seconds=1200) or ""
        return cache_name

    def _build_call_content(self, state: GameAgentState, subtask: GameSubtask) -> str:
        """The per-revision delta sent on each call (cache hit path)."""
        if subtask.revision_count == 0:
            return "Attempt 1. Implement the subtask. Use patches[] for existing files, new_files{} for brand-new ones."

        # On revision: include current written content so Dev can produce correct patches
        current_state_block = ""
        if subtask.written_files:
            current_state_block = (
                "## Current file state (produce patches against THIS content)\n"
                + "\n\n".join(
                    f"=== {path} ===\n{content}"
                    for path, content in subtask.written_files.items()
                )
                + "\n\n"
            )

        issues_md = ""
        if subtask.qa_issues:
            lines = [
                f"  - [{i['severity'].upper()}] {i.get('file','')} — {i['description']}"
                for i in subtask.qa_issues
            ]
            issues_md = "\n".join(lines)

        # Patch failure context — tell the model exactly what didn't match
        patch_fail_block = ""
        if subtask.patch_failures:
            fail_lines = []
            for fp, failed in subtask.patch_failures.items():
                for pf in failed:
                    fail_lines.append(
                        f"  File: {fp}\n"
                        f"  Expected to find (but NOT found):\n"
                        f"  ```\n  {pf.get('find','')[:200]}\n  ```\n"
                        f"  Intended replacement:\n"
                        f"  ```\n  {pf.get('replace','')[:200]}\n  ```"
                    )
            patch_fail_block = (
                "## ⚠️ PREVIOUS PATCHES FAILED TO MATCH\n"
                "The following patches were NOT applied because the 'find' string wasn't found.\n"
                "Re-issue them with the EXACT text as it appears in the current file state above:\n\n"
                + "\n\n".join(fail_lines)
                + "\n\n"
            )
            # Reset failures — will be re-populated if they fail again
            subtask.patch_failures = {}

        return (
            f"Attempt {subtask.revision_count + 1}.\n\n"
            f"{current_state_block}"
            f"{patch_fail_block}"
            f"## ⚠️ QA REJECTED — YOU MUST FIX ALL ISSUES BELOW\n"
            f"{issues_md or subtask.qa_summary}\n\n"
            "Output patches[] to fix every issue above. Do NOT rewrite the whole file."
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
        # Include static conventions (CLAUDE.md, constants) for coding rules
        if state.game_context:
            parts.append(f"## Project conventions & config\n{state.game_context}\n")
        if existing_files:
            parts.append(f"## Current file contents\n{existing_files}\n")
        # Cross-run lessons — inject known problem patterns from previous runs
        if state.lessons_context:
            parts.append(
                f"## Lessons from previous runs (avoid repeating these mistakes)\n"
                f"{state.lessons_context}\n"
            )

        return "\n".join(parts)

    @staticmethod
    def _apply_patches(
        patches: list[dict], base: str, file_path: str
    ) -> tuple[str, list[str], list[dict]]:
        """Apply find/replace patches sequentially.

        Returns:
            (patched_content, warning_strings, failed_patches)
            failed_patches: list of patch dicts whose 'find' was not found in base.
        """
        result = base
        warnings: list[str] = []
        failed_patches: list[dict] = []
        for p in patches:
            find = p.get("find", "")
            replace = p.get("replace", "")
            if not find:
                continue
            if find in result:
                result = result.replace(find, replace, 1)
            else:
                warnings.append(
                    f"Patch 'find' not matched in {file_path}: {find[:80]!r}..."
                )
                failed_patches.append(p)
        return result, warnings, failed_patches
