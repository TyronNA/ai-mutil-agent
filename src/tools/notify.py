"""Notification tools — macOS desktop notification + webhook."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

import requests


def notify_macos(title: str, message: str, subtitle: str = "") -> None:
    """Send a macOS desktop notification via osascript."""
    script = f'display notification "{message}" with title "{title}"'
    if subtitle:
        script += f' subtitle "{subtitle}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except FileNotFoundError:
        pass  # not macOS


def notify_terminal_bell() -> None:
    """Ring the terminal bell."""
    print("\a", end="", flush=True)


def notify_webhook(url: str, payload: dict) -> bool:
    """POST a JSON payload to a webhook URL. Returns True on success."""
    if not url:
        return False
    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.ok
    except Exception:
        return False


def notify_all(
    title: str,
    message: str,
    pr_url: Optional[str] = None,
    screenshots: Optional[list[str]] = None,
) -> None:
    """
    Send all configured notifications:
    - macOS desktop notification
    - Terminal bell
    - Webhook (if WEBHOOK_URL is set)
    """
    notify_terminal_bell()
    notify_macos(title=title, message=message, subtitle=pr_url or "")

    webhook_url = os.environ.get("WEBHOOK_URL", "")
    if webhook_url:
        notify_webhook(webhook_url, {
            "title": title,
            "message": message,
            "pr_url": pr_url,
            "screenshots": screenshots or [],
        })
