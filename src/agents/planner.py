"""Planner agent — reads existing codebase and decomposes task into subtasks."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.state import AgentState, Phase, Subtask
from src.tools import filesystem

# Key config files to read so the planner understands project dependencies + structure
_CONFIG_FILES = ["package.json", "app.json", "tsconfig.json", "app.config.ts", "app.config.js"]


class PlannerAgent(BaseAgent):
    name = "planner"
    system_prompt = (
        "You are a senior Expo React Native architect and planner.\n"
        "Given an existing Expo project and a task, break it into clear, ordered subtasks.\n\n"
        'Respond in JSON: {"plan_summary": "...", "subtasks": [{"id": 1, "description": "...", "files_to_touch": ["app/index.tsx", ...]}, ...]}\n\n'
        "Rules:\n"
        "- Analyze the existing file structure AND project config carefully\n"
        "- List only files that need to be created or modified for each subtask\n"
        "- Keep subtasks small, ordered by dependency (no subtask depends on a later one)\n"
        "- Maximum 5 subtasks\n"
        "- Use Expo Router v3, TypeScript, NativeWind or StyleSheet\n"
        "- Match the coding style and libraries already used in the project"
    )

    def run(self, state: AgentState, **kwargs) -> AgentState:
        state.current_phase = Phase.PLANNING
        state.log("Analyzing codebase and planning subtasks...", agent=self.name)

        project_files = state.project_file_list()

        # Read key config files so planner understands project deps + structure
        config_context = ""
        if state.project_dir:
            config_context = filesystem.read_multiple_files(
                state.project_dir,
                [f for f in _CONFIG_FILES],
                max_total=20_000,
            )

        user_prompt = (
            f"## Task\n{state.task}\n\n"
            f"## Existing project files\n{project_files}\n\n"
        )
        if config_context.strip():
            user_prompt += f"## Project config / dependencies\n{config_context}\n\n"
        user_prompt += "Break this task into subtasks."

        result = self._call_json(user_prompt)

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

