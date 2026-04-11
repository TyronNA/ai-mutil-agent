"""
FastAPI WebSocket server — real-time multi-agent pipeline via web UI.

Run: python -m src.main serve
  or: uvicorn src.web.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="AI Multi-Agent Builder", version="2.1.0")

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

# ── In-memory stores ──────────────────────────────────────────────────────────
_sessions: dict[str, dict] = {}
_ws_queues: dict[str, asyncio.Queue] = {}
_chat_histories: dict[str, list] = {}
_stop_flags: dict[str, threading.Event] = {}   # session_id → stop signal

# ── Queue + Scheduler state ───────────────────────────────────────────────────
_main_loop: Optional[asyncio.AbstractEventLoop] = None  # set at FastAPI startup
_queue_notify = threading.Event()
_queue_lock = threading.Lock()
_queue_active_sid: Optional[str] = None
_scheduler_status: dict = {
    "running": False,
    "last_run": None,
    "next_run": None,
    "enabled": True,
    "interval_hours": float(os.environ.get("AUTO_AUDIT_INTERVAL_HOURS", "1.0")),
}


# Max session count kept in memory — oldest completed sessions are evicted first
_MAX_SESSIONS = 200  # increased since DB handles real persistence


def _prune_sessions() -> None:
    """Remove oldest done/error sessions when the store exceeds _MAX_SESSIONS."""
    if len(_sessions) <= _MAX_SESSIONS:
        return
    by_age = sorted(
        _sessions.keys(),
        key=lambda k: _sessions[k].get("created_at", ""),
    )
    for old_id in by_age[: len(_sessions) - _MAX_SESSIONS]:
        if _sessions.get(old_id, {}).get("status") in ("done", "error"):
            _sessions.pop(old_id, None)
            _stop_flags.pop(old_id, None)


class RunRequest(BaseModel):
    task: str = Field(..., max_length=4000)
    pipeline_type: str = "game"
    project_dir: Optional[str] = None
    game_project_dir: Optional[str] = None
    git_enabled: bool = True
    test_enabled: bool = True
    max_revisions: int = 3
    max_workers: int = 1
    tech_expert_pro: bool = False   # True = Gemini Pro for TechExpert planning
    slow_mode: bool = False         # True = add 5s delay between subtasks


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=8000)
    chat_id: str = Field("", max_length=64)
    model: str = "flash"     # "flash" | "pro"


class AuditRequest(BaseModel):
    audit_type: str = "audit"  # "audit" | "improve"
    game_project_dir: str = Field("", max_length=500)


class QueueAddRequest(BaseModel):
    task: str = Field(..., max_length=2000)
    pipeline_type: str = "game"
    priority: int = Field(5, ge=1, le=10)


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
    _creds = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        return JSONResponse({"error": f"Vertex AI credentials not found: {_creds}"}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    project_dir = req.project_dir or os.environ.get("EXPO_PROJECT_DIR", "")
    if project_dir:
        project_dir = str(Path(project_dir).expanduser().resolve())

    game_project_dir = req.game_project_dir or os.environ.get("GAME_PROJECT_DIR", "")
    if game_project_dir:
        game_project_dir = str(Path(game_project_dir).expanduser().resolve())

    _sessions[session_id] = {
        "session_id": session_id,
        "status": "starting",
        "task": req.task,
        "pipeline_type": req.pipeline_type,
        "messages": [],
        "pr_url": None,
        "files": [],
        "subtasks": [],
        "created_at": datetime.now().isoformat(),
    }
    stop_flag = threading.Event()
    _stop_flags[session_id] = stop_flag

    # Persist immediately so the session survives server restarts
    try:
        import src.db as _db
        _db.save_session(_sessions[session_id])
    except Exception as _e:
        logging.getLogger(__name__).warning("DB early save failed: %s", _e)

    _prune_sessions()

    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_pipeline,
        args=(
            session_id, req.task, req.pipeline_type,
            project_dir, game_project_dir,
            req.git_enabled, req.test_enabled,
            req.max_revisions, req.max_workers,
            req.tech_expert_pro, req.slow_mode,
            stop_flag, loop,
        ),
        daemon=True,
    )
    thread.start()

    return {"session_id": session_id, "ws_url": f"/ws/{session_id}"}


@app.post("/stop/{session_id}")
async def stop_session(session_id: str) -> dict:
    """Signal a running pipeline or audit to stop after the current subtask."""
    if session_id not in _sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    flag = _stop_flags.get(session_id)
    if flag:
        flag.set()
    _sessions[session_id]["status"] = "stopping"
    return {"ok": True}


@app.get("/status/{session_id}")
async def get_status(session_id: str) -> dict:
    if session_id not in _sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return _sessions[session_id]


@app.get("/sessions")
async def list_sessions() -> list:
    """Return all sessions (in-memory + DB), most recent first."""
    seen: set[str] = set()
    rows: list[dict] = []

    for s in _sessions.values():
        seen.add(s["session_id"])
        rows.append({
            "session_id": s["session_id"],
            "task": s.get("task", ""),
            "status": s.get("status", ""),
            "pipeline_type": s.get("pipeline_type", "game"),
            "pr_url": s.get("pr_url"),
            "files_count": len(s.get("files") or []),
            "created_at": s.get("created_at", ""),
            "subtasks": s.get("subtasks") or [],
            "calls": s.get("calls", 0),
            "cost_usd": s.get("cost_usd", 0.0),
        })

    return sorted(rows, key=lambda x: x["created_at"], reverse=True)


@app.post("/chat")
async def chat_with_expert(req: ChatRequest) -> dict:
    """Single-turn chat with TechExpert (Gemini Flash). Maintains history per chat_id."""
    chat_id = req.chat_id or str(uuid.uuid4())[:8]
    if chat_id not in _chat_histories:
        _chat_histories[chat_id] = []

    history = _chat_histories[chat_id]
    history.append({"role": "user", "content": req.message})

    # Build prompt from full conversation history
    turns = []
    for m in history:
        prefix = "USER" if m["role"] == "user" else "ASSISTANT"
        turns.append(f"{prefix}: {m['content']}")
    full_prompt = "\n\n".join(turns)

    try:
        from src.agents.tech_expert import TechExpertAgent
        from src.llm import call as llm_call
        agent = TechExpertAgent()
        use_pro = req.model == "pro"
        response = llm_call(
            agent.system_prompt, full_prompt,
            temperature=0.5,
            thinking_budget=2048 if use_pro else 0,
            pro=use_pro,
        )
        history.append({"role": "assistant", "content": response})
        return {"chat_id": chat_id, "response": response, "history": history}
    except Exception as e:
        history.pop()  # remove failed user message
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/audit")
async def start_audit(req: AuditRequest) -> dict:
    """Start a TechExpert-only audit or improvement scan (no Dev/QA/git)."""
    _creds = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        return JSONResponse({"error": "Vertex AI credentials not found"}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    game_project_dir = req.game_project_dir or os.environ.get("GAME_PROJECT_DIR", "")
    if game_project_dir:
        game_project_dir = str(Path(game_project_dir).expanduser().resolve())

    label = "Bug Audit" if req.audit_type == "audit" else "Improvement Scan"
    _sessions[session_id] = {
        "session_id": session_id,
        "task": f"[{label}] Automatic analysis",
        "pipeline_type": req.audit_type,
        "status": "starting",
        "messages": [],
        "pr_url": None,
        "files": [],
        "subtasks": [],
        "created_at": datetime.now().isoformat(),
    }

    stop_flag = threading.Event()
    _stop_flags[session_id] = stop_flag

    # Persist immediately
    try:
        import src.db as _db
        _db.save_session(_sessions[session_id])
    except Exception as _e:
        logging.getLogger(__name__).warning("DB early save failed: %s", _e)

    _prune_sessions()

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_run_audit,
        args=(session_id, req.audit_type, game_project_dir, stop_flag, loop),
        daemon=True,
    ).start()

    return {"session_id": session_id, "ws_url": f"/ws/{session_id}"}


# ── Task Queue endpoints ─────────────────────────────────────────────────────

@app.get("/queue")
async def list_queue() -> list:
    """Return all task queue items ordered by status then priority.
    Running items include a live status check from the in-memory session."""
    import src.db as _db
    items = _db.get_all_queue_tasks()
    # Sync in-memory status for running items (pipeline may have just completed)
    for item in items:
        if item["status"] == "running" and item.get("session_id"):
            live = _sessions.get(item["session_id"], {}).get("status", "")
            if live in ("done", "error"):
                new_status = "done" if live == "done" else "failed"
                _db.update_queue_task(item["id"], new_status)
                item["status"] = new_status
    return items


@app.post("/queue")
async def add_queue_item(req: QueueAddRequest) -> dict:
    """Add a task to the queue manually."""
    import src.db as _db
    task = _db.add_queue_task(req.task, req.pipeline_type, "manual", req.priority)
    _queue_notify.set()
    return task


@app.post("/queue/{task_id}/cancel")
async def cancel_queue_task(task_id: int) -> dict:
    """Stop a running queue task (signals its pipeline to stop after current subtask)."""
    import src.db as _db
    tasks = _db.get_all_queue_tasks()
    target = next((t for t in tasks if t["id"] == task_id), None)
    if not target:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    if target["status"] != "running":
        return JSONResponse({"error": "Task is not running"}, status_code=400)
    sid = target.get("session_id")
    if sid:
        flag = _stop_flags.get(sid)
        if flag:
            flag.set()
        if sid in _sessions:
            _sessions[sid]["status"] = "stopping"
    _db.update_queue_task(task_id, "failed")
    with _queue_lock:
        global _queue_active_sid
        if _queue_active_sid == sid:
            _queue_active_sid = None
    return {"ok": True}


@app.post("/queue/{task_id}/run")
async def run_queue_task(task_id: int) -> dict:
    """Manually start a pending queue task as a pipeline run."""
    import src.db as _db
    _creds = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        return JSONResponse({"error": "Vertex AI credentials not found"}, status_code=400)

    tasks = _db.get_all_queue_tasks()
    target = next((t for t in tasks if t["id"] == task_id), None)
    if not target:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    if target["status"] != "pending":
        return JSONResponse({"error": f"Task is not pending (status: {target['status']})"}, status_code=400)

    session_id = str(uuid.uuid4())[:8]
    game_project_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if game_project_dir:
        game_project_dir = str(Path(game_project_dir).expanduser().resolve())

    stop_flag = threading.Event()
    _stop_flags[session_id] = stop_flag
    _sessions[session_id] = {
        "session_id":    session_id,
        "status":        "starting",
        "task":          target["task"],
        "pipeline_type": target.get("pipeline_type", "game"),
        "messages":      [],
        "pr_url":        None,
        "files":         [],
        "subtasks":      [],
        "created_at":    datetime.now().isoformat(),
        "queue_task_id": task_id,
    }

    try:
        _db.save_session(_sessions[session_id])
        _db.update_queue_task(task_id, "running", session_id=session_id)
    except Exception as exc:
        logging.getLogger(__name__).warning("Queue run: DB update failed: %s", exc)

    _prune_sessions()

    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_run_pipeline,
        args=(
            session_id,
            target["task"],
            target.get("pipeline_type", "game"),
            "",
            game_project_dir,
            True,   # git_enabled
            False,  # test_enabled
            3,      # max_revisions
            1,      # max_workers
            False,  # tech_expert_pro
            False,  # slow_mode
            stop_flag,
            loop,
        ),
        daemon=True,
    ).start()

    return {"session_id": session_id, "ws_url": f"/ws/{session_id}"}


@app.delete("/queue/{task_id}")
async def delete_queue_item(task_id: int) -> dict:
    """Delete a pending/failed/done queue task (cannot delete running tasks)."""
    import src.db as _db
    ok = _db.delete_queue_task(task_id)
    if not ok:
        return JSONResponse({"error": "Task not found or currently running"}, status_code=404)
    return {"ok": True}


@app.post("/queue/clear-done")
async def clear_done_queue() -> dict:
    """Remove all done/failed/skipped tasks from the queue."""
    import src.db as _db
    tasks = _db.get_all_queue_tasks()
    removed = 0
    for t in tasks:
        if t["status"] in ("done", "failed", "skipped"):
            if _db.delete_queue_task(t["id"]):
                removed += 1
    return {"removed": removed}


@app.post("/queue/clear-all")
async def clear_all_queue() -> dict:
    """Remove ALL non-running tasks from the queue."""
    import src.db as _db
    tasks = _db.get_all_queue_tasks()
    removed = 0
    for t in tasks:
        if t["status"] != "running":
            if _db.delete_queue_task(t["id"]):
                removed += 1
    return {"removed": removed}


# ── Scheduler endpoints ──────────────────────────────────────────────────────

@app.get("/scheduler/status")
async def get_scheduler_status() -> dict:
    """Return current scheduler state."""
    return dict(_scheduler_status)


@app.post("/scheduler/toggle")
async def toggle_scheduler() -> dict:
    """Enable or disable the hourly scheduled audit cycle."""
    _scheduler_status["enabled"] = not _scheduler_status["enabled"]
    return {"enabled": _scheduler_status["enabled"]}


@app.post("/scheduler/trigger")
async def trigger_scheduler_now() -> dict:
    """Manually trigger an immediate audit+improve cycle in background."""
    if _scheduler_status["running"]:
        return JSONResponse({"error": "Scheduler is already running"}, status_code=409)
    threading.Thread(target=_run_scheduled_cycle, daemon=True, name="sched-manual").start()
    return {"ok": True, "message": "Scheduled cycle started in background"}


@app.get("/analytics/agents")
async def get_agent_analytics() -> dict:
    """Aggregate per-agent token usage across ALL sessions (DB + in-memory)."""
    import src.db as _db
    from src.llm import get_agent_usage as mem_agent_usage

    merged: dict[str, dict] = {a["agent_name"]: a for a in _db.get_all_agent_usage()}

    for sid, s in _sessions.items():
        if s.get("status") in ("running", "starting"):
            for au in mem_agent_usage(sid):
                name = au["agent_name"]
                if name in merged:
                    merged[name]["calls"]         += au["calls"]
                    merged[name]["prompt_tokens"] += au["prompt_tokens"]
                    merged[name]["output_tokens"] += au["output_tokens"]
                    merged[name]["cached_tokens"] += au["cached_tokens"]
                    merged[name]["total_tokens"]  += au["total_tokens"]
                    merged[name]["cost_usd"]       = round(merged[name]["cost_usd"] + au["cost_usd"], 6)
                else:
                    merged[name] = dict(au)

    agents = sorted(merged.values(), key=lambda x: x["cost_usd"], reverse=True)
    return {
        "agents": agents,
        "total_cost_usd": round(sum(a["cost_usd"] for a in agents), 6),
    }


@app.get("/analytics/agents/{session_id}")
async def get_agent_analytics_for_session(session_id: str) -> dict:
    """Per-agent token usage for a specific session."""
    import src.db as _db
    from src.llm import get_agent_usage as mem_agent_usage

    if session_id not in _sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    agents = mem_agent_usage(session_id)
    if not agents:
        agents = _db.get_agent_usage_for_session(session_id)

    total_cost = round(sum(a.get("cost_usd", 0) for a in agents), 6)
    return {"agents": agents, "total_cost_usd": total_cost, "session_id": session_id}


@app.get("/analytics/{session_id}")
async def get_analytics(session_id: str) -> dict:
    """Return token usage and estimated cost for a single session."""
    from src.llm import get_usage, get_pricing
    import src.db as _db
    if session_id not in _sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    # Try in-memory first (running sessions), fall back to DB data in session record
    usage = get_usage(session_id)
    s = _sessions[session_id]
    if not usage["calls"] and s.get("calls", 0):
        usage = {
            **usage,
            "calls": s.get("calls", 0),
            "prompt_tokens": s.get("prompt_tokens", 0),
            "output_tokens": s.get("output_tokens", 0),
            "cached_tokens": s.get("cached_tokens", 0),
            "total_tokens": s.get("prompt_tokens", 0) + s.get("output_tokens", 0),
            "cost_usd": s.get("cost_usd", 0.0),
            "pricing": get_pricing(),
        }
    usage["task"] = s.get("task", "")
    usage["status"] = s.get("status", "")
    return usage


@app.get("/analytics")
async def get_all_analytics() -> dict:
    """Return aggregate token usage across all sessions (in-memory + DB history)."""
    from src.llm import get_all_usage, get_pricing
    import src.db as _db

    pricing = get_pricing()

    # In-memory usage for currently-running sessions
    mem_by_sid = {u["session_id"]: u for u in get_all_usage()}

    # DB sessions that have token data
    merged: dict[str, dict] = {}
    for s in _db.load_all_sessions():
        if s.get("calls", 0) > 0:
            merged[s["session_id"]] = {
                "session_id":    s["session_id"],
                "calls":         s["calls"],
                "flash_calls":   s["calls"],
                "pro_calls":     0,
                "prompt_tokens": s["prompt_tokens"],
                "output_tokens": s["output_tokens"],
                "cached_tokens": s["cached_tokens"],
                "total_tokens":  s["prompt_tokens"] + s["output_tokens"],
                "cost_usd":      s["cost_usd"],
                "task":          s.get("task", ""),
                "status":        s.get("status", ""),
                "created_at":    s.get("created_at", ""),
                "pricing":       pricing,
            }

    # In-memory overrides DB (more accurate for running sessions)
    for sid, u in mem_by_sid.items():
        entry = dict(u)
        if sid in _sessions:
            entry.setdefault("task", _sessions[sid].get("task", ""))
            entry.setdefault("status", _sessions[sid].get("status", ""))
            entry.setdefault("created_at", _sessions[sid].get("created_at", ""))
        merged[sid] = entry

    sessions_usage = sorted(merged.values(), key=lambda x: x.get("created_at", ""), reverse=True)
    total_prompt = sum(u.get("prompt_tokens", 0) for u in sessions_usage)
    total_output = sum(u.get("output_tokens", 0) for u in sessions_usage)
    total_cached = sum(u.get("cached_tokens", 0) for u in sessions_usage)
    total_calls  = sum(u.get("calls", 0) for u in sessions_usage)
    total_cost   = sum(u.get("cost_usd", 0.0) for u in sessions_usage)
    return {
        "aggregate": {
            "calls":         total_calls,
            "prompt_tokens": total_prompt,
            "output_tokens": total_output,
            "cached_tokens": total_cached,
            "total_tokens":  total_prompt + total_output,
            "cost_usd":      round(total_cost, 6),
            "pricing":       pricing,
        },
        "sessions": sessions_usage,
    }


_EXPO_AGENTS = [
    {
        "name": "git",
        "icon": "🌿",
        "role": "Git Operations",
        "description": "Checks out a fresh branch from main, then commits + pushes all changes and creates a GitHub Pull Request.",
        "system_prompt": "Runs git CLI commands. No LLM involved — pure automation.",
        "color": "#f59e0b",
        "pipeline": "expo",
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
        "pipeline": "expo",
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
        "pipeline": "expo",
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
        "pipeline": "expo",
    },
    {
        "name": "tester",
        "icon": "📸",
        "role": "Browser Tester",
        "description": "Starts the Expo web dev server via `npx expo start --web`, waits for it to be ready, then uses Playwright headless Chromium to take a full screenshot.",
        "system_prompt": "Non-LLM agent — uses Playwright browser automation. No AI calls.",
        "color": "#f472b6",
        "pipeline": "expo",
    },
    {
        "name": "notifier",
        "icon": "🔔",
        "role": "Notifier",
        "description": "Sends a macOS desktop notification via osascript and optionally POSTs a JSON payload to a configured webhook URL (Slack, Discord, custom).",
        "system_prompt": "Non-LLM agent — uses osascript and HTTP webhook. No AI calls.",
        "color": "#fb923c",
        "pipeline": "expo",
    },
]

_GAME_AGENTS = [
    {
        "name": "tech_expert",
        "icon": "🏛",
        "role": "Tech Expert / Architect",
        "description": "Plans subtasks and architectural constraints using Gemini Pro with deep reasoning. Validates the final implementation for correctness against Phaser 4 rules and game invariants. Runs first and last in every pipeline.",
        "system_prompt": (
            "You are the lead architect of a Phaser 4 JavaScript game (Mộng Võ Lâm). "
            "Phase 1 — plan: break the task into up to 5 ordered subtasks with file assignments, "
            "architectural constraints, and test scenarios. Enforce: CombatEngine zero Phaser imports, "
            "UI_THEME for all colors, SaveManager load→modify→save, crispText() for all text, "
            "gotoScene() for transitions, full Vietnamese diacritics. "
            "Phase 2 — review: validate all written code against the same rules."
        ),
        "color": "#a78bfa",
        "pipeline": "game",
    },
    {
        "name": "dev",
        "icon": "⚔",
        "role": "Game Developer",
        "description": "Writes complete Phaser 4 JavaScript files to disk. Uses Gemini context caching for efficiency — sends only QA feedback on revisions, not the full context again. Runs in parallel across subtasks.",
        "system_prompt": (
            "You are an expert Phaser 4 / JavaScript game developer working on Mộng Võ Lâm. "
            "Write or modify game files to implement the assigned subtask. "
            "Respond in JSON: {files: {path: content}, summary}. "
            "Return COMPLETE file content — never partial. "
            "Mandatory: CombatEngine has zero Phaser imports, all colors via UI_THEME, "
            "all text via crispText(), all transitions via gotoScene(), "
            "full Vietnamese diacritics (e.g. 'Chọn' not 'Chon')."
        ),
        "color": "#34d399",
        "pipeline": "game",
    },
    {
        "name": "qa",
        "icon": "🧪",
        "role": "QA Engineer",
        "description": "Static code analysis against game invariants and TechExpert test scenarios. Flags issues by severity: critical (must fix), warning, suggestion. Rejects code that breaks CombatEngine purity, SaveManager patterns, or uses bare hex colors.",
        "system_prompt": (
            "You are a QA engineer for the Mộng Võ Lâm Phaser 4 game. "
            "Perform static analysis on the written files against: game rules (combat formula, "
            "status effects, passives), architecture invariants, TechExpert test scenarios, "
            "and Vietnamese text correctness. "
            "Respond in JSON: {passed: bool, issues: [{severity, file, line, message}], summary}. "
            "Mark passed=false if ANY critical issue exists."
        ),
        "color": "#60a5fa",
        "pipeline": "game",
    },
    {
        "name": "git",
        "icon": "🌿",
        "role": "Git Operations",
        "description": "Checks out a branch, commits all written game files, pushes, and opens a GitHub Pull Request with a summary of changes.",
        "system_prompt": "Runs git CLI commands. No LLM involved — pure automation.",
        "color": "#f59e0b",
        "pipeline": "game",
    },
    {
        "name": "notifier",
        "icon": "🔔",
        "role": "Notifier",
        "description": "Sends a macOS desktop notification and optional webhook payload on pipeline completion.",
        "system_prompt": "Non-LLM agent — uses osascript and HTTP webhook. No AI calls.",
        "color": "#fb923c",
        "pipeline": "game",
    },
]


@app.get("/agents")
async def list_agents(pipeline: Optional[str] = None) -> list:
    """Return metadata for all agents. Filter by ?pipeline=expo or ?pipeline=game."""
    if pipeline == "expo":
        return _EXPO_AGENTS
    if pipeline == "game":
        return _GAME_AGENTS
    return _EXPO_AGENTS + _GAME_AGENTS


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

def _serialize_subtasks(state) -> list[dict]:
    """Extract serializable subtask metadata from pipeline state."""
    result = []
    for st in getattr(state, "subtasks", []):
        result.append({
            "id": getattr(st, "id", 0),
            "description": getattr(st, "description", ""),
            "files_to_touch": getattr(st, "files_to_touch", []),
            "status": getattr(st, "status", ""),
            "revision_count": getattr(st, "revision_count", 0),
            "qa_passed": getattr(st, "qa_passed", None),
        })
    return result


def _persist_session_to_db(session_id: str) -> None:
    """Save session + per-agent token usage to SQLite. Best-effort."""
    try:
        import src.db as _db
        from src.llm import get_usage, get_agent_usage

        session = _sessions.get(session_id)
        if not session:
            return

        # Attach token totals to session dict before saving
        usage = get_usage(session_id)
        session["prompt_tokens"] = usage.get("prompt_tokens", 0)
        session["output_tokens"] = usage.get("output_tokens", 0)
        session["cached_tokens"] = usage.get("cached_tokens", 0)
        session["calls"]         = usage.get("calls", 0)
        session["cost_usd"]      = usage.get("cost_usd", 0.0)

        _db.save_session(session)

        # Save per-agent usage
        for au in get_agent_usage(session_id):
            _db.save_agent_usage(session_id, au["agent_name"], au)
    except Exception as exc:
        logging.getLogger(__name__).warning("DB persist error: %s", exc)


def _run_pipeline(
    session_id: str,
    task: str,
    pipeline_type: str,
    project_dir: str,
    game_project_dir: str,
    git_enabled: bool,
    test_enabled: bool,
    max_revisions: int,
    max_workers: int,
    tech_expert_pro: bool,
    slow_mode: bool,
    stop_flag: threading.Event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    session = _sessions[session_id]
    session["status"] = "running"

    # Bind session_id to this thread for LLM token tracking
    from src import llm as _llm
    _llm.set_session_id(session_id)

    def push(msg: dict) -> None:
        """Thread-safe push to the WebSocket queue."""
        session["messages"].append(msg)
        queue = _ws_queues.get(session_id)
        if queue:
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    def progress_cb(event: dict) -> None:
        push({"type": "progress", **event})

    try:
        if pipeline_type == "game":
            from src.orchestrator_game import GameOrchestrator
            orchestrator = GameOrchestrator(tech_expert_pro=tech_expert_pro)
            state = orchestrator.run(
                task=task,
                game_project_dir=game_project_dir,
                git_enabled=git_enabled,
                max_revisions=max_revisions,
                max_workers=max_workers,
                subtask_delay=5.0 if slow_mode else 0.0,
                stop_flag=stop_flag,
                progress_cb=progress_cb,
            )
            session["status"] = "done"
            session["pr_url"] = state.pr_url
            session["files"] = state.files_written
            session["subtasks"] = _serialize_subtasks(state)
            push({"type": "result", "pr_url": state.pr_url, "files": state.files_written})
        else:
            from src.orchestrator import Orchestrator
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
            session["subtasks"] = _serialize_subtasks(state)
            session["screenshots"] = getattr(state, "screenshots", [])
            push({"type": "result", "pr_url": state.pr_url, "files": state.files_written})
    except Exception as e:
        session["status"] = "error"
        session["error"] = str(e)
        push({"type": "error", "message": str(e)})
    finally:
        queue = _ws_queues.get(session_id)
        if queue:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)
        _persist_session_to_db(session_id)
        # Update queue task status if this session was started from the queue
        _sync_queue_task_on_finish(session_id)


def _sync_queue_task_on_finish(session_id: str) -> None:
    """Mark the queue task as done/failed when its pipeline finishes."""
    try:
        import src.db as _db
        session = _sessions.get(session_id, {})
        task_id = session.get("queue_task_id")
        if task_id is None:
            return
        final = session.get("status", "error")
        new_status = "done" if final == "done" else "failed"
        _db.update_queue_task(task_id, new_status)
    except Exception as exc:
        logging.getLogger(__name__).warning("_sync_queue_task_on_finish: %s", exc)


def _run_audit(
    session_id: str,
    audit_type: str,
    game_project_dir: str,
    stop_flag: threading.Event,
    loop: Optional[asyncio.AbstractEventLoop],
) -> None:
    """TechExpert-only audit/improve scan. No Dev/QA/git."""
    session = _sessions[session_id]
    session["status"] = "running"

    # Bind session_id to this thread for LLM token tracking
    from src import llm as _llm
    _llm.set_session_id(session_id)

    def push(msg: dict) -> None:
        session["messages"].append(msg)
        if loop is not None:
            queue = _ws_queues.get(session_id)
            if queue:
                asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

    try:
        from src.agents.tech_expert import TechExpertAgent
        from src.context.game_loader import load_game_context
        from src.llm import call as llm_call, delete_cache

        push({"type": "progress", "agent": "tech_expert", "message": "Loading game context..."})

        context = ""
        cache_name = ""
        if game_project_dir:
            try:
                context, cache_name = load_game_context(game_project_dir)
                push({"type": "progress", "agent": "tech_expert",
                      "message": f"Context loaded (~{len(context):,} chars)"})
            except Exception as e:
                push({"type": "progress", "agent": "tech_expert",
                      "message": f"Context load warning: {e}"})

        if stop_flag.is_set():
            push({"type": "progress", "agent": "tech_expert", "message": "Stopped before analysis."})
            session["status"] = "done"
            return

        push({"type": "progress", "agent": "tech_expert", "message": "Analysing codebase with Gemini Flash..."})

        agent = TechExpertAgent()

        if audit_type == "audit":
            task_prompt = (
                "Perform a comprehensive **bug audit** of the game source below.\n"
                "Find and report:\n"
                "1. Logic bugs in combat (damage formula, status effects, passives, targeting)\n"
                "2. Save/load issues (direct localStorage access, missing save() calls)\n"
                "3. Architecture violations (Phaser imports in CombatEngine, bare hex colors, "
                "   scene.add.text() instead of crispText, this.scene.start() instead of gotoScene)\n"
                "4. Vietnamese UI strings missing full diacritics\n"
                "5. Phaser 4 memory leaks (tweens not killed, containers not destroyed)\n\n"
                "Group by severity: CRITICAL / WARNING / SUGGESTION. Be specific — include file and line hints."
            )
        else:
            task_prompt = (
                "Suggest **improvements** for the game source below.\n"
                "Cover:\n"
                "1. Code quality and maintainability (duplication, unclear naming, complex functions)\n"
                "2. Performance (Phaser object pooling, unnecessary redraws, heavy computations)\n"
                "3. Gameplay improvements (balance, UX, missing edge-case handling)\n"
                "4. Technical debt to prioritise\n\n"
                "For each suggestion include: what to change, why it matters, estimated effort (S/M/L)."
            )

        if context:
            prompt = f"## Game source\n{context[:90_000]}\n\n{task_prompt}"
        else:
            prompt = task_prompt

        response = llm_call(
            agent.system_prompt,
            prompt,
            temperature=0.3,
            thinking_budget=8192,
            pro=True,
        )

        if cache_name:
            delete_cache(cache_name)

        session["status"] = "done"
        session["audit_result"] = response
        push({"type": "result", "agent": "tech_expert", "message": response})

    except Exception as e:
        session["status"] = "error"
        session["error"] = str(e)
        push({"type": "error", "message": str(e)})
    finally:
        if loop is not None:
            queue = _ws_queues.get(session_id)
            if queue:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)
        _persist_session_to_db(session_id)


def _extract_tasks_from_audit(audit_text: str, source: str) -> list[dict]:
    """Use LLM (Flash) to extract concrete actionable tasks from an audit/improve report."""
    from pydantic import BaseModel as _PB
    from typing import List as _List
    from src.llm import call_json as _llm_json

    class _Task(_PB):
        title: str
        priority: int

    class _TaskList(_PB):
        tasks: _List[_Task]

    system = (
        "You extract concrete, actionable implementation tasks from game code audit reports. "
        "Return only specific tasks suitable for an AI game-dev pipeline. "
        "Bugs must rank higher than improvements."
    )
    user = (
        f"Game code {'bug audit' if source == 'audit' else 'improvement scan'} report:\n\n"
        f"{audit_text[:8000]}\n\n"
        "Extract at most 8 concrete, actionable tasks from the report above.\n"
        "Each title must be self-contained and ≤120 chars "
        "(e.g. 'Fix critical-hit formula in CombatEngine.js that ignores DEF stat').\n"
        "Priority: 10=game-breaking bug, 8-9=important fix, 5-7=improvement, 1-4=minor polish.\n"
        "Skip vague or generic advice — only include items that can be implemented directly."
    )

    log = logging.getLogger(__name__)
    try:
        result = _llm_json(system, user, response_schema=_TaskList)
        tasks = result.get("tasks", []) if isinstance(result, dict) else []
        return [
            {
                "task": str(t.get("title", "")).strip()[:200],
                "pipeline_type": "game",
                "source": source,
                "priority": max(1, min(10, int(t.get("priority", 5)))),
            }
            for t in tasks
            if str(t.get("title", "")).strip()
        ]
    except Exception as exc:
        log.warning("_extract_tasks_from_audit failed: %s", exc)
        return []


def _run_scheduled_cycle() -> None:
    """Run audit + improve scans and add extracted tasks to the queue. Blocking."""
    import src.db as _db
    log = logging.getLogger("scheduler")

    game_project_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if game_project_dir:
        game_project_dir = str(Path(game_project_dir).expanduser().resolve())

    _creds = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        log.warning("Scheduler: vertex-ai.json credentials not found, skipping cycle")
        return

    _scheduler_status["running"] = True
    _scheduler_status["last_run"] = datetime.now().isoformat()
    total_added = 0

    try:
        for audit_type in ("audit", "improve"):
            session_id = f"sched-{str(uuid.uuid4())[:6]}"
            stop_flag = threading.Event()
            _stop_flags[session_id] = stop_flag
            label = "Bug Audit" if audit_type == "audit" else "Improvement Scan"
            _sessions[session_id] = {
                "session_id":  session_id,
                "task":        f"[Scheduled {label}]",
                "pipeline_type": audit_type,
                "status":      "starting",
                "messages":    [],
                "pr_url":      None,
                "files":       [],
                "subtasks":    [],
                "created_at":  datetime.now().isoformat(),
            }
            log.info("Scheduler: running %s ...", audit_type)
            _run_audit(session_id, audit_type, game_project_dir, stop_flag, loop=_main_loop)

            audit_result = _sessions[session_id].get("audit_result", "")
            if audit_result:
                tasks = _extract_tasks_from_audit(audit_result, source=audit_type)
                for t in tasks:
                    _db.add_queue_task(**t)
                    total_added += 1
                log.info("Scheduler: added %d tasks from %s", len(tasks), audit_type)

        log.info("Scheduler: cycle complete — %d total new tasks", total_added)
        _queue_notify.set()
    except Exception as exc:
        log.error("Scheduler cycle error: %s", exc)
    finally:
        _scheduler_status["running"] = False


def _scheduler_loop() -> None:
    """Daemon thread: runs a scheduled audit+improve cycle every N hours."""
    import time
    import datetime as _dt

    log = logging.getLogger("scheduler")
    interval = _scheduler_status["interval_hours"]

    auto_start = os.environ.get("AUTO_AUDIT_ON_STARTUP", "false").lower() in ("1", "true", "yes")
    if auto_start:
        delay = int(os.environ.get("AUTO_AUDIT_STARTUP_DELAY", "60"))
        log.info("Scheduler: running initial cycle in %ds ...", delay)
        time.sleep(delay)
        if _scheduler_status["enabled"]:
            _run_scheduled_cycle()

    while True:
        next_run = datetime.now() + _dt.timedelta(hours=interval)
        _scheduler_status["next_run"] = next_run.isoformat()
        time.sleep(interval * 3600)
        if _scheduler_status["enabled"]:
            _run_scheduled_cycle()


def _queue_worker_loop() -> None:
    """Daemon thread: processes task queue items sequentially."""
    global _queue_active_sid
    import time
    import src.db as _db
    log = logging.getLogger("queue_worker")

    while True:
        # Wait for a signal (new task added, pipeline finished) or poll every 5s
        _queue_notify.wait(timeout=5.0)
        _queue_notify.clear()

        with _queue_lock:
            # If a task is currently running, check if it has finished
            if _queue_active_sid:
                sid_status = _sessions.get(_queue_active_sid, {}).get("status", "")
                if sid_status in ("done", "error", "stopping"):
                    new_status = "done" if sid_status == "done" else "failed"
                    for t in _db.get_all_queue_tasks():
                        if t.get("session_id") == _queue_active_sid and t["status"] == "running":
                            _db.update_queue_task(t["id"], new_status)
                            break
                    log.info("Queue: session %s finished (%s)", _queue_active_sid, sid_status)
                    _queue_active_sid = None
                else:
                    # Still running — wait for next notification (no spin)
                    continue

            # Pick next pending task
            next_task = _db.get_next_pending_task()
            if not next_task:
                continue

            task_id = next_task["id"]
            session_id = str(uuid.uuid4())[:8]
            _queue_active_sid = session_id

        # ── Start pipeline outside lock ──────────────────────────────────────
        game_project_dir = os.environ.get("GAME_PROJECT_DIR", "")
        if game_project_dir:
            game_project_dir = str(Path(game_project_dir).expanduser().resolve())

        stop_flag = threading.Event()
        _stop_flags[session_id] = stop_flag
        _sessions[session_id] = {
            "session_id":    session_id,
            "status":        "starting",
            "task":          next_task["task"],
            "pipeline_type": next_task.get("pipeline_type", "game"),
            "messages":      [],
            "pr_url":        None,
            "files":         [],
            "subtasks":      [],
            "created_at":    datetime.now().isoformat(),
            "queue_task_id": task_id,
        }

        try:
            _db.save_session(_sessions[session_id])
            _db.update_queue_task(task_id, "running", session_id=session_id)
        except Exception as exc:
            log.warning("Queue: DB update failed: %s", exc)

        _prune_sessions()
        log.info("Queue: starting task %d → session %s: %s", task_id, session_id, next_task["task"][:80])

        threading.Thread(
            target=_run_pipeline,
            args=(
                session_id,
                next_task["task"],
                next_task.get("pipeline_type", "game"),
                "",  # project_dir (expo)
                game_project_dir,
                True,   # git_enabled
                False,  # test_enabled
                3,      # max_revisions
                1,      # max_workers
                False,  # tech_expert_pro
                False,  # slow_mode
                stop_flag,
                _main_loop,
            ),
            daemon=True,
        ).start()
        # Worker will be woken up by _queue_notify.set() in _run_pipeline's finally block


def _init_db() -> None:
    """Initialize SQLite DB and load historical sessions into memory on startup."""
    try:
        import src.db as _db
        _db.init_db()
        count = 0
        for s in _db.load_all_sessions():
            if s["session_id"] not in _sessions:
                _sessions[s["session_id"]] = s
                count += 1
        if count:
            logging.getLogger(__name__).info("DB: loaded %d historical sessions", count)
    except Exception as exc:
        logging.getLogger(__name__).warning("DB init failed (non-fatal): %s", exc)


@app.on_event("startup")
async def _on_startup() -> None:
    """Capture event loop and start background scheduler thread (queue is manual-only)."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
    logging.getLogger(__name__).info("Scheduler started (queue is manual-run only)")


_init_db()
