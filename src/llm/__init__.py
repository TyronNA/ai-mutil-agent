"""LLM client — Gemini via google-genai SDK."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

_client: Optional[genai.Client] = None
DEFAULT_MODEL = "gemini-2.0-flash"

# Retry settings for transient API errors (429 rate-limit, 5xx server errors)
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 2.0  # seconds (doubles each attempt)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY not set. Copy .env.example to .env and fill it in."
            )
        _client = genai.Client(api_key=api_key)
    return _client


def _model_name() -> str:
    return os.environ.get("MODEL", DEFAULT_MODEL)


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
            return response.text
        except Exception as exc:
            if attempt < _MAX_RETRIES - 1 and _should_retry(exc):
                log.warning("LLM transient error (attempt %d/%d): %s — retrying in %.1fs",
                            attempt + 1, _MAX_RETRIES, exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise
    raise RuntimeError("Unreachable")


def call(system: str, user: str, temperature: float = 0.3) -> str:
    """Send a prompt with a system instruction and return the text response."""
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=temperature,
        max_output_tokens=8192,
    )
    return _call_with_retry(client, _model_name(), user, config)


def call_json(system: str, user: str) -> dict:
    """Send a prompt expecting strict JSON back. Returns parsed dict."""
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        temperature=0.2,
        max_output_tokens=8192,
        response_mime_type="application/json",
    )
    raw = _call_with_retry(client, _model_name(), user, config)
    # Strip markdown fences if model still wraps output
    raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    return json.loads(raw)


