"""Coder agent — reads relevant files and writes changes to disk."""

from __future__ import annotations

import re

from src.agents.base import BaseAgent
from src.llm import create_cache
from src.state import AgentState, Phase, Subtask
from src.tools import filesystem


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = (
        "You are an expert full-stack developer.\n"
        "Given a subtask, existing code, and project conventions, write or modify files to implement it.\n\n"
        'Respond in JSON: {"files": {"relative/path.tsx": "full file content...", ...}, "summary": "what was done"}\n\n'
        "Rules:\n"
        "- YOU MUST follow the Codebase conventions section exactly -- use the same patterns, libraries, import aliases, and style\n"
        "- Do NOT invent new libraries or patterns -- only use what is already in the project\n"
        "- Write production-quality code with proper TypeScript types\n"
        "- If modifying an existing file, return the COMPLETE updated file content\n"
        "- Keep files focused and small\n"
        "- Do NOT wrap code in markdown code fences inside JSON values\n"
        "- Use relative paths matching the project structure (e.g., app/index.tsx, components/Header.tsx)"
    )

    def run(self, state: AgentState, subtask: Subtask = None, **kwargs) -> AgentState:
        state.current_phase = Phase.CODING
        subtask.status = "in_progress"
        state.log(f"Coding subtask {subtask.id}: {subtask.description[:60]}", agent=self.name)

        # ── Build or reuse context cache ─────────────────────────────────────
        # On the first attempt for this subtask we read the files and try to cache
        # the static context (task + subtask + file contents) with the system prompt.
        # Subsequent revision attempts reuse the same cache — only the reviewer
        # feedback is sent as new content, saving significant tokens on large files.
        if not subtask.code_cache_name:
            existing = ""
            if subtask.files_to_touch and state.project_dir:
                existing = filesystem.read_multiple_files(state.project_dir, subtask.files_to_touch)

            static_context = (
                f"## Overall task\n{state.task}\n\n"
                f"## Subtask\n{subtask.description}\n\n"
                f"## Files to create/modify\n{', '.join(subtask.files_to_touch) or 'Decide based on task'}\n\n"
            )
            if state.codebase_context:
                static_context += f"## Codebase conventions (YOU MUST FOLLOW THESE)\n{state.codebase_context}\n\n"
            if existing:
                static_context += f"## Current file contents\n{existing}\n\n"

            # create_cache returns None silently if content is below the model's
            # minimum token threshold (~32K for Flash) — caller falls back to full prompt.
            subtask.code_cache_name = create_cache(self.system_prompt, static_context) or ""

        # ── Build the per-call (unique) prompt part ──────────────────────────
        if subtask.code_cache_name:
            # Static context is already in the cache — send only the attempt-specific delta.
            if subtask.review_feedback:
                call_content = (
                    f"Attempt {subtask.revision_count + 1}.\n\n"
                    f"## ⚠️ REVIEWER REJECTION — YOU MUST FIX THIS\n"
                    f"{subtask.review_feedback}\n\n"
                    "Address ALL reviewer concerns before returning your response."
                )
            else:
                call_content = f"Attempt {subtask.revision_count + 1}. Implement the subtask now."
            result = self._call_json(call_content, cached_content=subtask.code_cache_name, max_output_tokens=32768)
        else:
            # No cache (content below threshold) — fall back to full prompt per call.
            existing = ""
            if subtask.files_to_touch and state.project_dir:
                existing = filesystem.read_multiple_files(state.project_dir, subtask.files_to_touch)

            user_prompt = (
                f"## Overall task\n{state.task}\n\n"
                f"## Current subtask (attempt {subtask.revision_count + 1})\n{subtask.description}\n\n"
                f"## Files to create/modify\n{', '.join(subtask.files_to_touch) or 'Decide based on task'}\n\n"
            )
            if state.codebase_context:
                user_prompt += f"## Codebase conventions (YOU MUST FOLLOW THESE)\n{state.codebase_context}\n\n"
            if existing:
                user_prompt += f"## Current file contents\n{existing}\n\n"
            if subtask.review_feedback:
                user_prompt += (
                    f"## ⚠️ REVIEWER REJECTION — YOU MUST FIX THIS\n"
                    f"{subtask.review_feedback}\n\n"
                    "Address ALL reviewer concerns before returning your response.\n\n"
                )
            result = self._call_json(user_prompt, max_output_tokens=32768)

        files: dict = result.get("files", {})
        subtask.code_diff = result.get("summary", "")

        # Write files to disk
        for rel_path, content in files.items():
            # Strip any accidental markdown fences from file content
            content = re.sub(r"^```[\w]*\n?", "", content.strip())
            content = re.sub(r"\n?```$", "", content.strip())
            if state.project_dir:
                filesystem.write_file(state.project_dir, rel_path, content)
            if rel_path not in state.files_written:
                state.files_written.append(rel_path)

        state.log(f"Wrote {len(files)} file(s): {', '.join(files.keys())}", agent=self.name)
        return state

