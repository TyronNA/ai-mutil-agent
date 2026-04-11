"""LLM client — Gemini via Vertex AI (google-genai SDK + service account)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from google.oauth2 import service_account

log = logging.getLogger(__name__)

_client: Optional[genai.Client] = None
_client_lock = threading.Lock()

DEFAULT_MODEL = "gemini-3-flash-preview"
DEFAULT_PRO_MODEL = DEFAULT_MODEL
# Temporary rollout switch: keep all calls on Flash, including pro=True paths.
FORCE_FLASH_ONLY = True

_CREDENTIALS_FILE = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

# Retry settings for transient API errors (429 rate-limit, 5xx server errors)
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 2.0  # seconds (doubles each attempt)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _load_vertex_config() -> tuple[str, str]:
    """Return (project_id, location) from env vars or service account file."""
    project_id = os.environ.get("GCP_PROJECT", "")
    location = os.environ.get("GCP_LOCATION", "global")
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
                log.info("Vertex AI client initialized — project=%s location=%s", project_id, location)
    return _client


def _model_name() -> str:
    return os.environ.get("MODEL", DEFAULT_MODEL)


def _pro_model_name() -> str:
    if FORCE_FLASH_ONLY:
        return _model_name()
    return os.environ.get("PRO_MODEL", DEFAULT_PRO_MODEL)


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
        "── call_json() JSON | model=%s | sys=%d chars | user=%d chars | schema=%s | cached=%s | thinking_budget=%d | max_tokens=%d",
        model, len(system or ""), len(user or ""), schema_name,
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
