"""Reviewer agent — reviews code changes for correctness, style, and security."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.state import AgentState, Phase, Subtask
from src.tools import filesystem


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    system_prompt = (
        "You are a senior React Native / Expo code reviewer.\n"
        "Review code for correctness, security, performance, and Expo best practices.\n\n"
        'Respond in JSON: {"approved": true/false, "feedback": "specific issues if not approved", "summary": "one-line verdict"}\n\n'
        "Rules:\n"
        "- Check for: TypeScript errors, missing imports, broken Expo Router usage, security issues\n"
        "- Be constructive — approve if code is production-ready\n"
        "- Don't be pedantic about minor style preferences\n"
        "- Focus on bugs, crashes, and security vulnerabilities above all"
    )

    def run(self, state: AgentState, subtask: Subtask = None, **kwargs) -> AgentState:
        state.current_phase = Phase.REVIEWING
        state.log(f"Reviewing subtask {subtask.id}...", agent=self.name)

        # Read the files that were written for this subtask
        file_contents = ""
        if subtask.files_to_touch and state.project_dir:
            file_contents = filesystem.read_multiple_files(state.project_dir, subtask.files_to_touch)

        user_prompt = (
            f"## Subtask\n{subtask.description}\n\n"
            f"## Code written\n{file_contents or subtask.code_diff}\n\n"
            "Review this code and return your verdict."
        )

        result = self._call_json(user_prompt)

        approved = result.get("approved", False)
        feedback = result.get("feedback", "")
        summary = result.get("summary", "")

        if approved:
            subtask.status = "done"
            subtask.review_feedback = f"APPROVED: {summary}"
            state.log(f"Approved: {summary}", agent=self.name)
        else:
            subtask.status = "pending"
            subtask.review_feedback = feedback
            state.revision_count += 1
            state.log(f"Revision needed: {feedback[:100]}", agent=self.name)

        return state

