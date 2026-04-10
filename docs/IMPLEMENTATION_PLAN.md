# Implementation Plan — True Multi-Agent System

> **LLM:** Vertex AI Gemini (giữ nguyên).  
> **Pattern:** Worker Pool + ThreadPoolExecutor + DependencyGraph DAG (Hybrid CrewAI + GoClaw).  
> **Không thêm dependency ngoài** — `sqlite3`, `threading`, `queue` đều stdlib.

---

## Trạng thái hiện tại (2026-04-10)

### Đã hoàn thành

| Component | Status | Notes |
|---|---|---|
| LLM layer (`src/llm/__init__.py`) | ✅ Done | Gemini Flash + Pro, context cache, retry, structured output |
| Expo Pipeline (`src/orchestrator.py`) | ✅ Done | Analyze → Plan → Code/Review loop → Test → Git/PR |
| AnalyzerAgent (`src/agents/analyzer.py`) | ✅ Done | Reads source, extracts conventions, runs tsc |
| Game Pipeline (`src/orchestrator_game.py`) | ✅ Done | TechExpert → Dev/QA loop → Git/PR |
| Game context cache (`src/context/game_loader.py`) | ✅ Done | Loads full game source, creates Gemini cache |
| Dev/QA/TechExpert agents | ✅ Done | Game-specific agents with Phaser 4 invariants |
| Web server (`src/web/server.py`) | ✅ Done | FastAPI + WebSocket streaming |
| CLI (`src/main.py`) | ✅ Done | `run`, `game`, `serve` commands |

### Chưa làm (upgrade lên true multi-agent)

| Component | Status | Notes |
|---|---|---|
| Task DAG (`src/task_graph.py`) | ❌ Pending | `Task` dataclass + `DependencyGraph` |
| SQLite persistence (`src/task_queue.py`) | ❌ Pending | `runs` + `tasks` tables |
| `AgentState.graph` | ❌ Pending | Swap `subtasks: list[Subtask]` → `graph: DependencyGraph` |
| `BaseAgent.worker_loop()` | ❌ Pending | Agent claims task từ queue, chạy N iterations |
| Planner: `blocked_by` in plan JSON | ❌ Pending | Trả DAG thay vì flat list |
| Orchestrator: `_run_task_graph()` | ❌ Pending | Thay `_run_subtasks_parallel()` |
| Web UI: Kanban board + History tab | ❌ Pending | Phase 2 |
| CLI: `history` command | ❌ Pending | Phase 3 |

---

## Kiến trúc target

```
User (CLI / Web UI)
        ↓
   Orchestrator  ← quản lý Queue + DependencyGraph
        ↓ enqueue ready tasks
   queue.Queue (thread-safe)
        ↓ workers claim
┌─ CoderAgent.worker_loop() ─┐ ┌─ CoderAgent.worker_loop() ─┐
│ claim → run() → done_cb    │ │ claim → run() → done_cb    │  (parallel)
└────────────────────────────┘ └────────────────────────────┘
┌─ ReviewerAgent.worker_loop() ─┐
│ (wait blocked_by resolve)     │  (auto-dispatch khi code done)
└───────────────────────────────┘
        ↓ all tasks done
   git commit → PR → notify

SQLite tasks.db  ← persist run history
```

---

## Phase 1 — Task DAG + Worker Loop

### 1.1 Tạo `src/task_graph.py`

```python
@dataclass
class Task:
    id: str                     # uuid
    type: str                   # "code" | "review"
    description: str
    files_to_touch: list[str]
    blocked_by: list[str]       # task IDs
    status: str                 # pending | in_progress | done | failed
    claimed_by: str
    result: str
    review_feedback: str
    retry_count: int

class DependencyGraph:
    add(task)                   # register task + update in-degree
    ready() -> list[Task]       # tasks with in_degree == 0 and status == pending
    complete(task_id, result) -> list[Task]   # returns newly-unblocked tasks
    fail(task_id, reason)
```

### 1.2 Tạo `src/task_queue.py` (SQLite)

Hai tables: `runs(id, task, status, branch, pr_url, ...)` và `tasks(id, run_id, type, status, result, ...)`.

Functions: `init_db()`, `create_run()`, `update_run()`, `create_task()`, `update_task()`, `get_run_history(limit)`.

### 1.3 Update `src/state.py`

Thêm `graph: DependencyGraph = field(default_factory=DependencyGraph)` song song với `subtasks` (giữ `subtasks` để Expo pipeline cũ vẫn hoạt động cho đến khi migrate xong).

### 1.4 Update `src/agents/base.py`

```python
class BaseAgent(ABC):
    handles: tuple[str, ...] = ()   # task types agent này xử lý

    def worker_loop(self, state, task_queue, done_cb, fail_cb,
                    stop_event, max_revisions=3):
        while not stop_event.is_set():
            task = task_queue.get(timeout=0.5)    # blocking with timeout
            if task.type not in self.handles:
                task_queue.put(task); continue     # re-queue for other workers
            task.status = "in_progress"
            task.claimed_by = self.name
            try:
                result = self.run(state, task=task, max_revisions=max_revisions)
                done_cb(task.id, result)
            except Exception as e:
                fail_cb(task.id, str(e))
```

### 1.5 Update `src/agents/coder.py` và `src/agents/reviewer.py`

Chỉ thêm `handles = ("code",)` và `handles = ("review",)`.

### 1.6 Update `src/agents/planner.py`

Thêm `blocked_by: list[int]` vào `_SubtaskItem`. Planner được phép khai báo dependencies (task 3 blocked_by [1, 2]). Khi build `DependencyGraph`, map integer ID → uuid.

### 1.7 Update `src/orchestrator.py` — `_run_task_graph()`

```python
def _run_task_graph(self, state, max_revisions):
    q = queue.Queue()
    stop = threading.Event()

    def done_cb(task_id, result):
        newly_ready = state.graph.complete(task_id, result)
        for t in newly_ready: q.put(t)
        update_task(task_id, status="done", result=result)

    def fail_cb(task_id, reason):
        task = state.graph._tasks[task_id]
        if task.retry_count < 2:
            task.retry_count += 1; task.status = "pending"; q.put(task)
        else:
            state.graph.fail(task_id, reason)

    for t in state.graph.ready(): q.put(t)

    workers = [CoderAgent(), CoderAgent(), ReviewerAgent()]
    threads = [Thread(target=w.worker_loop, args=(state, q, done_cb, fail_cb, stop, max_revisions), daemon=True)
               for w in workers]
    for t in threads: t.start()

    while any(t.status in ("pending", "in_progress") for t in state.graph._tasks.values()):
        time.sleep(0.2)

    stop.set()
    for t in threads: t.join(timeout=5)
```

---

## Phase 2 — Real-time Kanban UI

### 2.1 WebSocket events mới (emit qua `state.log` / `progress_cb`)

```json
{"type": "plan_ready",  "tasks": [{"id":"...", "description":"...", "blocked_by":[...]}]}
{"type": "task_start",  "id": "...", "description": "...", "claimed_by": "coder"}
{"type": "task_done",   "id": "...", "status": "done",     "files": [...]}
{"type": "task_retry",  "id": "...", "retry_count": 1}
{"type": "task_failed", "id": "...", "reason": "..."}
```

**Files:** `src/orchestrator.py` (emit events), `src/web/server.py` (forward qua WS)

### 2.2 Kanban board (6 cột) trong Web UI

Tab **[Board | Log | History]**:

```
┌─ sidebar ──────┬─ [Board] [Log] [History] ────────────────────────────┐
│ Pipeline       │  pending │ blocked │ in_progress │ review │ done │ failed │
│  ● Plan        │  card... │ card... │   card...   │ card.. │ ...  │  ...  │
│  ● Code ×2     │─────────────────────────────────────────────────────────│
│  ● Review      │  (Tab: Log — log stream hiện tại)                       │
│  ● History     │  (Tab: History — table from /history)                   │
└────────────────┴─────────────────────────────────────────────────────────┘
```

Cards di chuyển tự động theo WS events. Mỗi card hiển thị: description, `claimed_by` badge, file count, retry count.

### 2.3 REST endpoint `GET /history`

```python
@app.get("/history")
async def get_history(limit: int = 20):
    return get_run_history(limit)
```

---

## Phase 3 — CLI `history` command

```python
@cli.command()
def history(limit: int = typer.Option(10)):
    """Show past agent runs."""
    init_db()
    rows = get_run_history(limit)
    # render as rich Table: id[:8], task[:50], status, branch, pr_url, created_at
```

---

## Thứ tự thực hiện

```
Phase 1 (làm trước)
  1.1  src/task_graph.py          ← tạo mới
  1.2  src/task_queue.py          ← tạo mới (SQLite)
  1.3  src/state.py               ← thêm graph field
  1.4  src/agents/base.py         ← thêm handles + worker_loop()
  1.5  src/agents/coder.py        ← thêm handles = ("code",)
  1.5  src/agents/reviewer.py     ← thêm handles = ("review",)
  1.6  src/agents/planner.py      ← thêm blocked_by trong JSON + build DAG
  1.7  src/orchestrator.py        ← _run_task_graph() thay _run_subtasks_parallel()

Phase 2
  src/orchestrator.py             ← emit task events qua progress_cb
  src/web/server.py               ← forward events + /history endpoint
  ui/ hoặc src/web/static/        ← Tab Board (6 cols) + Tab History

Phase 3
  src/main.py                     ← history CLI command
```

---

## File summary

| File | Action | Phase |
|---|---|---|
| `src/task_graph.py` | Create — `Task` + `DependencyGraph` | 1 |
| `src/task_queue.py` | Create — SQLite persistence | 1 |
| `src/state.py` | Update — thêm `graph: DependencyGraph` | 1 |
| `src/agents/base.py` | Update — `handles`, `worker_loop()` | 1 |
| `src/agents/coder.py` | Update — `handles = ("code",)` | 1 |
| `src/agents/reviewer.py` | Update — `handles = ("review",)` | 1 |
| `src/agents/planner.py` | Update — `blocked_by` trong plan JSON | 1 |
| `src/orchestrator.py` | Update — `_run_task_graph()` + task events | 1–2 |
| `src/web/server.py` | Update — forward events + `/history` | 2 |
| `src/web/static/index.html` | Update — Kanban 6-col + History tab | 2 |
| `src/main.py` | Update — `history` CLI command | 3 |

**Không thay đổi:** `src/llm/__init__.py`, `src/orchestrator_game.py`, `src/context/`, `src/tools/`, `pyproject.toml`

---

## Checklist

### Phase 1
- [ ] Tạo `src/task_graph.py` (`Task`, `DependencyGraph`)
- [ ] Tạo `src/task_queue.py` (SQLite `runs` + `tasks` tables)
- [ ] Update `src/state.py` — thêm `graph: DependencyGraph`
- [ ] Update `src/agents/base.py` — thêm `handles`, `worker_loop()`
- [ ] Update `src/agents/coder.py` — `handles = ("code",)`
- [ ] Update `src/agents/reviewer.py` — `handles = ("review",)`
- [ ] Update `src/agents/planner.py` — `blocked_by` trong plan JSON + build `DependencyGraph`
- [ ] Update `src/orchestrator.py` — `_run_task_graph()` thay `_run_subtasks_parallel()`
- [ ] Test: 3 subtasks, task 3 blocked_by [1, 2] — verify unblock đúng thứ tự

### Phase 2
- [ ] Emit `plan_ready` / `task_start` / `task_done` / `task_retry` / `task_failed` events
- [ ] `server.py` — forward events qua WS + `GET /history`
- [ ] Web UI — Tab [Board | Log | History]
- [ ] Kanban 6-col — cards di chuyển theo WS events
- [ ] History tab — table from `/history`
- [ ] Test: chạy task, xem cards move real-time trên browser

### Phase 3
- [ ] `src/main.py` — `history` CLI command với rich Table
- [ ] Test: `agent history --limit 5`
