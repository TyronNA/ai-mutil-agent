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

    def _call(self, user: str, temperature: float = 0.3, thinking_budget: int = 0) -> str:
        return call(self.system_prompt, user, temperature=temperature, thinking_budget=thinking_budget)

    def _call_json(
        self,
        user: str,
        response_schema=None,
        cached_content: str = None,
        thinking_budget: int = 0,
        max_output_tokens: int = 16384,
    ) -> dict:
        return call_json(
            self.system_prompt,
            user,
            response_schema=response_schema,
            cached_content=cached_content,
            thinking_budget=thinking_budget,
            max_output_tokens=max_output_tokens,
        )

