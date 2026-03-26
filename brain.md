# Custom Brain cho GoClaw — Orchestrator Python + UI GoClaw

> **Chiến lược:** Giữ nguyên UI/Dashboard/API của GoClaw, thay toàn bộ "brain" (agent logic) bằng Python orchestrator của riêng bạn. Kiểm soát hoàn toàn token, logic, và chi phí.

---

## Tổng quan kiến trúc

```
┌─────────────────────────────────────────┐
│           GoClaw UI (giữ nguyên)        │
│   Dashboard · Task Board · Analytics   │
└────────────────┬────────────────────────┘
                 │ REST API + WebSocket
┌────────────────▼────────────────────────┐
│        Your Orchestrator (Python)       │
│   Nhận task → Quyết định → Chia việc   │
└────────┬───────────────────┬────────────┘
         │                   │
┌────────▼───────┐   ┌───────▼────────┐
│  Agent: Coder  │   │ Agent: Reviewer│
│  ~3-8K token   │   │  ~3-8K token   │
└────────┬───────┘   └───────┬────────┘
         │                   │
┌────────▼───────────────────▼────────────┐
│         Anthropic API trực tiếp         │
│         (không qua GoClaw brain)        │
└─────────────────────────────────────────┘
```

**Lý do:**
- GoClaw mặc định tốn 200K–1M token/request vì inject toàn bộ context
- Tự build orchestrator → kiểm soát token ở mức 3–8K/agent call
- Không cần rebuild UI, Task Board, hay Database

---

## Cấu trúc thư mục

```
your-brain/
├── orchestrator.py          # Brain chính: nhận task, quyết định chia việc
├── agents/
│   ├── base_agent.py        # Base class, quản lý context window
│   ├── coder.py             # Agent viết code
│   └── reviewer.py          # Agent review code
├── task_queue.py            # SQLite queue — lưu task và status
├── goclaw_bridge.py         # Kết nối vào GoClaw REST API để update UI
├── workspace.py             # Quản lý shared folder code
├── config.py                # Config: API keys, endpoints, limits
└── requirements.txt         # Dependencies
```

---

## Bước 1 — Cài đặt

```bash
# Clone hoặc tạo thư mục
mkdir your-brain && cd your-brain

# Tạo virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Cài dependencies
pip install anthropic httpx aiosqlite asyncio python-dotenv
```

**`requirements.txt`:**
```
anthropic>=0.25.0
httpx>=0.27.0
aiosqlite>=0.20.0
python-dotenv>=1.0.0
```

**`.env`:**
```env
ANTHROPIC_API_KEY=sk-ant-...
GOCLAW_BASE_URL=http://localhost:8080
GOCLAW_API_KEY=your-goclaw-api-key
WORKSPACE_PATH=/path/to/your/code/folder
```

---

## Bước 2 — Config

**`config.py`:**
```python
import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GOCLAW_BASE_URL   = os.getenv("GOCLAW_BASE_URL", "http://localhost:8080")
GOCLAW_API_KEY    = os.getenv("GOCLAW_API_KEY")
WORKSPACE_PATH    = os.getenv("WORKSPACE_PATH", "./workspace")

# Token budget — hard cap mỗi agent
MAX_CONTEXT_TOKENS = 20_000   # không bao giờ vượt
MAX_HISTORY_MESSAGES = 5      # chỉ giữ 5 message gần nhất

# Model
MODEL = "claude-sonnet-4-5"
```

---

## Bước 3 — Base Agent (kiểm soát token)

**`agents/base_agent.py`:**
```python
import anthropic
from config import ANTHROPIC_API_KEY, MODEL, MAX_HISTORY_MESSAGES

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

class BaseAgent:
    """
    Base class cho tất cả agents.
    Kiểm soát chặt context window để tránh token bloat.
    """

    def __init__(self, name: str, role: str, skills: str):
        self.name   = name
        self.role   = role
        self.skills = skills

    def build_system_prompt(self) -> str:
        # System prompt gọn — chỉ role + skills, không inject team context thừa
        return f"""You are {self.name}, a specialized AI agent.

Role: {self.role}
Skills: {self.skills}

Rules:
- Focus only on the assigned task
- Be concise and precise
- Output code in markdown code blocks
- If blocked, clearly state WHY you are blocked
"""

    def build_messages(self, task: str, history: list) -> list:
        # Chỉ lấy N message gần nhất — không gửi full history
        recent = history[-MAX_HISTORY_MESSAGES:] if history else []
        return recent + [{"role": "user", "content": task}]

    def run(self, task: str, history: list = None) -> dict:
        """
        Chạy agent với task và trả về kết quả.
        Token thực tế: ~3-8K thay vì 200K+ của GoClaw.
        """
        history = history or []

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=self.build_system_prompt(),
            messages=self.build_messages(task, history)
        )

        return {
            "agent":    self.name,
            "result":   response.content[0].text,
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
```

---

## Bước 4 — Các Agent chuyên biệt

**`agents/coder.py`:**
```python
from agents.base_agent import BaseAgent

class CoderAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Coder",
            role="Senior software engineer. Writes clean, production-ready code.",
            skills="Python, Go, React, TypeScript, REST APIs, database design"
        )
```

**`agents/reviewer.py`:**
```python
from agents.base_agent import BaseAgent

class ReviewerAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="Reviewer",
            role="Code reviewer. Checks correctness, security, and best practices.",
            skills="Code review, security audit, performance optimization, testing"
        )
```

---

## Bước 5 — Task Queue (SQLite)

**`task_queue.py`:**
```python
import aiosqlite
import asyncio
import uuid
from datetime import datetime

DB_PATH = "tasks.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                subject     TEXT NOT NULL,
                description TEXT,
                assignee    TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',
                priority    INTEGER DEFAULT 0,
                result      TEXT,
                progress    INTEGER DEFAULT 0,
                created_at  TEXT,
                updated_at  TEXT
            )
        """)
        await db.commit()

async def create_task(subject: str, assignee: str,
                      description: str = "", priority: int = 0) -> str:
    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO tasks
               (id, subject, description, assignee, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (task_id, subject, description, assignee, priority, now, now)
        )
        await db.commit()
    return task_id

async def update_task(task_id: str, status: str = None,
                      result: str = None, progress: int = None):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            await db.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status, now, task_id)
            )
        if result:
            await db.execute(
                "UPDATE tasks SET result=?, updated_at=? WHERE id=?",
                (result, now, task_id)
            )
        if progress is not None:
            await db.execute(
                "UPDATE tasks SET progress=?, updated_at=? WHERE id=?",
                (progress, now, task_id)
            )
        await db.commit()

async def get_pending_tasks(assignee: str = None) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if assignee:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE status='pending' AND assignee=? ORDER BY priority DESC",
                (assignee,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE status='pending' ORDER BY priority DESC"
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
```

---

## Bước 6 — GoClaw Bridge (Update UI)

**`goclaw_bridge.py`:**
```python
import httpx
from config import GOCLAW_BASE_URL, GOCLAW_API_KEY

class GoClawBridge:
    """
    Kết nối vào GoClaw REST API để cập nhật UI.
    Dữ liệu bạn push lên sẽ hiển thị ngay trên Dashboard.
    """

    def __init__(self):
        self.base = GOCLAW_BASE_URL
        self.headers = {
            "Authorization": f"Bearer {GOCLAW_API_KEY}",
            "Content-Type": "application/json"
        }

    async def push_task(self, team_id: str, subject: str,
                        assignee: str, description: str = "") -> dict:
        """Tạo task trên GoClaw Task Board."""
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.base}/api/teams/{team_id}/tasks",
                json={"subject": subject, "assignee": assignee,
                      "description": description},
                headers=self.headers
            )
            return r.json()

    async def update_progress(self, task_id: str,
                               percent: int, text: str = ""):
        """Update progress bar trên UI."""
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{self.base}/api/tasks/{task_id}/progress",
                json={"percent": percent, "text": text},
                headers=self.headers
            )

    async def complete_task(self, task_id: str, result: str):
        """Đánh dấu task hoàn thành."""
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{self.base}/api/tasks/{task_id}/complete",
                json={"result": result},
                headers=self.headers
            )

    async def log_usage(self, agent: str, input_tokens: int,
                        output_tokens: int):
        """Ghi usage vào GoClaw analytics."""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.base}/api/usage",
                json={"agent": agent, "input_tokens": input_tokens,
                      "output_tokens": output_tokens},
                headers=self.headers
            )
```

---

## Bước 7 — Orchestrator (Brain chính)

**`orchestrator.py`:**
```python
import asyncio
from agents.coder    import CoderAgent
from agents.reviewer import ReviewerAgent
from task_queue      import init_db, create_task, update_task, get_pending_tasks
from goclaw_bridge   import GoClawBridge

bridge   = GoClawBridge()
coder    = CoderAgent()
reviewer = ReviewerAgent()

AGENT_MAP = {
    "coder":    coder,
    "reviewer": reviewer,
}

async def orchestrate(user_request: str, team_id: str):
    """
    Nhận yêu cầu từ user → tự quyết định chia task → chạy agents song song.
    """
    print(f"\n[Orchestrator] Nhận yêu cầu: {user_request}")

    # Bước 1: Quyết định chia task
    # (đơn giản — mở rộng logic này theo nhu cầu)
    tasks = [
        {
            "subject":     f"[CODE] {user_request}",
            "description": "Viết code theo yêu cầu. Output markdown code block.",
            "assignee":    "coder",
            "priority":    10,
        },
        {
            "subject":     f"[REVIEW] {user_request}",
            "description": "Review code được viết. Kiểm tra bug, security, best practices.",
            "assignee":    "reviewer",
            "priority":    5,
        },
    ]

    # Bước 2: Tạo tasks trong DB + push lên GoClaw UI
    task_ids = []
    for t in tasks:
        task_id = await create_task(**t)
        task_ids.append((task_id, t["assignee"]))
        await bridge.push_task(team_id, t["subject"],
                               t["assignee"], t["description"])
        print(f"  → Tạo task: {t['subject']} [{t['assignee']}]")

    # Bước 3: Chạy agents song song
    async def run_agent(task_id: str, assignee: str, task_subject: str):
        agent = AGENT_MAP.get(assignee)
        if not agent:
            return

        await update_task(task_id, status="in_progress", progress=10)
        await bridge.update_progress(task_id, 10, "Bắt đầu...")

        print(f"  [Agent:{assignee}] Đang xử lý...")
        result = agent.run(task_subject)

        await update_task(task_id, status="completed",
                          result=result["result"], progress=100)
        await bridge.complete_task(task_id, result["result"])
        await bridge.log_usage(assignee,
                               result["input_tokens"],
                               result["output_tokens"])

        print(f"  [Agent:{assignee}] Xong! "
              f"({result['input_tokens']} in / {result['output_tokens']} out tokens)")
        return result

    results = await asyncio.gather(*[
        run_agent(tid, assignee, tasks[i]["subject"])
        for i, (tid, assignee) in enumerate(task_ids)
    ])

    # Bước 4: Tổng hợp kết quả
    print("\n[Orchestrator] Hoàn thành tất cả tasks!")
    return results


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    async def main():
        await init_db()
        request = " ".join(sys.argv[1:]) or "Build login API with JWT auth"
        team_id = "your-team-id"   # lấy từ GoClaw Dashboard
        await orchestrate(request, team_id)

    asyncio.run(main())
```

---

## Bước 8 — Workspace (Shared folder)

**`workspace.py`:**
```python
import os
from pathlib import Path
from config import WORKSPACE_PATH

class Workspace:
    """Quản lý shared folder code — tất cả agents đọc/ghi vào đây."""

    def __init__(self):
        self.root = Path(WORKSPACE_PATH)
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, filename: str, content: str):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"  [Workspace] Ghi: {path}")

    def read(self, filename: str) -> str:
        path = self.root / filename
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def list_files(self) -> list:
        return [str(p.relative_to(self.root))
                for p in self.root.rglob("*") if p.is_file()]
```

---

## Cách chạy

```bash
# Kích hoạt môi trường
source venv/bin/activate

# Chạy orchestrator với một yêu cầu
python orchestrator.py "Build OAuth login với Google, backend Python FastAPI"

# Output mẫu:
# [Orchestrator] Nhận yêu cầu: Build OAuth login với Google...
#   → Tạo task: [CODE] Build OAuth login... [coder]
#   → Tạo task: [REVIEW] Build OAuth login... [reviewer]
#   [Agent:coder]    Đang xử lý...
#   [Agent:reviewer] Đang xử lý...
#   [Agent:coder]    Xong! (2840 in / 1205 out tokens)
#   [Agent:reviewer] Xong! (3120 in / 890 out tokens)
# [Orchestrator] Hoàn thành tất cả tasks!
```

---

## So sánh token trước/sau

| | GoClaw mặc định | Your Brain |
|---|---|---|
| Token/request đơn giản | ~200K–1M | ~6K–16K |
| Lý do | Inject full team context mỗi lần | Chỉ gửi task + 5 message gần nhất |
| Chi phí 1000 requests | ~$50–$200 | ~$1–$3 |
| Kiểm soát | ❌ Không | ✅ Hoàn toàn |

---

## Roadmap mở rộng

### Tuần 1 — Core hoạt động
- [x] `base_agent.py` — gọi Anthropic API, hard cap token
- [x] `task_queue.py` — SQLite lưu task
- [x] `orchestrator.py` — chia task cơ bản
- [ ] Test end-to-end với 1 feature request

### Tuần 2 — Multi-agent thông minh hơn
- [ ] Orchestrator dùng LLM để tự quyết định chia task (thay vì hardcode)
- [ ] Task dependency — task B chờ task A xong mới chạy
- [ ] Retry khi agent thất bại

### Tuần 3 — Connect UI đầy đủ
- [ ] `goclaw_bridge.py` — push task/progress lên GoClaw UI
- [ ] Real-time update qua WebSocket
- [ ] Analytics token hiển thị đúng trên Dashboard

### Tuần 4 — Production ready
- [ ] Queue worker riêng (không block main thread)
- [ ] Logging + monitoring
- [ ] Rate limiting để tránh vượt quota Anthropic

---

## Lưu ý quan trọng

**Tắt GoClaw brain, giữ GoClaw UI:**
- Không dùng agent mặc định của GoClaw để xử lý task
- Chỉ dùng GoClaw như một "display layer" — UI, database, REST API
- Orchestrator Python của bạn là brain duy nhất gọi Anthropic API

**Kiểm soát token — nguyên tắc cốt lõi:**
1. System prompt ngắn gọn, chỉ role + skills
2. History tối đa 5 message gần nhất
3. Hard cap `MAX_CONTEXT_TOKENS = 20_000`
4. Không inject file listing, team config, hay workspace tree vào mỗi request

---

*Docs này được tạo dựa trên phân tích GoClaw architecture và best practices xây dựng multi-agent system tiết kiệm token.*