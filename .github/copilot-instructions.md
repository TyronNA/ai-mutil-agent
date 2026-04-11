# AI Multi-Agent Project Guidelines

Detailed architecture, pipeline descriptions, and design decisions are in [CLAUDE.md](../CLAUDE.md). This file covers what AI agents need to be immediately productive.

## Build & Test

```bash
make install           # Create venv, install deps, Playwright Chromium
make test              # Run pytest
make lint              # Python syntax check
make web               # FastAPI :8000 + Next.js :3000
make run TASK="..."    # Run Expo pipeline
make clean             # Remove venv + caches
```

Run a single test: `pytest tests/test_orchestrator.py::test_name -v`

## Environment Setup

- Copy `.env.example` → `.env`; set `GITHUB_TOKEN`, `EXPO_PROJECT_DIR`, `GAME_PROJECT_DIR`
- Place Vertex AI service account key at `config/vertex-ai.json` (not committed)
- CLI available after `source .venv/bin/activate`: `agent run "..."`, `agent game "..."`, `agent serve`

## Architecture

Two independent pipelines share agents, tools, and the LLM layer. See [CLAUDE.md](../CLAUDE.md) for full pipeline descriptions.

| Pipeline | Orchestrator | State | Entry |
|----------|-------------|-------|-------|
| Expo React Native | `src/orchestrator.py` | `src/state.py` | `agent run "..."` |
| Mộng Võ Lâm Game | `src/orchestrator_game.py` | `src/state_game.py` | `agent game "..."` |

Key directories:
- `src/agents/` — all agent implementations (each inherits `BaseAgent` from `agents/base.py`)
- `src/llm/` — Vertex AI client, retry, context caching
- `src/tools/` — file I/O, git, browser, notify utilities
- `src/web/` — FastAPI + WebSocket server
- `tests/` — pytest suite with mocked LLM/filesystem

## Web UI Navigation

- Main dashboard views in `ui/app/page.tsx`: `pipeline`, `tasks`, `queue`, `analytics`, `preview`
- Desktop uses a top tab bar with all main views
- Mobile uses a bottom navigation bar with all main views, including `preview`
- Mobile `pipeline` view uses sub-tabs: `form` and `feed`

## Conventions

**Agent design:** Every agent inherits `BaseAgent` and implements `run(state) → state`. Use `state.log(msg, agent="AgentName")` for progress (fires WebSocket callbacks). Use `self._call()` for text, `self._call_json(schema=MyModel)` for structured output.

**LLM calls:**
- Default: `gemini-3-flash-preview` (fast, all routine tasks)
- Planning/review: pass `pro=True` → `gemini-3-pro-preview`
- Deep reasoning: `thinking_budget=4096` for TechExpert planning; `thinking_budget=1024` for QA (rule-checking only); `thinking_budget=0` for TechExpert review (reads diff, no reasoning needed)
- Static context reuse: `create_context_cache(content)` — stored in `subtask.code_cache_name`, deleted after subtask loop

**File writes:** Dev outputs `{"patches": [{"file", "find", "replace"}], "new_files": {...}}` — patches applied server-side in `DevAgent._apply_patches()`. QA receives a unified diff (original → patched) instead of full file content. `subtask.original_files` captures pre-write state; `subtask.written_files` holds final content. Subtasks assigned non-overlapping files for safe parallelization.

## Game Pipeline Invariants

QAAgent enforces these — DevAgent must never violate them:
- `CombatEngine.js` — pure JavaScript, zero Phaser imports
- Colors only via `UI_THEME` from `constants.js` — no bare hex literals like `0x0000ff`
- `SaveManager`: always `load()` → modify → `save()` — never access `localStorage` directly
- Text rendered with `crispText()`, scene transitions via `gotoScene()`
- Vietnamese text must include full diacritics (`'Chọn'` not `'Chon'`)
- Combat formula: `final = rawDmg * (DEF_K / (DEF_K + DEF)) * crit`
