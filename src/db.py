"""SQLite persistence — sessions, subtasks, and per-agent token tracking."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "sessions.db"
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist yet."""
    with _lock:
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id     TEXT PRIMARY KEY,
                    task           TEXT NOT NULL DEFAULT '',
                    status         TEXT NOT NULL DEFAULT 'starting',
                    pipeline_type  TEXT NOT NULL DEFAULT 'game',
                    pr_url         TEXT,
                    files_json     TEXT NOT NULL DEFAULT '[]',
                    subtasks_json  TEXT NOT NULL DEFAULT '[]',
                    error          TEXT,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    prompt_tokens  INTEGER NOT NULL DEFAULT 0,
                    output_tokens  INTEGER NOT NULL DEFAULT 0,
                    cached_tokens  INTEGER NOT NULL DEFAULT 0,
                    calls          INTEGER NOT NULL DEFAULT 0,
                    cost_usd       REAL    NOT NULL DEFAULT 0.0
                );

                CREATE TABLE IF NOT EXISTS agent_usage (
                    session_id    TEXT NOT NULL,
                    agent_name    TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    calls         INTEGER NOT NULL DEFAULT 0,
                    cost_usd      REAL    NOT NULL DEFAULT 0.0,
                    updated_at    TEXT NOT NULL,
                    PRIMARY KEY (session_id, agent_name)
                );

                CREATE TABLE IF NOT EXISTS task_queue (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    task          TEXT NOT NULL,
                    pipeline_type TEXT NOT NULL DEFAULT 'game',
                    status        TEXT NOT NULL DEFAULT 'pending',
                    source        TEXT NOT NULL DEFAULT 'manual',
                    priority      INTEGER NOT NULL DEFAULT 5,
                    session_id    TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def save_session(session: dict) -> None:
    """Upsert a session record (insert or update on conflict)."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, task, status, pipeline_type, pr_url,
                     files_json, subtasks_json, error,
                     created_at, updated_at,
                     prompt_tokens, output_tokens, cached_tokens, calls, cost_usd)
                VALUES (?,?,?,?,?,?,?,?,?,?, ?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status        = excluded.status,
                    pr_url        = excluded.pr_url,
                    files_json    = excluded.files_json,
                    subtasks_json = excluded.subtasks_json,
                    error         = excluded.error,
                    updated_at    = excluded.updated_at,
                    prompt_tokens = excluded.prompt_tokens,
                    output_tokens = excluded.output_tokens,
                    cached_tokens = excluded.cached_tokens,
                    calls         = excluded.calls,
                    cost_usd      = excluded.cost_usd
                """,
                (
                    session["session_id"],
                    session.get("task", ""),
                    session.get("status", "starting"),
                    session.get("pipeline_type", "game"),
                    session.get("pr_url"),
                    json.dumps(session.get("files") or []),
                    json.dumps(session.get("subtasks") or []),
                    session.get("error"),
                    session.get("created_at", now),
                    now,
                    session.get("prompt_tokens", 0),
                    session.get("output_tokens", 0),
                    session.get("cached_tokens", 0),
                    session.get("calls", 0),
                    session.get("cost_usd", 0.0),
                ),
            )
            conn.commit()
        except Exception as exc:
            log.warning("DB save_session error: %s", exc)
        finally:
            conn.close()


def load_all_sessions() -> list[dict]:
    """Return all sessions ordered newest-first (max 200)."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["files"] = json.loads(d.pop("files_json") or "[]")
                d["subtasks"] = json.loads(d.pop("subtasks_json") or "[]")
                result.append(d)
            return result
        except Exception as exc:
            log.warning("DB load_all_sessions error: %s", exc)
            return []
        finally:
            conn.close()


def save_agent_usage(session_id: str, agent_name: str, usage: dict) -> None:
    """Upsert per-agent token usage for a session."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO agent_usage
                    (session_id, agent_name, prompt_tokens, output_tokens,
                     cached_tokens, calls, cost_usd, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id, agent_name) DO UPDATE SET
                    prompt_tokens = excluded.prompt_tokens,
                    output_tokens = excluded.output_tokens,
                    cached_tokens = excluded.cached_tokens,
                    calls         = excluded.calls,
                    cost_usd      = excluded.cost_usd,
                    updated_at    = excluded.updated_at
                """,
                (
                    session_id,
                    agent_name,
                    usage.get("prompt_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cached_tokens", 0),
                    usage.get("calls", 0),
                    usage.get("cost_usd", 0.0),
                    now,
                ),
            )
            conn.commit()
        except Exception as exc:
            log.warning("DB save_agent_usage error: %s", exc)
        finally:
            conn.close()


def get_agent_usage_for_session(session_id: str) -> list[dict]:
    """Return per-agent usage rows for a specific session (DB only)."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM agent_usage WHERE session_id = ? ORDER BY cost_usd DESC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.warning("DB get_agent_usage_for_session error: %s", exc)
            return []
        finally:
            conn.close()


# ── Task Queue ───────────────────────────────────────────────────────────────

def add_queue_task(
    task: str,
    pipeline_type: str = "game",
    source: str = "manual",
    priority: int = 5,
) -> dict:
    """Insert a task into the queue. Returns the created row as dict."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO task_queue (task, pipeline_type, status, source, priority, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (task[:2000], pipeline_type, source, max(1, min(10, priority)), now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM task_queue WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return dict(row) if row else {}
        except Exception as exc:
            log.warning("DB add_queue_task error: %s", exc)
            return {}
        finally:
            conn.close()


def get_next_pending_task() -> Optional[dict]:
    """Return the highest-priority pending task (priority DESC, created_at ASC)."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_queue
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            log.warning("DB get_next_pending_task error: %s", exc)
            return None
        finally:
            conn.close()


def get_all_queue_tasks() -> list[dict]:
    """Return all queue tasks ordered by status then priority."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_queue
                ORDER BY
                    CASE status
                        WHEN 'running'  THEN 0
                        WHEN 'pending'  THEN 1
                        WHEN 'done'     THEN 2
                        WHEN 'failed'   THEN 3
                        WHEN 'skipped'  THEN 4
                        ELSE 5
                    END,
                    priority DESC,
                    created_at ASC
                LIMIT 500
                """
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            log.warning("DB get_all_queue_tasks error: %s", exc)
            return []
        finally:
            conn.close()


def update_queue_task(
    task_id: int,
    status: str,
    session_id: Optional[str] = None,
) -> None:
    """Update status (and optionally session_id) for a queue task."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            if session_id is not None:
                conn.execute(
                    "UPDATE task_queue SET status=?, session_id=?, updated_at=? WHERE id=?",
                    (status, session_id, now, task_id),
                )
            else:
                conn.execute(
                    "UPDATE task_queue SET status=?, updated_at=? WHERE id=?",
                    (status, now, task_id),
                )
            conn.commit()
        except Exception as exc:
            log.warning("DB update_queue_task error: %s", exc)
        finally:
            conn.close()


def delete_queue_task(task_id: int) -> bool:
    """Delete a task that is not currently running. Returns False if not deletable."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT status FROM task_queue WHERE id=?", (task_id,)
            ).fetchone()
            if not row or row["status"] == "running":
                return False
            conn.execute("DELETE FROM task_queue WHERE id=?", (task_id,))
            conn.commit()
            return True
        except Exception as exc:
            log.warning("DB delete_queue_task error: %s", exc)
            return False
        finally:
            conn.close()


def get_all_agent_usage() -> list[dict]:
    """Aggregate per-agent token usage across ALL sessions (DB only)."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    agent_name,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(output_tokens) AS output_tokens,
                    SUM(cached_tokens) AS cached_tokens,
                    SUM(calls)         AS calls,
                    SUM(cost_usd)      AS cost_usd
                FROM agent_usage
                GROUP BY agent_name
                ORDER BY cost_usd DESC
                """
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["total_tokens"] = d["prompt_tokens"] + d["output_tokens"]
                result.append(d)
            return result
        except Exception as exc:
            log.warning("DB get_all_agent_usage error: %s", exc)
            return []
        finally:
            conn.close()
