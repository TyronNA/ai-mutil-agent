"""
FastAPI WebSocket server — real-time multi-agent pipeline via web UI.

Run: python -m src.main serve
  or: uvicorn src.web.server:app --port 8000
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="AI Multi-Agent Builder", version="2.1.0")

_cors_allow_origins = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]
_cors_extra = [o.strip() for o in os.environ.get("WEB_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_extra:
    _cors_allow_origins.extend(_cors_extra)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
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
_prompt_root = _project_root / "prompt"
_queue_retry_counts: dict[int, int] = {}
_QUEUE_MAX_AUTO_RETRIES = int(os.environ.get("QUEUE_MAX_AUTO_RETRIES", "1"))
_AUTO_AUDIT_ENQUEUE_TASKS = os.environ.get("AUTO_AUDIT_ENQUEUE_TASKS", "false").lower() in (
    "1", "true", "yes"
)

# ── Queue + Scheduler state ───────────────────────────────────────────────────
_main_loop: Optional[asyncio.AbstractEventLoop] = None  # set at FastAPI startup
_queue_notify = threading.Event()
_queue_lock = threading.Lock()
_queue_active_sid: Optional[str] = None
_scheduler_status: dict = {
    "running": False,
    "last_run": None,
    "next_run": None,
    "enabled": False,
    "interval_hours": float(os.environ.get("AUTO_AUDIT_INTERVAL_HOURS", "1.0")),
}


# Max session count kept in memory — oldest completed sessions are evicted first
_MAX_SESSIONS = 200  # increased since DB handles real persistence
_AUTH_COOKIE = "ama_auth"
_AUTH_TTL_SECONDS = int(os.environ.get("WEB_AUTH_TTL_SECONDS", "86400"))
_WEB_API_KEY = os.environ.get("WEB_API_KEY", "").strip()

# Track last task run date for daily git sync (YYYY-MM-DD)
_last_task_run_date: Optional[str] = None
_last_task_run_date_lock = threading.Lock()


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


def _is_auth_enabled() -> bool:
    return bool(_WEB_API_KEY)


def _prune_auth_sessions() -> None:
    try:
        import src.db as _db
        _db.prune_expired_auth_sessions(time.time())
    except Exception:
        # Auth checks still work; pruning is best-effort.
        pass


def _create_auth_session() -> str:
    _prune_auth_sessions()
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + _AUTH_TTL_SECONDS
    import src.db as _db
    _db.save_auth_session(token, expires_at)
    return token


def _is_valid_auth_session(token: Optional[str]) -> bool:
    if not token:
        return False
    _prune_auth_sessions()
    try:
        import src.db as _db
        row = _db.get_auth_session(token)
    except Exception:
        return False
    if not row:
        return False
    expiry = float(row.get("expires_at") or 0)
    if expiry <= time.time():
        try:
            import src.db as _db
            _db.delete_auth_session(token)
        except Exception:
            pass
        return False
    return True


def _delete_auth_session(token: Optional[str]) -> None:
    if token:
        try:
            import src.db as _db
            _db.delete_auth_session(token)
        except Exception:
            pass


def _is_public_path(path: str) -> bool:
    if path == "/" or path.startswith("/ui"):
        return True
    return path in {"/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path.startswith("/auth/") or _is_public_path(path):
        return await call_next(request)

    if not _is_auth_enabled():
        return JSONResponse(
            {"error": "Server auth is not configured. Set WEB_API_KEY in .env."},
            status_code=503,
        )

    token = request.cookies.get(_AUTH_COOKIE)
    if not _is_valid_auth_session(token):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return await call_next(request)


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
    character: str = Field("tech_expert", max_length=32)
    model: str = "flash"     # "flash" | "pro"


def _normalize_chat_character(raw: str) -> str:
    val = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if val in {"mate", "assistant", "virtual_mate"}:
        return "mate"
    if val in {"tech", "techexpert", "tech_expert", "tech_expect", "expert"}:
        return "tech_expert"
    return "tech_expert"


def _load_prompt_file(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _compose_chat_system_prompt(character: str, default_prompt: str) -> str:
    # TechExpert chat prompt should stay stable and architecture-focused.
    # Only Mate gets dynamic base/soul composition from prompt files.
    if character != "mate":
        return default_prompt

    base = _load_prompt_file(_prompt_root / character / "base.md")
    soul = _load_prompt_file(_prompt_root / character / "soul.md")
    if base and soul:
        return f"{base}\n\n## Soul\n{soul}"
    if base:
        return base
    if soul:
        return f"{default_prompt}\n\n## Soul\n{soul}"
    return default_prompt


class AuditRequest(BaseModel):
    audit_type: str = "audit"  # "audit" | "improve"
    game_project_dir: str = Field("", max_length=500)


class QueueAddRequest(BaseModel):
    task: str = Field(..., max_length=2000)
    pipeline_type: str = "game"
    priority: int = Field(5, ge=1, le=10)


class QueueResumeRequest(BaseModel):
    error_log: str = Field("", max_length=2000)


class LoginRequest(BaseModel):
    api_key: str = Field(..., max_length=512)


# ── WebSocket endpoint ───────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    if not _is_auth_enabled() or not _is_valid_auth_session(websocket.cookies.get(_AUTH_COOKIE)):
        await websocket.close(code=1008)
        return

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

@app.get("/auth/me")
async def auth_me(request: Request) -> dict:
    token = request.cookies.get(_AUTH_COOKIE)
    return {
        "authenticated": _is_valid_auth_session(token),
        "configured": _is_auth_enabled(),
    }


@app.post("/auth/login")
async def auth_login(req: LoginRequest) -> Response:
    if not _is_auth_enabled():
        return JSONResponse(
            {"error": "Server auth is not configured. Set WEB_API_KEY in .env."},
            status_code=503,
        )

    if not hmac.compare_digest(req.api_key, _WEB_API_KEY):
        return JSONResponse({"error": "Invalid API key"}, status_code=401)

    token = _create_auth_session()
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=_AUTH_COOKIE,
        value=token,
        max_age=_AUTH_TTL_SECONDS,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    _delete_auth_session(request.cookies.get(_AUTH_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(_AUTH_COOKIE, path="/")
    return response

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
    """Single-turn chat with TechExpert. Maintains history per chat_id."""
    character = _normalize_chat_character(req.character)
    chat_id = req.chat_id or str(uuid.uuid4())[:8]
    history_key = f"{character}:{chat_id}"
    if history_key not in _chat_histories:
        _chat_histories[history_key] = []

    history = _chat_histories[history_key]
    history.append({"role": "user", "content": req.message})

    # Build prompt from full conversation history
    turns = []
    for m in history:
        prefix = "USER" if m["role"] == "user" else "ASSISTANT"
        turns.append(f"{prefix}: {m['content']}")
    full_prompt = "\n\n".join(turns)

    try:
        from src.agents.tech_expert import TechExpertAgent
        from src.llm import call as llm_call, get_effective_model_name
        from src import llm as _llm
        import os as _os
        _slog = logging.getLogger(__name__)
        agent = TechExpertAgent()
        mate_default_prompt = (
            "You are Mate, a witty and evolving virtual assistant for general Q&A.\n"
            "Tone: playful, funny, and lively when appropriate.\n"
            "Priority: factual accuracy, clear explanations, and practical guidance.\n"
            "Never sacrifice correctness for jokes.\n"
            "If uncertain, say what is uncertain and propose a verification step.\n"
            "Respond naturally in Vietnamese when user writes Vietnamese."
        )
        default_system_prompt = (
            agent.chat_system_prompt if character == "tech_expert" else mate_default_prompt
        )
        system_prompt = _compose_chat_system_prompt(character, default_system_prompt)
        requested_model = (req.model or "flash").strip().lower()
        use_pro = requested_model == "pro"

        # Bind chat turns into usage tracking for analytics visibility.
        _llm.set_session_id(f"chat-{chat_id}")
        _llm.set_agent_name(f"{character}_chat")

        effective_model = get_effective_model_name(pro=use_pro)
        downgraded = use_pro and "pro" not in effective_model.lower()
        _slog.info(
            "── /chat | character=%s | chat_id=%s | requested=%s | use_pro=%s | effective_model=%s | "
            "downgraded=%s | PRO_MODEL=%s | GCP_LOCATION=%s",
            character, chat_id, requested_model, use_pro, effective_model,
            downgraded,
            _os.environ.get("PRO_MODEL", "gemini-2.5-pro"),
            _os.environ.get("GCP_LOCATION", "us-central1"),
        )
        if downgraded:
            _slog.warning(
                "⚡ /chat pro request DOWNGRADED to flash for chat_id=%s — "
                "check PRO_MODEL env var",
                chat_id,
            )
        response = llm_call(
            system_prompt, full_prompt,
            temperature=0.5,
            thinking_budget=2048 if use_pro else 0,
            pro=use_pro,
        )
        history.append({"role": "assistant", "content": response})
        return {
            "chat_id": chat_id,
            "character": character,
            "response": response,
            "history": history,
            "requested_model": requested_model,
            "effective_model": effective_model,
            "downgraded_to_flash": use_pro and "pro" not in effective_model.lower(),
        }
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
    Running items include a live status check from the in-memory session.
    Legacy rows with status='pending' are normalized to 'waiting' for clients.
    """
    import src.db as _db
    items = _db.get_all_queue_tasks()
    # Sync in-memory status for running items (pipeline may have just completed)
    for item in items:
        sid = item.get("session_id")
        if item["status"] == "running" and sid:
            live = _sessions.get(sid, {}).get("status", "")
            if live in ("done", "error"):
                new_status = "done" if live == "done" else "failed"
                _db.update_queue_task(item["id"], new_status)
                item["status"] = new_status
        # Enrich with branch from session if available
        if sid and sid in _sessions:
            item["branch"] = _sessions[sid].get("branch", "")
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


@app.post("/queue/{task_id}/resume")
async def resume_queue_task(task_id: int, req: QueueResumeRequest) -> dict:
    """Resume a failed/blocked task with optional operator notes on the SAME task."""
    import src.db as _db

    tasks = _db.get_all_queue_tasks()
    target = next((t for t in tasks if t["id"] == task_id), None)
    if not target:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    if target["status"] not in ("failed", "blocked"):
        return JSONResponse({"error": f"Task cannot be resumed (status: {target['status']})"}, status_code=400)

    note = (req.error_log or "").strip() or "Manual resume requested by operator"
    ok = _db.resume_task_with_context(task_id, note)
    if not ok:
        return JSONResponse({"error": "Failed to resume task"}, status_code=500)

    _queue_retry_counts[task_id] = 0
    _queue_notify.set()
    return {"ok": True, "status": "waiting"}


@app.post("/queue/{task_id}/run")
async def run_queue_task(task_id: int) -> dict:
    """Run a pending task.

    - If no task is currently running: starts immediately → status 'running'.
    - If a task is already running: queues this one → status 'waiting'.
      The queue worker will auto-start it when the running task finishes.
    """
    import src.db as _db
    _creds = Path(__file__).parent.parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        return JSONResponse({"error": "Vertex AI credentials not found"}, status_code=400)

    tasks = _db.get_all_queue_tasks()
    target = next((t for t in tasks if t["id"] == task_id), None)
    if not target:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    if target["status"] not in ("pending", "waiting"):
        return JSONResponse({"error": f"Task cannot be run (status: {target['status']})"}, status_code=400)

    game_project_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if game_project_dir:
        game_project_dir = str(Path(game_project_dir).expanduser().resolve())

    with _queue_lock:
        global _queue_active_sid
        if _queue_active_sid and _sessions.get(_queue_active_sid, {}).get("status") in ("starting", "running", "stopping"):
            # Another task is actively running — queue this one as waiting
            try:
                _db.update_queue_task(task_id, "waiting")
            except Exception as exc:
                logging.getLogger(__name__).warning("Queue waiting: DB update failed: %s", exc)
            _queue_notify.set()
            return {"queued": True, "waiting": True, "message": "Task queued — will start when current run finishes"}

        # No active task — start immediately, claim the active slot
        session_id = str(uuid.uuid4())[:8]
        _queue_active_sid = session_id

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
    """Delete a waiting/failed/done queue task (cannot delete running tasks)."""
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
        if t["status"] in ("done", "failed", "blocked", "skipped"):
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
# ── Preview endpoints ───────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    branch: str = Field(..., max_length=200)


@app.get("/preview/info")
async def get_preview_info() -> dict:
    """Return game directory, current git branch, and all local branches."""
    import re
    import subprocess as _sp

    game_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if game_dir:
        game_dir = str(Path(game_dir).expanduser().resolve())

    if not game_dir or not Path(game_dir).exists():
        return {"game_dir": game_dir, "current_branch": "", "branches": []}

    current_branch = ""
    branches: list[str] = []
    try:
        result = _sp.run(
            ["git", "branch", "--list"],
            cwd=game_dir, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                is_current = line.startswith("*")
                name = line.strip().lstrip("* ")
                if name and re.match(r'^[a-zA-Z0-9/_\-.]+$', name):
                    branches.append(name)
                    if is_current:
                        current_branch = name
    except Exception as e:
        logging.getLogger(__name__).warning("git branch list failed: %s", e)

    return {"game_dir": game_dir, "current_branch": current_branch, "branches": branches}


@app.post("/preview/checkout")
async def checkout_preview_branch(req: CheckoutRequest) -> dict:
    """Checkout a branch in the game project directory for preview."""
    import re
    import subprocess as _sp

    # Validate branch name to prevent command injection
    if not re.match(r'^[a-zA-Z0-9/_\-.]+$', req.branch):
        return JSONResponse({"error": "Invalid branch name"}, status_code=400)

    game_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if game_dir:
        game_dir = str(Path(game_dir).expanduser().resolve())

    if not game_dir or not Path(game_dir).exists():
        return JSONResponse({"error": "GAME_PROJECT_DIR not configured or not found"}, status_code=400)

    result = _sp.run(
        ["git", "checkout", req.branch],
        cwd=game_dir, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return JSONResponse({"error": result.stderr.strip() or result.stdout.strip()}, status_code=400)

    return {"ok": True, "branch": req.branch}


@app.get("/preview/game-html")
async def get_game_html() -> HTMLResponse:
    """Return the game's index.html with a console interceptor injected.
    
    The interceptor forwards console.log/warn/error/info to the parent window
    via postMessage so the Preview tab can display them without cross-origin issues.
    """
    import re as _re

    game_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if not game_dir:
        return HTMLResponse("<html><body><p>GAME_PROJECT_DIR not set in .env</p></body></html>")

    game_path = Path(game_dir).expanduser().resolve()
    index_file = game_path / "index.html"

    if not index_file.exists():
        return HTMLResponse(
            f"<html><body><p>index.html not found in {game_path}</p></body></html>"
        )

    content = index_file.read_text(encoding="utf-8", errors="replace")

    # Absolute root paths (e.g. /src/main.js) bypass <base href>. Rewrite them
    # to /game/... so assets are served from the mounted game static directory.
    content = _re.sub(r'(\b(?:src|href)=["\'])/(?!/)', r'\1/game/', content)

    interceptor = """<base href="/game/">
<script type="importmap">
{
    "imports": {
        "phaser": "/preview/phaser-shim.js"
    }
}
</script>
<script>
(function(){
  function _send(level,args){
    try{
      var msg=Array.prototype.slice.call(args).map(function(a){
        try{return typeof a==='object'?JSON.stringify(a):String(a);}catch(e){return String(a);}
      }).join(' ');
      window.parent.postMessage({type:'console',level:level,message:msg},'*');
    }catch(e){}
  }
  ['log','warn','error','info'].forEach(function(l){
    var orig=console[l].bind(console);
    console[l]=function(){orig.apply(console,arguments);_send(l,arguments);};
  });
  window.addEventListener('error',function(e){
    _send('error',['Uncaught: '+(e.message||String(e))]);
  });
  window.addEventListener('unhandledrejection',function(e){
    _send('error',['Unhandled rejection: '+(e.reason||String(e))]);
  });
})();
</script>"""

    if "</head>" in content:
        content = content.replace("</head>", interceptor + "\n</head>", 1)
    elif "<body" in content:
        content = _re.sub(r'(<body[^>]*>)', r'\1' + interceptor, content, count=1)
    else:
        content = interceptor + content

    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@app.get("/preview/phaser-shim.js")
async def get_phaser_shim() -> Response:
    """Provide a Phaser ESM shim that includes a default export.

    Some game files import Phaser as default (import Phaser from "phaser").
    Native browser ESM for phaser.esm.js exposes named exports only.
    This shim preserves named exports and adds a default namespace export.
    """
    shim = """import * as PhaserNS from '/game/node_modules/phaser/dist/phaser.esm.js';
export * from '/game/node_modules/phaser/dist/phaser.esm.js';
export default PhaserNS;
"""
    return Response(content=shim, media_type="application/javascript", headers={"Cache-Control": "no-store"})


@app.get("/game/{requested_path:path}")
async def serve_game_file(requested_path: str):
    """Serve game files with fallback to public/ for static assets.

    Vite serves `public/*` at web root during dev/build, so game code requests
    paths like `/assets/...` and `/sw.js`. In preview mode we mount under `/game`,
    therefore we resolve `/game/<path>` against both:
    1) <GAME_PROJECT_DIR>/<path>
    2) <GAME_PROJECT_DIR>/public/<path>
    """
    game_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if not game_dir:
        return JSONResponse({"error": "GAME_PROJECT_DIR not set"}, status_code=400)

    game_root = Path(game_dir).expanduser().resolve()
    if not game_root.exists():
        return JSONResponse({"error": "GAME_PROJECT_DIR not found"}, status_code=404)

    rel = Path(requested_path)
    candidates = [
        (game_root / rel).resolve(),
        (game_root / "public" / rel).resolve(),
    ]

    for candidate in candidates:
        try:
            candidate.relative_to(game_root)
        except ValueError:
            continue
        if candidate.is_file():
            return FileResponse(candidate)

    return JSONResponse({"detail": "Not Found"}, status_code=404)


@app.get("/sw.js")
async def serve_game_service_worker():
    """Serve game's public sw.js for absolute '/sw.js' registrations."""
    game_dir = os.environ.get("GAME_PROJECT_DIR", "")
    if not game_dir:
        return JSONResponse({"error": "GAME_PROJECT_DIR not set"}, status_code=400)

    game_root = Path(game_dir).expanduser().resolve()
    sw_file = (game_root / "public" / "sw.js").resolve()
    try:
        sw_file.relative_to(game_root)
    except ValueError:
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if not sw_file.is_file():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(sw_file, media_type="application/javascript")


@app.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/debug/llm-routing")
async def debug_llm_routing() -> dict:
    """Show effective LLM routing from the CURRENT running process env."""
    from src.llm import get_effective_model_name

    flash_model = get_effective_model_name(pro=False)
    pro_model = get_effective_model_name(pro=True)
    downgraded = "pro" not in pro_model.lower()

    return {
        "model_env": os.environ.get("MODEL", "gemini-3-flash-preview"),
        "pro_model_env": os.environ.get("PRO_MODEL", "gemini-2.5-pro"),
        "gcp_location": os.environ.get("GCP_LOCATION", "us-central1"),
        "effective_flash_model": flash_model,
        "effective_pro_model": pro_model,
        "pro_is_downgraded_to_flash": downgraded,
    }


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

# Log preview game path at startup for visibility.
_game_preview_dir = os.environ.get("GAME_PROJECT_DIR", "")
if _game_preview_dir:
    _game_preview_path = Path(_game_preview_dir).expanduser().resolve()
    if _game_preview_path.exists():
        logging.getLogger(__name__).info("Game preview serving from %s (with public/ fallback)", _game_preview_path)


# ── Pipeline runner (runs in thread) ────────────────────────────────────────

def _maybe_daily_git_sync(game_project_dir: str, push_fn=None) -> None:
    """If today is a new calendar day vs the last task run, stash dirty files
    then pull origin main — so each new day starts from a clean, up-to-date base.
    Uses 'git pull origin main' explicitly to never accidentally pull a different
    remote branch or lose origin/HEAD tracking.
    """
    global _last_task_run_date
    if not game_project_dir:
        return

    import subprocess as _sp
    today = datetime.now().strftime("%Y-%m-%d")

    with _last_task_run_date_lock:
        if _last_task_run_date == today:
            return  # already synced today

    _log = logging.getLogger("git-daily-sync")

    def _notify(msg: str) -> None:
        _log.info(msg)
        if push_fn:
            push_fn({"type": "progress", "agent": "git", "message": msg})

    try:
        _notify(f"New day detected ({today}) — syncing git before run...")

        # 1. Stash uncommitted changes so checkout won't fail
        status = _sp.run(
            ["git", "status", "--porcelain"],
            cwd=game_project_dir, capture_output=True, text=True, timeout=30,
        )
        if status.returncode == 0 and status.stdout.strip():
            _notify("Stashing uncommitted changes...")
            stash = _sp.run(
                ["git", "stash", "--include-untracked"],
                cwd=game_project_dir, capture_output=True, text=True, timeout=30,
            )
            if stash.returncode != 0:
                _log.warning("git stash failed: %s", stash.stderr.strip())
            else:
                _notify("Changes stashed successfully.")

        # 2. Checkout main branch
        checkout = _sp.run(
            ["git", "checkout", "main"],
            cwd=game_project_dir, capture_output=True, text=True, timeout=30,
        )
        if checkout.returncode != 0:
            _log.warning("git checkout main failed: %s", checkout.stderr.strip())
            _notify(f"Warning: git checkout main failed — {checkout.stderr.strip()}")

        # 3. Pull origin main explicitly (never just 'git pull' to preserve HEAD tracking)
        _notify("Pulling latest from origin main...")
        pull = _sp.run(
            ["git", "pull", "origin", "main"],
            cwd=game_project_dir, capture_output=True, text=True, timeout=60,
        )
        if pull.returncode == 0:
            _notify(f"Git sync complete: {pull.stdout.strip() or 'Already up to date.'}")
        else:
            _log.warning("git pull origin main failed: %s", pull.stderr.strip())
            _notify(f"Warning: git pull failed — {pull.stderr.strip()}")
    except Exception as exc:
        _log.warning("Daily git sync error: %s", exc)
    finally:
        # Always mark today as synced even on partial failure
        with _last_task_run_date_lock:
            _last_task_run_date = today


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

    # ── Daily git sync: if it's a new day, stash dirty files and pull origin main ──
    if pipeline_type == "game":
        _maybe_daily_git_sync(game_project_dir, push_fn=push)

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
            session["branch"] = getattr(state, "branch", "")
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
    """Mark the queue task as done/failed when its pipeline finishes.
    Clears the active session slot so the queue worker can auto-start waiting tasks.
    """
    try:
        import src.db as _db
        session = _sessions.get(session_id, {})
        task_id = session.get("queue_task_id")
        if task_id is not None:
            final = session.get("status", "error")
            if final == "done":
                _queue_retry_counts.pop(task_id, None)
                _db.update_queue_task(task_id, "done")
            else:
                err = str(session.get("error", "Unknown pipeline error")).strip()
                policy, retry_limit = _compute_retry_policy(err)
                retries = _queue_retry_counts.get(task_id, 0)
                if retries < retry_limit:
                    _queue_retry_counts[task_id] = retries + 1
                    _db.requeue_task_with_context(task_id, err, retries + 1)
                    logging.getLogger(__name__).warning(
                        "Queue task %s auto-retry #%s/%s (%s) after failure: %s",
                        task_id,
                        retries + 1,
                        retry_limit,
                        policy,
                        err[:200],
                    )
                else:
                    _db.mark_task_blocked(task_id, err)
                    logging.getLogger(__name__).warning(
                        "Queue task %s blocked after %s/%s auto-retry attempt(s) - manual review required",
                        task_id,
                        retries,
                        retry_limit,
                    )
        # Always clear active slot and wake queue worker (even if no queue_task_id)
        global _queue_active_sid
        with _queue_lock:
            if _queue_active_sid == session_id:
                _queue_active_sid = None
        _queue_notify.set()
    except Exception as exc:
        logging.getLogger(__name__).warning("_sync_queue_task_on_finish: %s", exc)


def _compute_retry_policy(error_text: str) -> tuple[str, int]:
    """Classify failure and return policy + retry limit for smarter retries."""
    text = (error_text or "").lower()

    transient_markers = [
        "timeout", "timed out", "rate limit", "429", "connection", "network", "service unavailable", "503",
    ]
    code_fix_markers = [
        "syntax", "type", "lint", "compile", "test failed", "assertion", "module not found", "import error",
    ]
    qa_markers = [
        "qa", "invariant", "review", "needs_revision", "rejected", "violation",
    ]

    if any(m in text for m in transient_markers):
        return ("transient", max(2, _QUEUE_MAX_AUTO_RETRIES + 1))
    if any(m in text for m in code_fix_markers):
        return ("code_fix", max(1, _QUEUE_MAX_AUTO_RETRIES))
    if any(m in text for m in qa_markers):
        return ("qa_revision", max(1, _QUEUE_MAX_AUTO_RETRIES))
    return ("generic", max(1, _QUEUE_MAX_AUTO_RETRIES))


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
    _llm.set_agent_name("tech_expert_audit")

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
                if _AUTO_AUDIT_ENQUEUE_TASKS:
                    tasks = _extract_tasks_from_audit(audit_result, source=audit_type)
                    for t in tasks:
                        _db.add_queue_task(**t)
                        total_added += 1
                    log.info("Scheduler: added %d tasks from %s", len(tasks), audit_type)
                else:
                    log.info(
                        "Scheduler: enqueue disabled (AUTO_AUDIT_ENQUEUE_TASKS=false); "
                        "keeping %s result as report only",
                        audit_type,
                    )

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
    global _last_task_run_date
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
        # Seed last-run date so we don't trigger a spurious sync on fresh deploy
        last_ts = _db.get_last_completed_task_updated_at()
        if last_ts:
            _last_task_run_date = last_ts[:10]  # YYYY-MM-DD
            logging.getLogger(__name__).info("Daily git sync: last task date = %s", _last_task_run_date)
    except Exception as exc:
        logging.getLogger(__name__).warning("DB init failed (non-fatal): %s", exc)


@app.on_event("startup")
async def _on_startup() -> None:
    """Capture event loop, start scheduler and queue-worker background threads."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
    threading.Thread(target=_queue_worker_loop, daemon=True, name="queue-worker").start()
    logging.getLogger(__name__).info("Scheduler and queue worker started")


_init_db()
