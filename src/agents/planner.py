"""Planner agent — reads existing codebase and decomposes task into subtasks."""

from __future__ import annotations

from pydantic import BaseModel

from src.agents.base import BaseAgent
from src.state import AgentState, Phase, Subtask


class _SubtaskItem(BaseModel):
    id: int
    description: str
    files_to_touch: list[str] = []


class _PlanResponse(BaseModel):
    plan_summary: str
    subtasks: list[_SubtaskItem]


class PlannerAgent(BaseAgent):
    name = "planner"
    system_prompt = (
        "You are a senior software architect and technical planner.\n"
        "Given an existing project and a task, break it into clear, ordered subtasks.\n\n"
        'Respond in JSON: {"plan_summary": "...", "subtasks": [{"id": 1, "description": "...", "files_to_touch": ["path/file.tsx", ...]}, ...]}\n\n'
        "Rules:\n"
        "- ALWAYS follow the provided Codebase conventions -- use the same patterns, libraries, and style already in the project\n"
        "- Do NOT assume any framework or library -- only use what is already in the project\n"
        "- Analyze the existing file structure carefully to identify which files need changes\n"
        "- List only files that need to be created or modified for each subtask\n"
        "- Keep subtasks small and ordered by dependency (no subtask may depend on a later one)\n"
        "- Maximum 5 subtasks"
    )

    def run(self, state: AgentState, **kwargs) -> AgentState:
        state.current_phase = Phase.PLANNING
        state.log("Planning subtasks...", agent=self.name)

        project_files = state.project_file_list()

        user_prompt = (
            f"## Task\n{state.task}\n\n"
            f"## Existing project files\n{project_files}\n\n"
        )
        if state.codebase_context:
            user_prompt += f"## Codebase conventions and patterns (FOLLOW THESE)\n{state.codebase_context}\n\n"
        if state.existing_errors:
            user_prompt += f"## Pre-existing errors (do NOT introduce more)\n{state.existing_errors}\n\n"
        user_prompt += "Break this task into subtasks that strictly follow the existing code conventions above."

        result = self._call_json(user_prompt, response_schema=_PlanResponse, thinking_budget=8192)

        state.plan_summary = result.get("plan_summary", "")
        state.subtasks = [
            Subtask(
                id=s["id"],
                description=s["description"],
                files_to_touch=s.get("files_to_touch", []),
            )
            for s in result.get("subtasks", [])
        ]
        state.log(f"Plan ready: {len(state.subtasks)} subtasks — {state.plan_summary[:80]}", agent=self.name)
        return state

