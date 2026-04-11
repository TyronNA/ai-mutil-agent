"""LLM client — Gemini via Vertex AI (google-genai SDK + service account)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from google.oauth2 import service_account

log = logging.getLogger(__name__)

_client: Optional[genai.Client] = None
_client_lock = threading.Lock()

# ── Token usage tracking ─────────────────────────────────────────────────────

@dataclass
class _Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    flash_calls: int = 0
    pro_calls: int = 0
    flash_prompt_tokens: int = 0
    flash_output_tokens: int = 0
    flash_cached_tokens: int = 0
    pro_prompt_tokens: int = 0
    pro_output_tokens: int = 0
    pro_cached_tokens: int = 0

# Gemini pricing estimates (USD per token, as of 2025)
_FLASH_INPUT_PRICE  = 0.10  / 1_000_000   # $0.10 / 1M
_FLASH_OUTPUT_PRICE = 0.40  / 1_000_000   # $0.40 / 1M
_FLASH_CACHED_PRICE = 0.025 / 1_000_000   # $0.025 / 1M
_PRO_INPUT_PRICE    = 1.25  / 1_000_000   # $1.25 / 1M
_PRO_OUTPUT_PRICE   = 5.00  / 1_000_000   # $5.00 / 1M

_token_stats: dict[str, _Usage] = {}       # session_id → accumulated usage
_token_lock = threading.Lock()
_session_local = threading.local()          # per-thread: current_session_id
_agent_local  = threading.local()           # per-thread: current_agent_name

# Per-agent stats: session_id → agent_name → _Usage
_agent_stats: dict[str, dict[str, _Usage]] = {}


def set_session_id(session_id: str) -> None:
    """Bind the current thread to a session for token tracking."""
    _session_local.session_id = session_id
    with _token_lock:
        if session_id not in _token_stats:
            _token_stats[session_id] = _Usage()


def get_session_id() -> str:
    """Return the session_id bound to the current thread, or '' if none."""
    return getattr(_session_local, "session_id", "")


def set_agent_name(name: str) -> None:
    """Bind an agent name to the current thread for per-agent token tracking."""
    _agent_local.agent_name = name


def get_agent_name() -> str:
    """Return the agent name bound to the current thread, or '' if none."""
    return getattr(_agent_local, "agent_name", "")


def get_usage(session_id: str) -> dict:
    """Return token usage dict for *session_id*, plus USD cost estimate."""
    with _token_lock:
        u = _token_stats.get(session_id, _Usage())
        return _usage_to_dict(session_id, u)


def get_agent_usage(session_id: str) -> list[dict]:
    """Return per-agent usage list for a session (in-memory only)."""
    with _token_lock:
        agents = _agent_stats.get(session_id, {})
        return [
            _agent_usage_to_dict(session_id, name, u)
            for name, u in sorted(agents.items(), key=lambda x: x[1].calls, reverse=True)
        ]


def _agent_usage_to_dict(session_id: str | None, agent_name: str, u: _Usage) -> dict:
    cost = _usage_cost_usd(u)
    return {
        "session_id": session_id,
        "agent_name": agent_name,
        "calls": u.calls,
        "flash_calls": u.flash_calls,
        "pro_calls": u.pro_calls,
        "prompt_tokens": u.prompt_tokens,
        "output_tokens": u.output_tokens,
        "cached_tokens": u.cached_tokens,
        "total_tokens": u.prompt_tokens + u.output_tokens,
        "cost_usd": round(cost, 6),
    }


def get_pricing() -> dict:
    """Return current pricing rates for use in API responses."""
    return {
        "flash_input_per_1m":  _FLASH_INPUT_PRICE  * 1_000_000,
        "flash_output_per_1m": _FLASH_OUTPUT_PRICE * 1_000_000,
        "flash_cached_per_1m": _FLASH_CACHED_PRICE * 1_000_000,
        "pro_input_per_1m":    _PRO_INPUT_PRICE    * 1_000_000,
        "pro_output_per_1m":   _PRO_OUTPUT_PRICE   * 1_000_000,
    }


def get_all_usage() -> list[dict]:
    """Return usage dicts for all sessions, sorted newest-first."""
    with _token_lock:
        return [_usage_to_dict(sid, u) for sid, u in _token_stats.items()]


def _usage_to_dict(session_id: str, u: _Usage) -> dict:
    """Convert _Usage to a JSON-serialisable dict with cost estimates."""
    cost = _usage_cost_usd(u)
    return {
        "session_id": session_id,
        "calls": u.calls,
        "flash_calls": u.flash_calls,
        "pro_calls": u.pro_calls,
        "prompt_tokens": u.prompt_tokens,
        "output_tokens": u.output_tokens,
        "cached_tokens": u.cached_tokens,
        "total_tokens": u.prompt_tokens + u.output_tokens,
        "cost_usd": round(cost, 6),
        "pricing": {
            "flash_input_per_1m":  _FLASH_INPUT_PRICE  * 1_000_000,
            "flash_output_per_1m": _FLASH_OUTPUT_PRICE * 1_000_000,
            "flash_cached_per_1m": _FLASH_CACHED_PRICE * 1_000_000,
            "pro_input_per_1m":    _PRO_INPUT_PRICE    * 1_000_000,
            "pro_output_per_1m":   _PRO_OUTPUT_PRICE   * 1_000_000,
        },
    }


def _usage_cost_usd(u: _Usage) -> float:
    """Compute USD cost using per-model token buckets, with legacy fallback."""
    has_model_split = (
        u.flash_prompt_tokens
        + u.flash_output_tokens
        + u.flash_cached_tokens
        + u.pro_prompt_tokens
        + u.pro_output_tokens
        + u.pro_cached_tokens
    ) > 0
    if not has_model_split:
        # Backward compatibility for previously persisted in-memory snapshots.
        prompt_net = max(0, u.prompt_tokens - u.cached_tokens)
        return (
            prompt_net        * _FLASH_INPUT_PRICE
            + u.cached_tokens * _FLASH_CACHED_PRICE
            + u.output_tokens * _FLASH_OUTPUT_PRICE
        )

    flash_prompt_net = max(0, u.flash_prompt_tokens - u.flash_cached_tokens)
    pro_prompt_net = max(0, u.pro_prompt_tokens - u.pro_cached_tokens)
    return (
        flash_prompt_net        * _FLASH_INPUT_PRICE
        + u.flash_cached_tokens * _FLASH_CACHED_PRICE
        + u.flash_output_tokens * _FLASH_OUTPUT_PRICE
        + pro_prompt_net        * _PRO_INPUT_PRICE
        + u.pro_output_tokens   * _PRO_OUTPUT_PRICE
    )


def _record_tokens(model: str, prompt: int, output: int, cached: int) -> None:
    """Accumulate token counts for the current thread's session and agent."""
    sid = get_session_id()
    if not sid:
        return
    is_pro = "pro" in model.lower()
    with _token_lock:
        # Session-level tracking
        if sid not in _token_stats:
            _token_stats[sid] = _Usage()
        u = _token_stats[sid]
        u.calls += 1
        u.prompt_tokens += prompt
        u.output_tokens += output
        u.cached_tokens += cached
        if is_pro:
            u.pro_calls += 1
            u.pro_prompt_tokens += prompt
            u.pro_output_tokens += output
            u.pro_cached_tokens += cached
        else:
            u.flash_calls += 1
            u.flash_prompt_tokens += prompt
            u.flash_output_tokens += output
            u.flash_cached_tokens += cached

        # Per-agent tracking
        agent = get_agent_name()
        if agent:
            if sid not in _agent_stats:
                _agent_stats[sid] = {}
            if agent not in _agent_stats[sid]:
                _agent_stats[sid][agent] = _Usage()
            au = _agent_stats[sid][agent]
            au.calls += 1
            au.prompt_tokens += prompt
            au.output_tokens += output
            au.cached_tokens += cached
            if is_pro:
                au.pro_calls += 1
                au.pro_prompt_tokens += prompt
                au.pro_output_tokens += output
                au.pro_cached_tokens += cached
            else:
                au.flash_calls += 1
                au.flash_prompt_tokens += prompt
                au.flash_output_tokens += output
                au.flash_cached_tokens += cached

DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_PRO_MODEL = "gemini-2.5-pro"

_CREDENTIALS_FILE = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Retry settings for transient API errors (429 rate-limit, 5xx server errors)
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 2.0  # seconds (doubles each attempt)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _load_vertex_config() -> tuple[str, str]:
    """Return (project_id, location) from env vars or service account file."""
    project_id = os.environ.get("GCP_PROJECT", "")
    location = os.environ.get("GCP_LOCATION", "us-central1")
    if not project_id and _CREDENTIALS_FILE.exists():
        with open(_CREDENTIALS_FILE) as f:
            project_id = json.load(f).get("project_id", "")
    return project_id, location


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking — safe under multi-threaded parallel agents
                if not _CREDENTIALS_FILE.exists():
                    raise EnvironmentError(
                        f"Vertex AI credentials not found: {_CREDENTIALS_FILE}\n"
                        "Place the service account JSON at config/vertex-ai.json"
                    )
                credentials = service_account.Credentials.from_service_account_file(
                    str(_CREDENTIALS_FILE), scopes=_VERTEX_SCOPES
                )
                project_id, location = _load_vertex_config()
                _client = genai.Client(
                    vertexai=True,
                    project=project_id,
                    location=location,
                    credentials=credentials,
                )
                log.info(
                    "Vertex AI client initialized — project=%s location=%s | "
                    "flash_model=%s | pro_model=%s",
                    project_id, location,
                    os.environ.get("MODEL", DEFAULT_MODEL),
                    os.environ.get("PRO_MODEL", DEFAULT_PRO_MODEL),
                )
    return _client


def _model_name() -> str:
    return os.environ.get("MODEL", DEFAULT_MODEL)


def _pro_model_name() -> str:
    return os.environ.get("PRO_MODEL", DEFAULT_PRO_MODEL)


def get_effective_model_name(pro: bool = False) -> str:
    """Return the model name that will be used for the given request type."""
    return _pro_model_name() if pro else _model_name()


def _should_retry(exc: Exception) -> bool:
    """Return True if the exception is a transient API error worth retrying."""
    msg = str(exc).lower()
    return any(str(code) in msg for code in _RETRYABLE_STATUS) or "rate" in msg or "quota" in msg


def _call_with_retry(client: genai.Client, model: str, contents: str, config: types.GenerateContentConfig) -> str:
    """Call the API with exponential backoff retry on transient errors."""
    content_len = len(contents) if isinstance(contents, str) else sum(len(str(c)) for c in contents)
    log.info(
        "▶ Sending to Gemini | model=%s | content=%d chars | temp=%s | max_tokens=%s | json=%s | cache=%s | thinking=%s",
        model,
        content_len,
        getattr(config, "temperature", "?"),
        getattr(config, "max_output_tokens", "?"),
        getattr(config, "response_mime_type", None) == "application/json",
        bool(getattr(config, "cached_content", None)),
        bool(getattr(config, "thinking_config", None)),
    )
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            log.info("  ↺ Retry attempt %d/%d for model=%s", attempt + 1, _MAX_RETRIES, model)
        t0 = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            elapsed = time.monotonic() - t0
            text = response.text
            if not text:
                raise ValueError("Empty response from model (possibly blocked by safety filters)")
            if response.usage_metadata:
                u = response.usage_metadata
                cached = getattr(u, "cached_content_token_count", 0) or 0
                _record_tokens(
                    model,
                    u.prompt_token_count or 0,
                    u.candidates_token_count or 0,
                    cached,
                )
                log.info(
                    "◀ Gemini response | model=%s | elapsed=%.2fs | prompt_tokens=%d (cached=%d) | output_tokens=%d | total=%d | preview: %s",
                    model,
                    elapsed,
                    u.prompt_token_count or 0,
                    cached,
                    u.candidates_token_count or 0,
                    u.total_token_count or 0,
                    text[:120].replace("\n", " "),
                )
            else:
                log.info(
                    "◀ Gemini response | model=%s | elapsed=%.2fs | no usage metadata | preview: %s",
                    model, elapsed, text[:120].replace("\n", " "),
                )
            return text
        except Exception as exc:
            elapsed = time.monotonic() - t0
            if attempt < _MAX_RETRIES - 1 and _should_retry(exc):
                log.warning(
                    "⚠ LLM transient error (attempt %d/%d, elapsed=%.2fs): %s — retrying in %.1fs",
                    attempt + 1, _MAX_RETRIES, elapsed, exc, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                log.error("✗ LLM call failed (attempt %d/%d, elapsed=%.2fs): %s", attempt + 1, _MAX_RETRIES, elapsed, exc)
                raise
    raise RuntimeError("Unreachable")


def call(system: str, user: str, temperature: float = 0.3, thinking_budget: int = 0, pro: bool = False) -> str:
    """Send a prompt with a system instruction and return the text response.

    `pro=True` uses the configured pro model, unless Flash-only mode is enabled.
    `thinking_budget` controls native thinking tokens (0 = off).
    """
    client = _get_client()
    model = _pro_model_name() if pro else _model_name()
    log.info(
        "── call() routing | pro=%s | resolved=%s | thinking_budget=%d",
        pro, model, thinking_budget,
    )
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=temperature,
        max_output_tokens=8192,
        **({
            "thinking_config": types.ThinkingConfig(thinking_budget=thinking_budget)
        } if thinking_budget > 0 else {}),
    )
    return _call_with_retry(client, model, user, config)


def call_json(
    system: str,
    user: str,
    response_schema=None,
    cached_content: Optional[str] = None,
    thinking_budget: int = 0,
    max_output_tokens: int = 16384,
    pro: bool = False,
) -> dict:
    """Send a prompt expecting strict JSON back. Returns parsed dict.

    Args:
        response_schema: Pydantic model class to constrain JSON output shape.
        cached_content: Cache name from `create_cache()`.
        thinking_budget: Native thinking tokens (0 = off).
        max_output_tokens: Override for code-heavy agents that need large outputs.
        pro: Use the configured pro model instead of flash unless Flash-only mode is enabled.
    """
    client = _get_client()
    model = _pro_model_name() if pro else _model_name()
    schema_name = response_schema.__name__ if response_schema else "none"
    log.info(
        "── call_json() | pro=%s | model=%s | sys=%d chars | user=%d chars | schema=%s | cached=%s | thinking_budget=%d | max_tokens=%d",
        pro, model,
        len(system or ""), len(user or ""), schema_name,
        "yes" if cached_content else "no",
        thinking_budget, max_output_tokens,
    )
    config = types.GenerateContentConfig(
        system_instruction=system or None if not cached_content else None,
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_schema=response_schema or None,
        cached_content=cached_content or None,
        **({
            "thinking_config": types.ThinkingConfig(thinking_budget=thinking_budget)
        } if thinking_budget > 0 else {}),
    )
    raw = _call_with_retry(client, model, user, config)
    # Strip markdown fences in case the model wraps output despite mime type
    raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    parsed = json.loads(raw)
    log.info(
        "── call_json() parsed | model=%s | %d top-level keys: %s",
        model, len(parsed), list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
    )
    return parsed


def create_cache(system: str, content: str, ttl_seconds: int = 600) -> Optional[str]:
    """Cache a large shared context (system instruction + content) for reuse across calls.

    Returns the cache name string on success, or None if creation fails
    (e.g., content is below the model's minimum token threshold).
    Callers should always handle the None case and fall back to normal calls.
    """
    try:
        client = _get_client()
        cache = client.caches.create(
            model=_model_name(),
            config=types.CreateCachedContentConfig(
                system_instruction=system or None,
                contents=[content],
                ttl=f"{ttl_seconds}s",
            ),
        )
        log.info("Context cache created: %s (content=%d chars, ttl=%ds)", cache.name, len(content), ttl_seconds)
        return cache.name
    except Exception as exc:
        log.debug("Context cache skipped (content likely below min token threshold): %s", exc)
        return None


def delete_cache(cache_name: str) -> None:
    """Delete a context cache. Best-effort — errors are silently ignored."""
    try:
        _get_client().caches.delete(name=cache_name)
        log.debug("Context cache deleted: %s", cache_name)
    except Exception:
        pass
