"""Tester agent — starts Expo web and captures screenshots via Playwright."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import BaseAgent
from src.state import AgentState, Phase
from src.tools.browser import take_expo_screenshots


class TesterAgent(BaseAgent):
    name = "tester"
    system_prompt = ""  # not an LLM agent — uses browser automation

    def run(self, state: AgentState, **kwargs) -> AgentState:
        state.current_phase = Phase.TESTING
        state.log("Starting Expo web server for screenshot testing...", agent=self.name)

        if not state.project_dir:
            state.log("No project_dir set — skipping browser test.", agent=self.name)
            return state

        screenshots_dir = str(Path(state.project_dir) / ".agent" / "screenshots")
        shots = take_expo_screenshots(
            project_dir=state.project_dir,
            screenshots_dir=screenshots_dir,
        )

        if shots:
            state.screenshots.extend(shots)
            state.log(f"Screenshots saved: {shots}", agent=self.name)
        else:
            state.log(
                "Could not capture screenshots (Expo web may not be configured). Skipping.",
                agent=self.name,
            )

        return state

    # Override since this agent doesn't use LLM
    def _call(self, user: str, temperature: float = 0.3) -> str:
        return ""

    def _call_json(self, user: str) -> dict:
        return {}
