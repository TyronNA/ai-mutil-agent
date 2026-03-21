"""Coder agent — reads relevant files and writes changes to disk."""

from __future__ import annotations

import re

from src.agents.base import BaseAgent
from src.state import AgentState, Phase, Subtask
from src.tools import filesystem


class CoderAgent(BaseAgent):
    name = "coder"
    system_prompt = (
        "You are an expert Expo React Native developer.\n"
        "Given a subtask and existing code context, write or modify files to implement it.\n\n"
        'Respond in JSON: {"files": {"relative/path.tsx": "full file content...", ...}, "summary": "what was done"}\n\n'
        "Rules:\n"
        "- Write production-quality TypeScript with proper types\n"
        "- If modifying an existing file, return the COMPLETE updated file content\n"
        "- Use Expo Router v3, React Native StyleSheet or NativeWind\n"
        "- Keep files focused and small\n"
        "- Do NOT wrap code in markdown code fences inside JSON values\n"
        "- Use relative paths (e.g., app/index.tsx, components/Header.tsx)"
    )

    def run(self, state: AgentState, subtask: Subtask = None, **kwargs) -> AgentState:
        state.current_phase = Phase.CODING
        subtask.status = "in_progress"
        state.log(f"Coding subtask {subtask.id}: {subtask.description[:60]}", agent=self.name)

        # Read existing content of files this subtask will touch
        existing = ""
        if subtask.files_to_touch and state.project_dir:
            existing = filesystem.read_multiple_files(state.project_dir, subtask.files_to_touch)

        user_prompt = (
            f"## Overall task\n{state.task}\n\n"
            f"## Current subtask (attempt {state.revision_count + 1})\n{subtask.description}\n\n"
            f"## Files to create/modify\n{', '.join(subtask.files_to_touch) or 'Decide based on task'}\n\n"
        )
        if existing:
            user_prompt += f"## Current file contents\n{existing}\n\n"
        if subtask.review_feedback:
            # Put reviewer feedback at the END so it's the last thing the model sees (recency bias)
            user_prompt += (
                f"## ⚠️ REVIEWER REJECTION — YOU MUST FIX THIS\n"
                f"{subtask.review_feedback}\n\n"
                "Address ALL reviewer concerns before returning your response.\n\n"
            )

        result = self._call_json(user_prompt)

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

