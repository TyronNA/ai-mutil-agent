"""Notifier agent — sends macOS notification + webhook when PR is ready."""

from __future__ import annotations

from src.agents.base import BaseAgent
from src.state import AgentState, Phase
from src.tools.notify import notify_all


class NotifierAgent(BaseAgent):
    name = "notifier"
    system_prompt = ""

    def run(self, state: AgentState, **kwargs) -> AgentState:
        state.current_phase = Phase.NOTIFYING

        if state.pr_url:
            title = "PR Ready for Review"
            message = f"Task: {state.task[:60]}"
            notify_all(
                title=title,
                message=message,
                pr_url=state.pr_url,
                screenshots=state.screenshots,
            )
            state.log(f"Notified: PR {state.pr_url}", agent=self.name)
        else:
            title = "Agent Task Complete"
            message = f"{state.task[:60]} — {len(state.files_written)} file(s) written"
            notify_all(title=title, message=message)
            state.log("Notified: task complete (no PR)", agent=self.name)

        return state

    def _call(self, user: str, temperature: float = 0.3) -> str:
        return ""

    def _call_json(self, user: str) -> dict:
        return {}
