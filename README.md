# AI Multi-Agent Builder (External Codebase Orchestrator)

Production-grade multi-agent system that orchestrates development for an external game repository (configured via GAME_PROJECT_DIR), with planning, implementation, QA, optional git/PR, and a Next.js dashboard.

---

## Quick Start — Docker (Windows / bất kỳ máy nào)

> Cách nhanh nhất: **không cần cài Python, Node hay build gì cả** — chỉ cần Docker.

### Yêu cầu
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows / macOS / Linux)

### 1. Tạo thư mục làm việc

```powershell
mkdir ai-agent && cd ai-agent
```

### 2. Tải file cấu hình

Tải 2 file sau vào thư mục `ai-agent/`:

| File | Tải từ |
|---|---|
| [`docker-compose.yml`](./docker-compose.yml) | repo này |
| [`.env.example`](./.env.example) → đổi tên thành `.env` | repo này |

Cấu trúc cần có:
```
ai-agent/
├── docker-compose.yml
├── .env
└── config/
    └── vertex-ai.json   ← service account key Vertex AI
```

### 3. Điền thông tin vào `.env`

Mở `.env` bằng Notepad hoặc VS Code, điền các giá trị bắt buộc:

```env
GITHUB_TOKEN=ghp_...         # GitHub token (scope: repo)
GITHUB_REPO=owner/repo       # Repo game cần build
GAME_PROJECT_DIR=C:\Projects\game   # Đường dẫn game (Windows path)
WEB_API_KEY=your-secret      # Mật khẩu đăng nhập dashboard
```

> **`GAME_PROJECT_DIR`**: nếu muốn chạy game pipeline từ trong Docker, bỏ comment dòng volume mount game trong `docker-compose.yml`.

### 4. Kéo image và chạy

```powershell
# Kéo cả 2 image mới nhất từ GitHub Container Registry
docker compose pull

# Khởi động (backend :8000 + UI :3001)
docker compose up -d

# Xem log
docker compose logs -f
```

Dashboard mở tại **http://localhost:3001** — đăng nhập bằng `WEB_API_KEY`.
Backend API tại **http://localhost:8000**.

### Cập nhật lên phiên bản mới

```powershell
docker compose pull && docker compose up -d
```

### Dừng / xoá

```powershell
docker compose down        # dừng
docker compose down -v     # dừng + xoá data volumes
```

---

## Quick Start — Dev (build từ source)

### 1. Clone & cài đặt

```bash
git clone https://github.com/phantomla/ai-mutil-agent.git
cd ai-mutil-agent
make install
```

### 2. Configure

```bash
cp .env.example .env
```

Required keys:

| Key | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub token (`repo` scope) for PR creation |
| `GITHUB_REPO` | Format `owner/repo` |
| `GAME_PROJECT_DIR` | Absolute path to local Mộng Võ Lâm game project |
| `WEB_API_KEY` | Login key for web dashboard |

Also place Vertex credentials at `config/vertex-ai.json`.

### 3. Run

CLI:

```bash
# Run game pipeline
make game TASK="Add daily reward popup"

# Run without git/PR creation
make game-no-git TASK="Fix silence debuff not blocking Ultimate"

# Override workers/subtask cap
make game TASK="Refactor combat flow" WORKERS=2 SUBTASKS=3
```

Web UI:

```bash
make web
```

Backend API chạy trên `:8000`, Next.js UI trên `:3001`.

## Architecture

This repository is the orchestrator runtime (agents + prompts + tools + UI).
It does NOT contain the target game source as the main codebase. Instead, it reads/writes files in GAME_PROJECT_DIR.

Single production pipeline (game):

1. `GameLoader` loads static + dynamic game context
2. `TechExpertAgent` plans subtasks and constraints
3. `DevAgent` implements changes
4. `QAAgent` validates against invariants and scenarios
5. `TechExpertAgent` final review
6. Git tooling + `NotifierAgent` finalize output

Main entry points:

- CLI: `agent game`, `agent serve`
- Orchestrator: `src/orchestrator_game.py`
- State: `src/state_game.py`
- Web server: `src/web/server.py`
- Web UI: `ui/`

## Runtime Brain and Prompt Flow

1. Loader builds two context tiers from external game source:
   - static tier (conventions/config) for Gemini cache
   - dynamic tier (classes/scenes) inline for planner
2. TechExpert plans subtasks, constraints, and QA scenarios
3. Dev writes patches/new files against external game repo
4. QA verifies changed code (critical issues block, warnings inform)
5. TechExpert final review and optional fix-up pass
6. Lint gate + optional git/PR + notifier

Prompt assets:

- Agent system prompts are embedded in `src/agents/*.py`
- Chat persona prompt files are in `prompt/mate/` (base, soul, memory)
- Workspace-level Copilot instructions are in `.github/copilot-instructions.md`

## Repository Structure

```text
.
├── .github/
│   ├── copilot-instructions.md      # workspace-wide AI instructions
│   └── workflows/
│       └── docker-publish.yml       # build & push image lên ghcr.io khi push main
├── config/
│   ├── game-lessons.md              # cross-run lessons memory
│   └── vertex-ai.json               # local credential file (not committed)
├── Dockerfile                       # multi-stage build (Node UI + Python backend)
├── docker-compose.yml               # run via pre-built image hoặc build local
├── prompt/
│   └── mate/
│       ├── base.md                  # Mate base persona
│       ├── soul.md                  # Mate adaptive behavior
├── src/
│   ├── agents/                      # TechExpert, Dev, QA, Notifier
│   ├── context/                     # external-source context loader
│   ├── llm/                         # Vertex Gemini client/cache/retry/tokens
│   ├── tools/                       # filesystem, git, search, notify helpers
│   ├── web/                         # FastAPI + WebSocket server
│   ├── orchestrator_game.py         # main game pipeline orchestration
│   ├── state_game.py                # shared pipeline state dataclasses
│   └── main.py                      # CLI entrypoints
├── tests/
│   └── test_orchestrator.py
└── ui/
	├── app/
	└── components/
```

## AI Customization Scope (Current)

- Present: workspace instructions via `.github/copilot-instructions.md`
- Not yet present: `.github/skills/`, `.github/agents/`, `.github/prompts/`, `.github/instructions/`

If you want discoverable slash-command workflows, add dedicated skill/prompt/agent files under `.github/`.

## Make Commands

```bash
make help
make install
make game TASK="..."
make game-no-git TASK="..."
make web
make web-reload
make test
make lint
make clean
```

## Notes

- Legacy Expo pipeline and old static HTML dashboard are removed.
- The web dashboard source of truth is `ui/` (Next.js).

