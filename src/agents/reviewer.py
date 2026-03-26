"""Reviewer agent — reviews code changes for correctness, style, and security."""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.state import AgentState, Phase, Subtask
from src.tools import filesystem


class _ReviewResponse(BaseModel):
    approved: bool
    feedback: str
    summary: str


class ReviewerAgent(BaseAgent):
    name = "reviewer"
    system_prompt = (
        "You are a senior code reviewer.\n"
        "Review code for correctness, security, and strict adherence to the project's own conventions.\n\n"
        'Respond in JSON: {"approved": true/false, "feedback": "specific issues if not approved", "summary": "one-line verdict"}\n\n'
        "Rules:\n"
        "- Check AGAINST the provided Codebase conventions -- flag any deviation from established patterns\n"
        "- Check for: TypeScript errors, wrong import paths, missing imports, security issues\n"
        "- Verify the code does NOT introduce new errors beyond any pre-existing ones listed\n"
        "- Approve if the code is correct, secure, and consistent with the project conventions\n"
        "- Focus on bugs, crashes, security, and convention violations -- not minor style nits"
    )

    def run(self, state: AgentState, subtask: Subtask = None, **kwargs) -> AgentState:
        state.current_phase = Phase.REVIEWING
        state.log(f"Reviewing subtask {subtask.id}...", agent=self.name)

        # Read the files that were written for this subtask
        file_contents = ""
        if subtask.files_to_touch and state.project_dir:
            file_contents = filesystem.read_multiple_files(state.project_dir, subtask.files_to_touch)

        user_prompt = ""
        if state.codebase_context:
            user_prompt += f"## Codebase conventions (review AGAINST these)\n{state.codebase_context}\n\n"
        if state.existing_errors:
            user_prompt += f"## Pre-existing errors (already present before changes)\n{state.existing_errors}\n\n"
        user_prompt += (
            f"## Subtask\n{subtask.description}\n\n"
            f"## Code written\n{file_contents or subtask.code_diff}\n\n"
            "Review this code against the conventions above and return your verdict."
        )

        result = self._call_json(user_prompt, response_schema=_ReviewResponse, thinking_budget=4096)

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
            subtask.revision_count += 1  # per-subtask counter — each subtask runs in its own thread
            state.log(f"Revision needed: {feedback[:100]}", agent=self.name)

        return state

