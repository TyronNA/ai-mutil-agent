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
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="AI Multi-Agent Expo Builder", version="2.0.0")

# Serve static files (web UI)
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

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


@app.get("/")
async def index() -> HTMLResponse:
    html_path = _static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>AI Multi-Agent Builder</h1><p>Static files not found.</p>")


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
