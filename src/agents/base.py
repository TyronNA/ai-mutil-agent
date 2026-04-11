"""Base agent class — all agents inherit from this."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from src.llm import call, call_json
from src.state import AgentState

log = logging.getLogger(__name__)


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

    def _call(self, user: str, temperature: float = 0.3, thinking_budget: int = 0, pro: bool = False) -> str:
        from src.llm import set_agent_name
        set_agent_name(self.name)
        log.info(
            "[%s] → text call | user=%d chars | temp=%.1f | thinking_budget=%d | pro=%s",
            self.name, len(user), temperature, thinking_budget, pro,
        )
        result = call(self.system_prompt, user, temperature=temperature, thinking_budget=thinking_budget, pro=pro)
        log.info(
            "[%s] ← text response | %d chars | preview: %s",
            self.name, len(result), result[:120].replace("\n", " "),
        )
        return result

    def _call_json(
        self,
        user: str,
        response_schema=None,
        cached_content: str = None,
        thinking_budget: int = 0,
        max_output_tokens: int = 16384,
        pro: bool = False,
    ) -> dict:
        from src.llm import set_agent_name
        set_agent_name(self.name)
        schema_name = response_schema.__name__ if response_schema else "none"
        log.info(
            "[%s] → json call | user=%d chars | schema=%s | cached=%s | thinking_budget=%d | max_tokens=%d | pro=%s",
            self.name, len(user), schema_name,
            "yes" if cached_content else "no",
            thinking_budget, max_output_tokens, pro,
        )
        result = call_json(
            self.system_prompt,
            user,
            response_schema=response_schema,
            cached_content=cached_content,
            thinking_budget=thinking_budget,
            max_output_tokens=max_output_tokens,
            pro=pro,
        )
        log.info(
            "[%s] ← json response | %d keys: %s",
            self.name, len(result), list(result.keys()),
        )
        return result

