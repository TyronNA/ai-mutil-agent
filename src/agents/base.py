"""Base agent class — all agents inherit from this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.llm import call, call_json
from src.state import AgentState


class BaseAgent(ABC):
    """Base class for all agents in the system."""

    name: str = "base"
    system_prompt: str = "You are a helpful assistant."

    def __init__(self) -> None:
        pass

    @abstractmethod
    def run(self, state: AgentState, **kwargs) -> AgentState:
        """Execute this agent's logic and return updated state."""
        ...

    def _call(self, user: str, temperature: float = 0.3) -> str:
        return call(self.system_prompt, user, temperature=temperature)

    def _call_json(self, user: str) -> dict:
        return call_json(self.system_prompt, user)

