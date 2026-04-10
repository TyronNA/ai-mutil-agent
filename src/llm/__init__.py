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
DEFAULT_PRO_MODEL = "gemini-3-pro-preview"

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
    return os.environ.get("PRO_MODEL", DEFAULT_PRO_MODEL)


def _should_retry(exc: Exception) -> bool:
    """Return True if the exception is a transient API error worth retrying."""
    msg = str(exc).lower()
    return any(str(code) in msg for code in _RETRYABLE_STATUS) or "rate" in msg or "quota" in msg


def _call_with_retry(client: genai.Client, model: str, contents: str, config: types.GenerateContentConfig) -> str:
    """Call the API with exponential backoff retry on transient errors."""
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(model=model, contents=contents, config=config)
            text = response.text
            if not text:
                raise ValueError("Empty response from model (possibly blocked by safety filters)")
            if response.usage_metadata:
                u = response.usage_metadata
                cached = getattr(u, "cached_content_token_count", 0) or 0
                log.info(
                    "Tokens — prompt: %d (cached: %d), output: %d, total: %d",
                    u.prompt_token_count or 0,
                    cached,
                    u.candidates_token_count or 0,
                    u.total_token_count or 0,
                )
            return text
        except Exception as exc:
            if attempt < _MAX_RETRIES - 1 and _should_retry(exc):
                log.warning("LLM transient error (attempt %d/%d): %s — retrying in %.1fs",
                            attempt + 1, _MAX_RETRIES, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise
    raise RuntimeError("Unreachable")


def call(system: str, user: str, temperature: float = 0.3, thinking_budget: int = 0, pro: bool = False) -> str:
    """Send a prompt with a system instruction and return the text response.

    `pro=True` uses gemini-3-pro-preview for complex reasoning tasks.
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
        pro: Use gemini-3-pro-preview instead of flash (for complex tasks).
    """
    client = _get_client()
    model = _pro_model_name() if pro else _model_name()
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
    return json.loads(raw)


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
