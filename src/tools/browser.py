"""Browser automation — start Expo web and take screenshots with Playwright."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import requests


def start_expo_web(project_dir: str, port: int = 19006, timeout: int = 45) -> Optional[subprocess.Popen]:
    """
    Start Expo web dev server in the background.
    Polls until ready or timeout. Returns the Popen process (caller must kill it).
    Returns None if startup fails.
    """
    proc = subprocess.Popen(
        ["npx", "expo", "start", "--web", f"--port={port}"],
        cwd=project_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{port}"
    for _ in range(timeout):
        time.sleep(1)
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code < 500:
                return proc
        except requests.exceptions.ConnectionError:
            pass

    proc.terminate()
    return None


def screenshot_url(url: str, output_path: str, wait_ms: int = 4000) -> str:
    """
    Navigate to a URL using Playwright headless Chromium and save a screenshot.
    Returns the output_path on success.
    """
    from playwright.sync_api import sync_playwright

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=output_path, full_page=False)
        browser.close()
    return output_path


def take_expo_screenshots(
    project_dir: str,
    screenshots_dir: str,
    port: int = 19006,
) -> list[str]:
    """
    Start Expo web, take a screenshot, stop server.
    Returns list of screenshot file paths (empty if failed).
    """
    proc = None
    taken: list[str] = []
    try:
        proc = start_expo_web(project_dir, port)
        if proc is None:
            return []
        shot_path = str(Path(screenshots_dir) / "expo-web.png")
        screenshot_url(f"http://localhost:{port}", shot_path)
        taken.append(shot_path)
    except Exception:
        pass
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
    return taken
