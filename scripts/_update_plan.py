"""One-shot script to rewrite the Phase 1/2/3 body of IMPLEMENTATION_PLAN.md."""
import pathlib

PLAN = pathlib.Path("docs/IMPLEMENTATION_PLAN.md")
content = PLAN.read_text()

MARKER = (
    "**Mục tiêu:** Thay thế hardcoded `subtasks list` bằng `Task DAG`"
    " + `worker loop` thật sự.\n"
)

NEW_BODY = r"""
### 1.1 — Tạo `src/task_graph.py`

File mới — không deps ngoài stdlib:

```python
# src/task_graph.py
import uuid, threading
from collections import defaultdict
from dataclasses import dataclass, field

@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""           # "code"|"review"
    description: str = ""
    files_to_touch: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)   # task IDs
    status: str = "pending"  # pending|in_progress|done|failed
    claimed_by: str = ""
    result: str = ""
    review_feedback: str = ""
    retry_count: int = 0
    db_id: str = ""

class DependencyGraph:
    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._dependents: dict[str, set[str]] = defaultdict(set)
        self._in_degree: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def add(self, task: Task):
        with self._lock:
            self._tasks[task.id] = task
            for dep_id in task.blocked_by:
                self._dependents[dep_id].add(task.id)
                self._in_degree[task.id] += 1

    def ready(self) -> list[Task]:
        with self._lock:
            return [
                t for t in self._tasks.values()
                if t.status == "pending" and self._in_degree[t.id] == 0
            ]

    def complete(self, task_id: str, result: str) -> list[Task]:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "done"
            task.result = result
            newly_ready = []
            for dep_id in self._dependents[task_id]:
                self._in_degree[dep_id] -= 1
                if self._in_degree[dep_id] == 0:
                    newly_ready.append(self._tasks[dep_id])
            return newly_ready

    def fail(self, task_id: str, reason: str):
        with self._lock:
            self._tasks[task_id].status = "failed"
            self._tasks[task_id].result = reason
```

**File tạo mới:** `src/task_graph.py`

---

### 1.2 — Nâng cấp `src/state.py`

Thay `subtasks: list[Subtask]` bằng `graph: DependencyGraph`:

```python
from src.task_graph import DependencyGraph
# trong AgentState:
graph: DependencyGraph = field(default_factory=DependencyGraph)
```

**File thay đổi:** `src/state.py`

---

### 1.3 — Tạo `src/task_queue.py` (SQLite persistence)

```sql
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, task TEXT, status TEXT DEFAULT 'running',
    branch TEXT, pr_url TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, run_id TEXT, type TEXT, description TEXT,
    files TEXT, blocked_by TEXT,
    status TEXT DEFAULT 'pending', claimed_by TEXT,
    result TEXT, retry_count INT DEFAULT 0,
    created_at TEXT, updated_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
```

Functions: `init_db()`, `create_run()`, `update_run()`, `create_task()`, `update_task()`, `get_run_history()`.

**File tạo mới:** `src/task_queue.py`

---

### 1.4 — Agent Worker Loop trong `src/agents/base.py`

```python
import queue as Q, threading
from src.task_graph import Task

class BaseAgent(ABC):
    name: str = "base"
    handles: tuple[str, ...] = ()

    def worker_loop(self, state, task_queue: Q.Queue,
                    done_cb, fail_cb, stop_event: threading.Event,
                    max_revisions: int = 3):
        while not stop_event.is_set():
            try:
                task: Task = task_queue.get(timeout=0.5)
            except Q.Empty:
                continue
            if task.type not in self.handles:
                task_queue.put(task)
                continue
            task.status = "in_progress"
            task.claimed_by = self.name
            try:
                result = self.run(state, task=task, max_revisions=max_revisions)
                done_cb(task.id, result)
            except Exception as e:
                fail_cb(task.id, str(e))
```

**File thay đổi:** `src/agents/base.py`

---

### 1.5 — Nâng cấp `src/orchestrator.py` — `_run_task_graph()`

Thay `_run_subtasks_parallel` bằng `_run_task_graph`:

```python
import queue as Q, threading, time

def _run_task_graph(self, state, max_revisions: int):
    task_queue = Q.Queue()
    stop_event = threading.Event()

    def done_cb(task_id: str, result: str):
        newly_ready = state.graph.complete(task_id, result)
        for t in newly_ready:
            task_queue.put(t)
        update_task(task_id, status="done", result=result)

    def fail_cb(task_id: str, reason: str):
        task = state.graph._tasks[task_id]
        if task.retry_count < 2:
            task.retry_count += 1
            task.status = "pending"
            task_queue.put(task)
        else:
            state.graph.fail(task_id, reason)
            update_task(task_id, status="failed", result=reason)

    for t in state.graph.ready():
        task_queue.put(t)

    workers = [CoderAgent(), CoderAgent(), ReviewerAgent()]
    threads = [
        threading.Thread(
            target=w.worker_loop,
            args=(state, task_queue, done_cb, fail_cb, stop_event, max_revisions),
            daemon=True,
        )
        for w in workers
    ]
    for t in threads:
        t.start()

    while any(t.status in ("pending", "in_progress") for t in state.graph._tasks.values()):
        time.sleep(0.2)

    stop_event.set()
    for t in threads:
        t.join(timeout=5)
```

**File thay đổi:** `src/orchestrator.py`

---

### 1.6 — Planner tạo DAG thay vì list

Planner trả thêm `blocked_by` trong JSON response:

```python
# Planner system_prompt thêm field:
# {"plan_summary":"...", "subtasks":[{"id":1,"description":"...","files_to_touch":[...],"blocked_by":[]}]}

id_map = {}
for s in result["subtasks"]:
    task = Task(
        type="code",
        description=s["description"],
        files_to_touch=s.get("files_to_touch", []),
        blocked_by=[id_map[dep] for dep in s.get("blocked_by", []) if dep in id_map],
    )
    id_map[s["id"]] = task.id
    state.graph.add(task)
```

**File thay đổi:** `src/agents/planner.py`

---

## Phase 2 — Real-time Task Board (Web UI — inspired by GoClaw)

**Mục tiêu:** Hiển thị task DAG realtime — Kanban board 6 cột như GoClaw.

### 2.1 — WebSocket events mới

```json
{"type": "plan_ready", "tasks": [{"id":"...", "description":"...", "blocked_by":[...]}]}
{"type": "task_start",  "id": "...", "description": "...", "claimed_by": "coder"}
{"type": "task_done",   "id": "...", "status": "done", "files": [...]}
{"type": "task_retry",  "id": "...", "retry_count": 1}
{"type": "task_failed", "id": "...", "reason": "..."}
```

**File thay đổi:** `src/orchestrator.py` (emit qua `progress_cb`), `src/web/server.py`

---

### 2.2 — Kanban Board (6 cột, giống GoClaw)

Tab **[Board | Log | History]** trong `index.html`:

```
┌─ sidebar ──────┬─ [Board] [Log] [History] ──────────────────────────┐
│ Pipeline       │  (Tab: Board — 6 Kanban columns)                   │
│  ● Plan        │  ┌pending┬blocked┬in_progress┬review┬done┬failed─┐ │
│  ● Code ×2     │  │  ...  │  ...  │    ...    │ ...  │ ...│  ... │ │
│  ● Review      │  └───────┴───────┴───────────┴──────┴────┴───────┘ │
│ Agents         │  (Tab: Log — log stream)                           │
│  💻 Coder      │  (Tab: History — fetch /history → table)           │
└────────────────┴─────────────────────────────────────────────────────┘
```

Cards di chuyển tự động khi nhận WS events — description, `claimed_by` badge, file count, retry badge.

---

### 2.3 — REST endpoint `/history`

```python
@app.get("/history")
async def get_history(limit: int = 20) -> list:
    from src.task_queue import get_run_history
    return get_run_history(limit)
```

**File thay đổi:** `src/web/server.py`, `src/web/static/index.html`

---

## Phase 3 — CLI history command

```python
@app.command()
def history(limit: int = 10):
    """Show last N agent runs from SQLite."""
    from src.task_queue import get_run_history, init_db
    init_db()
    for r in get_run_history(limit):
        # render as rich Table
        ...
```

**File thay đổi:** `src/main.py`

---

## Thứ tự thực hiện

```
Phase 1 (core — làm trước hết)
  ├── 1.1 src/task_graph.py        ← tạo mới, không deps
  ├── 1.3 src/task_queue.py        ← tạo mới, sqlite3 stdlib
  ├── 1.2 src/state.py             ← swap subtasks → graph
  ├── 1.4 src/agents/base.py       ← thêm worker_loop()
  ├── 1.6 src/agents/planner.py    ← trả về blocked_by trong JSON
  └── 1.5 src/orchestrator.py      ← _run_task_graph() thay _run_subtasks_parallel()

Phase 2 (sau Phase 1)
  ├── WS events trong orchestrator
  ├── src/web/server.py            ← forward events + /history endpoint
  └── src/web/static/index.html    ← Tab Board (6 Kanban cols) + Tab History

Phase 3
  └── src/main.py                  ← history CLI command
```

---

## File thay đổi — Tổng hợp

| File | Action | Phase |
|---|---|---|
| `src/task_graph.py` | **Create** — Task dataclass + DependencyGraph | 1 |
| `src/task_queue.py` | **Create** — SQLite persistence | 1 |
| `src/state.py` | **Upgrade** — swap `subtasks` list → `graph` | 1 |
| `src/agents/base.py` | **Upgrade** — thêm `worker_loop()`, `handles` tuple | 1 |
| `src/agents/planner.py` | **Upgrade** — trả về `blocked_by` trong plan JSON | 1 |
| `src/orchestrator.py` | **Upgrade** — `_run_task_graph()` thay parallel cũ | 1 |
| `src/web/server.py` | **Upgrade** — WS events + `/history` endpoint | 2 |
| `src/web/static/index.html` | **Upgrade** — Tab Board (6 cols) + Tab History | 2 |
| `src/main.py` | **Upgrade** — `history` CLI command | 3 |

**Files KHÔNG thay đổi:**
- `src/llm/__init__.py` — Gemini giữ nguyên
- `src/agents/coder.py` — chỉ thêm `handles = ("code",)`
- `src/agents/reviewer.py` — chỉ thêm `handles = ("review",)`
- `src/tools/` — không thay đổi
- `pyproject.toml` — không thêm dependency

---

## Dependencies thay đổi

Không có — `sqlite3`, `threading`, `queue` đều là stdlib Python.

---

## Checklist triển khai

### Phase 1
- [ ] Tạo `src/task_graph.py` (`Task`, `DependencyGraph`)
- [ ] Tạo `src/task_queue.py` (SQLite `runs` + `tasks` tables)
- [ ] Update `src/state.py` — swap `subtasks` → `graph: DependencyGraph`
- [ ] Update `src/agents/base.py` — thêm `handles`, `worker_loop()`
- [ ] Update `src/agents/coder.py` — thêm `handles = ("code",)`
- [ ] Update `src/agents/reviewer.py` — thêm `handles = ("review",)`
- [ ] Update `src/agents/planner.py` — trả `blocked_by` trong JSON
- [ ] Update `src/orchestrator.py` — `_run_task_graph()` thay `_run_subtasks_parallel()`
- [ ] Test: chạy task với 3 subtasks, verify DAG unblock đúng thứ tự

### Phase 2
- [ ] Thêm `plan_ready` / `task_start` / `task_done` / `task_retry` / `task_failed` vào `progress_cb`
- [ ] Update `server.py` — forward events + thêm `GET /history`
- [ ] Thêm tab [Board | Log | History] vào `index.html`
- [ ] Build Kanban board 6 cột — cards di chuyển theo WS events
- [ ] Build History tab — fetch `/history`, render table
- [ ] Test: mở Web UI, chạy task, xem cards move real-time

### Phase 3
- [ ] Thêm `history` CLI command vào `src/main.py`
- [ ] Test: `python -m src.main history --limit 5`
"""

idx = content.find(MARKER)
assert idx != -1, "MARKER not found"

new_content = content[: idx + len(MARKER)] + NEW_BODY

PLAN.write_text(new_content)
print(f"Done — {PLAN} written, {len(new_content)} chars, {new_content.count(chr(10))} lines")
