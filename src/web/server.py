"""
FastAPI WebSocket server — real-time multi-agent pipeline via web UI.

Run: python -m src.main serve
  or: uvicorn src.web.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AI Multi-Agent Expo Builder", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Serve static files (web UI) — prefer Next.js build output, fall back to legacy static/
# NOTE: StaticFiles must NOT be mounted at "/" or it swallows all API routes.
# We serve the index.html explicitly at GET "/" and mount assets at "/ui".
_project_root = Path(__file__).parent.parent.parent
_nextjs_out = _project_root / "ui" / "out"
_static_dir = Path(__file__).parent / "static"

_ui_dir: Optional[Path] = None
if _nextjs_out.exists():
    _ui_dir = _nextjs_out
elif _static_dir.exists():
    _ui_dir = _static_dir

# ── In-memory session store ──────────────────────────────────────────────────
# session_id → {"status": str, "messages": list, "pr_url": str, "files": list}
_sessions: dict[str, dict] = {}
# session_id → asyncio.Queue for WebSocket messages
_ws_queues: dict[str, asyncio.Queue] = {}


class RunRequest(BaseModel):
    task: str
    project_dir: Optional[str] = None
    git_enabled: bool = True
    test_enabled: bool = True
    max_revisions: int = 3


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    _ws_queues[session_id] = queue
    try:
        while True:
            msg = await queue.get()
            if msg is None:  # sentinel — pipeline finished
                await websocket.send_text(json.dumps({"type": "done"}))
                break
            await websocket.send_text(json.dumps(msg))
    except WebSocketDisconnect:
        pass
    finally:
        _ws_queues.pop(session_id, None)


# ── REST endpoints ───────────────────────────────────────────────────────────

@app.post("/run")
async def start_run(req: RunRequest) -> dict:
    """Start a pipeline run. Returns session_id immediately."""
    if not os.environ.get("GEMINI_API_KEY"):
        return JSONResponse({"error": "GEMINI_API_KEY not set"}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    project_dir = req.project_dir or os.environ.get("EXPO_PROJECT_DIR", "")
    if project_dir:
        project_dir = str(Path(project_dir).expanduser().resolve())

    _sessions[session_id] = {
        "session_id": session_id,
        "status": "starting",
        "task": req.task,
        "messages": [],
        "pr_url": None,
        "files": [],
    }

    # Run orchestrator in a background thread so we don't block the event loop
    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_pipeline,
        args=(session_id, req.task, project_dir, req.git_enabled, req.test_enabled, req.max_revisions, loop),
        daemon=True,
    )
    thread.start()

    return {"session_id": session_id, "ws_url": f"/ws/{session_id}"}


@app.get("/status/{session_id}")
async def get_status(session_id: str) -> dict:
    if session_id not in _sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return _sessions[session_id]


@app.get("/agents")
async def list_agents() -> list:
    """Return metadata for all agents (name, role, icon, system_prompt)."""
    return [
            {
                "name": "git",
                "icon": "⑀",
                "role": "Git Operations",
                "description": "Checks out a fresh branch from main, then commits + pushes all changes and creates a GitHub Pull Request.",
                "system_prompt": "Runs git CLI commands. No LLM involved — pure automation.",
                "color": "#f59e0b",
            },
            {
                "name": "planner",
                "icon": "🗺",
                "role": "Task Planner",
                "description": "Reads the entire project file tree + package.json to understand the codebase, then breaks the task into ordered subtasks with specific files to touch.",
                "system_prompt": (
                    "You are a senior Expo React Native architect and planner. "
                    "Given an existing Expo project and a task, break it into clear, ordered subtasks. "
                    "Respond in JSON: {plan_summary, subtasks: [{id, description, files_to_touch}]}. "
                    "Maximum 5 subtasks. Use Expo Router v3, TypeScript, NativeWind or StyleSheet. "
                    "Match the coding style and libraries already used in the project."
                ),
                "color": "#a78bfa",
            },
            {
                "name": "coder",
                "icon": "💻",
                "role": "Code Writer",
                "description": "Reads existing file contents for context, then writes or modifies TypeScript/React Native files directly to disk. On reviewer rejection, receives the full feedback and fixes all issues.",
                "system_prompt": (
                    "You are an expert Expo React Native developer. "
                    "Given a subtask and existing code context, write or modify files to implement it. "
                    "Respond in JSON: {files: {path: content}, summary}. "
                    "Write production-quality TypeScript. Return COMPLETE updated file content. "
                    "Do NOT wrap code in markdown fences inside JSON values."
                ),
                "color": "#34d399",
            },
            {
                "name": "reviewer",
                "icon": "🔍",
                "role": "Code Reviewer",
                "description": "Reads the actual written files from disk, then reviews for TypeScript errors, broken Expo Router usage, missing imports, security issues, and performance problems.",
                "system_prompt": (
                    "You are a senior React Native / Expo code reviewer. "
                    "Review code for correctness, security, performance, and Expo best practices. "
                    "Respond in JSON: {approved: bool, feedback: str, summary: str}. "
                    "Focus on bugs, crashes, and security vulnerabilities above all. "
                    "Approve if code is production-ready."
                ),
                "color": "#60a5fa",
            },
            {
                "name": "tester",
                "icon": "📸",
                "role": "Browser Tester",
                "description": "Starts the Expo web dev server via `npx expo start --web`, waits for it to be ready, then uses Playwright headless Chromium to take a full screenshot.",
                "system_prompt": "Non-LLM agent — uses Playwright browser automation. No AI calls.",
                "color": "#f472b6",
            },
            {
                "name": "notifier",
                "icon": "🔔",
                "role": "Notifier",
                "description": "Sends a macOS desktop notification via osascript and optionally POSTs a JSON payload to a configured webhook URL (Slack, Discord, custom).",
                "system_prompt": "Non-LLM agent — uses osascript and HTTP webhook. No AI calls.",
                "color": "#fb923c",
            },
        ]


# Note: The root "/" is served by the StaticFiles mount above (Next.js out/ or static/).
# The following fallback only applies if neither directory exists.
@app.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the web UI index page."""
    if _ui_dir:
        index_file = _ui_dir / "index.html"
        if index_file.exists():
            return HTMLResponse(index_file.read_text())
    return HTMLResponse("<h1>AI Multi-Agent</h1><p>No web UI build found. Run <code>make web</code>.</p>")


# Mount static assets AFTER all API routes are registered so the catch-all
# StaticFiles handler doesn't shadow /run, /agents, /ws, etc.
if _ui_dir:
    app.mount("/ui", StaticFiles(directory=str(_ui_dir)), name="static")


# ── Pipeline runner (runs in thread) ────────────────────────────────────────

def _run_pipeline(
    session_id: str,
    task: str,
    project_dir: str,
    git_enabled: bool,
    test_enabled: bool,
    max_revisions: int,
    loop: asyncio.AbstractEventLoop,
) -> None:
    from src.orchestrator import Orchestrator

    session = _sessions[session_id]
    session["status"] = "running"

    def push(msg: dict) -> None:
        """Thread-safe push to the WebSocket queue."""
        session["messages"].append(msg)
        queue = _ws_queues.get(session_id)
        if queue:
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    def progress_cb(event: dict) -> None:
        push({"type": "progress", **event})

    try:
        orchestrator = Orchestrator()
        state = orchestrator.run(
            task=task,
            project_dir=project_dir,
            git_enabled=git_enabled,
            test_enabled=test_enabled,
            max_revisions=max_revisions,
            progress_cb=progress_cb,
        )
        session["status"] = "done"
        session["pr_url"] = state.pr_url
        session["files"] = state.files_written
        session["screenshots"] = state.screenshots
        push({"type": "result", "pr_url": state.pr_url, "files": state.files_written})
    except Exception as e:
        session["status"] = "error"
        session["error"] = str(e)
        push({"type": "error", "message": str(e)})
    finally:
        # Send done sentinel
        queue = _ws_queues.get(session_id)
        if queue:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
