# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install           # Create venv, install deps, install Playwright Chromium
make run TASK="..."    # Run Expo pipeline (requires EXPO_PROJECT_DIR in .env)
make run-no-git        # Run without git commit/PR creation
make run-no-test       # Run without browser screenshot
make web               # Start FastAPI on :8000 + Next.js UI on :3000
make web-reload        # Start web with auto-reload
make test              # Run pytest
make lint              # Python syntax check
make clean             # Remove venv + caches
```

CLI entry point (after `make install`, `source .venv/bin/activate`):
```bash
agent run "Add dark mode toggle"               # Expo pipeline
agent run --dir ~/Projects/my-app "..."        # Explicit project dir
agent game "Add daily reward popup"            # Game pipeline
agent game --workers 2 "..."                   # Override parallel workers
agent serve --port 8000 --reload               # Web UI
```

Run a single test:
```bash
pytest tests/test_orchestrator.py::test_name -v
```

## Environment

Copy `.env.example` to `.env`. Key variables:
- `GITHUB_TOKEN`, `GITHUB_REPO` — for PR creation
- `EXPO_PROJECT_DIR` — path to the target Expo app
- `GAME_PROJECT_DIR` — path to the Mộng Võ Lâm game repo
- `WEBHOOK_URL` — optional Slack/Discord notification
- `MODEL` — override default `gemini-3-flash-preview`

Vertex AI credentials must be in `config/vertex-ai.json` (service account key, not committed).

## Architecture

Two independent pipelines share agents, tools, and the LLM layer:

### Expo Pipeline (`src/orchestrator.py`)
Builds Expo React Native features end-to-end:
1. **AnalyzerAgent** — reads source files, extracts conventions, runs `tsc --noEmit` to detect pre-existing errors
2. **PlannerAgent** — decomposes task into up to 5 ordered subtasks, each targeting specific files
3. **CoderAgent** + **ReviewerAgent** loop — runs in parallel threads (up to 3 workers); Coder writes complete file content (never diffs); Reviewer reads from disk and checks TypeScript errors + conventions
4. **TesterAgent** — non-LLM; starts Expo web server, captures Playwright screenshots
5. **GitAgent** → **NotifierAgent** — commit, push, open GitHub PR, send macOS notification

Shared state is `AgentState` (`src/state.py`) — a single mutable object passed through the pipeline.

### Game Pipeline (`src/orchestrator_game.py`)
Builds Phaser 4 / JavaScript features for the Mộng Võ Lâm game:
1. **GameLoader** (`src/context/game_loader.py`) — loads full game source (~80–120K chars) into a single context string; creates a Gemini context cache
2. **TechExpertAgent** (Gemini Pro) — plans subtasks, test scenarios, and architectural constraints
3. **DevAgent** + **QAAgent** loop — same parallel pattern as Expo pipeline
4. **TechExpertAgent** — final architecture review before commit
5. **GitAgent** → **NotifierAgent** — same as Expo

State is `GameAgentState` (`src/state_game.py`).

### LLM Layer (`src/llm/__init__.py`)
- **Backend**: Vertex AI via `google-genai` SDK with service account auth
- **Models**: `gemini-3-flash-preview` (default, fast) and `gemini-3-pro-preview` (planning/review, via `pro=True`)
- **Retry**: exponential backoff on 429 and 5xx errors
- **Context Cache**: `create_context_cache(content)` caches static context (game source, codebase conventions) for reuse across multiple calls within a subtask. Falls back to full prompt if content is below the 32K token threshold.
- **Thinking tokens**: pass `thinking_budget=8192` for deeper reasoning (used by Planner, Reviewer, TechExpert)
- **Structured output**: pass a Pydantic model as `response_schema` for JSON mode

### Web Server (`src/web/server.py`)
FastAPI app with:
- `POST /run` — starts pipeline in a background thread, returns `session_id`
- `GET /ws/{session_id}` — WebSocket that streams real-time agent progress via `asyncio.Queue`
- `GET /agents` — agent metadata (name, icon, role, description)
- Serves Next.js build from `ui/out/` or falls back to `src/web/static/`

### Key Design Decisions
- **Coder always returns complete file content** — never patches or diffs. Reviewer reads the file from disk after Coder writes it.
- **Subtask parallelization** is safe because the Planner is instructed to assign non-overlapping files per subtask.
- **Convention extraction** is first-class: AnalyzerAgent output flows into every downstream agent's prompt to enforce project-specific patterns.
- **Cache naming**: subtask cache stored in `subtask.code_cache_name`; explicitly deleted after the subtask loop completes.
- **Progress callbacks**: `state.log(msg, agent=name)` appends to `state.messages` and fires an optional `progress_cb` — used by the Web server to push WebSocket updates.

## Game Pipeline Invariants (enforced by QAAgent)

These constraints are architectural rules the DevAgent must follow and QAAgent validates:
- `CombatEngine.js` — pure JavaScript only, zero Phaser imports ever
- Colors via `UI_THEME` from `constants.js` — no bare hex literals like `0x0000ff`
- `SaveManager`: always `load()` → modify → `save()` — never access `localStorage` directly
- All text rendered with `crispText()`, all scene transitions via `gotoScene()`
- Vietnamese text must include full diacritics (e.g., `'Chọn'` not `'Chon'`)
- Combat formula: `final = rawDmg * (DEF_K / (DEF_K + DEF)) * crit`
- Status effects: `{type: 'stun'|..., remaining: N}`
