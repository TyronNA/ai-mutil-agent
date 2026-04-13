# AI Multi-Agent Project Guidelines

Detailed architecture, pipeline descriptions, and design decisions are in [CLAUDE.md](../CLAUDE.md). This file covers what AI agents need to be immediately productive.

## Repo Role (Important)

This repository is the AI orchestrator/runtime. It is used to build or modify a separate game source repository pointed to by `GAME_PROJECT_DIR`.
Treat code under `src/` as the control plane, not the target game implementation.

## Build & Test

```bash
make install           # Create venv and install Python deps
make test              # Run pytest
make lint              # Python syntax check
make web               # FastAPI :8000 + Next.js :3000
make web-reload        # FastAPI reload mode + Next.js :3000
make game TASK="..."   # Run game pipeline
make game-no-git TASK="..." # Run game pipeline without git/PR
make clean             # Remove venv + caches
```

Run a single test: `pytest tests/test_orchestrator.py::test_name -v`

## Environment Setup

- Copy `.env.example` → `.env`; set `GITHUB_TOKEN`, `GAME_PROJECT_DIR`
- Place Vertex AI service account key at `config/vertex-ai.json` (not committed)
- CLI available after `source .venv/bin/activate`: `agent game "..."`, `agent serve`

## Documentation Map

- `README.md` — setup, run modes, and day-to-day usage
- `CLAUDE.md` — full architecture, pipeline internals, and design rationale
- `config/game-lessons.md` — accumulated game QA/dev lessons and constraints

Prefer linking to these files in prompts/reviews instead of duplicating long explanations.

## Architecture

Game pipeline with shared agents, tools, and LLM layer. See [CLAUDE.md](../CLAUDE.md) for full pipeline details.

| Pipeline | Orchestrator | State | Entry |
|----------|-------------|-------|-------|
| Mộng Võ Lâm Game | `src/orchestrator_game.py` | `src/state_game.py` | `agent game "..."` |

Key directories:
- `src/agents/` — all agent implementations (each inherits `BaseAgent` from `agents/base.py`)
- `src/llm/` — Vertex AI client, retry, context caching
- `src/tools/` — file I/O, git, browser, notify utilities
- `src/web/` — FastAPI + WebSocket server
- `tests/` — pytest suite with mocked LLM/filesystem

## Repository Structure Map

```text
.
├── .github/
│   └── copilot-instructions.md
├── config/
│   ├── game-lessons.md
│   └── vertex-ai.json
├── prompt/
│   └── mate/
│       ├── base.md
│       ├── soul.md
│       ├── memory.md
│       └── EVOLUTION.md
├── src/
│   ├── agents/
│   ├── context/
│   ├── llm/
│   ├── tools/
│   ├── web/
│   ├── orchestrator_game.py
│   ├── state_game.py
│   └── main.py
├── tests/
└── ui/
```

## Web UI Navigation

- Main dashboard views in `ui/app/page.tsx`: `pipeline`, `tasks`, `queue`, `analytics`, `preview`
- Desktop uses a top tab bar with all main views
- Mobile uses a bottom navigation bar with all main views, including `preview`
- Mobile `pipeline` view uses sub-tabs: `form` and `feed`

## Conventions

**Agent design:** Every agent inherits `BaseAgent` and implements `run(state) → state`. Use `state.log(msg, agent="AgentName")` for progress (fires WebSocket callbacks). Use `self._call()` for text, `self._call_json(schema=MyModel)` for structured output.

**LLM calls:**
- Default: `gemini-3-flash-preview` (fast, all routine tasks)
- Planning: pass `pro=True` → `gemini-2.5-pro` (review remains on Flash)
- Deep reasoning: `thinking_budget=4096` for TechExpert planning by default (`8192` when pro planning mode is enabled); `thinking_budget=1024` for QA (rule-checking only); `thinking_budget=1024` for TechExpert review
- Static context reuse: `create_context_cache(content)` — stored in `subtask.code_cache_name`, deleted after subtask loop

**File writes:** Dev outputs `{"patches": [{"file", "find", "replace"}], "new_files": {...}}` — patches applied server-side in `DevAgent._apply_patches()`. QA receives a unified diff (original → patched) instead of full file content. `subtask.original_files` captures pre-write state; `subtask.written_files` holds final content. Subtasks assigned non-overlapping files for safe parallelization.

## Game Pipeline Invariants

QAAgent enforces these — DevAgent must never violate them:
- All TypeScript types from `src/types/game.ts` — never define duplicate interfaces ad-hoc
- Zustand store (`useGameStore`) for ALL shared game state — never `useState` for collection/team/gold
- Tailwind design tokens only: `panel`, `header`, `gold`, `gold-dim`, `label`, `sub`, `dim`, `ok`, `warn`, `tier.*` — no arbitrary hex colors or inline styles
- Component hierarchy: atoms → molecules → organisms → templates — no circular imports
- All API calls via `src/lib/api/client.ts` — never raw `fetch()` in components
- `GameBridge.getInstance().sendCommand()` + `onGameEvent()` — never raw `postMessage` calls
- Next.js App Router routing: `useRouter()` / `redirect()` — never `window.location.href`
- Vietnamese text must include full diacritics (`'Chọn'` not `'Chon'`)
- Combat formula: `final = rawDmg * (DEF_K / (DEF_K + DEF)) * crit`

## Common Pitfalls

- Do not rely on outdated docs for commands; `Makefile` is the source of truth.
- Missing `config/vertex-ai.json` or required `.env` keys will fail pipelines early.
- For web mode, backend reuses existing `:8000` if already running; `make web` installs UI npm deps before `npm run dev`.

## Code Change Quality Bar (Always Apply)

- Prefer semantic edits over brittle text replacement whenever possible. For TypeScript/TSX changes, target symbols/functions/contracts first; use raw find/replace only as fallback.
- Keep scope minimal: only modify files and code blocks required by the current subtask.
- Preserve public behavior unless the task explicitly requests a behavior change.
- Verify in this order before considering a task complete: `npm run lint` (target repo) → `npm run build` (target repo) → orchestrator tests (`make test`) when orchestrator logic changed.
- If a patch fails to apply cleanly, stop broad rewrites and retry with narrower, context-aware edits.
- Never bypass architecture invariants to satisfy short-term output; correctness and maintainability are priority over speed.

## AI Customization Coverage (Current)

- Present: workspace-wide instruction file (`.github/copilot-instructions.md`)
- Not present yet: `.github/skills/`, `.github/prompts/`, `.github/agents/`, `.github/instructions/`

If task-specific slash workflows are needed, add dedicated skills/prompts/agents under `.github/` with clear descriptions for discovery.

## JS Edit Strategy (AST)

- Current mode: **Hybrid** — text patching first, AST fallback on mismatches for TypeScript/TSX files.
- Supported AST fallback targets: import declarations, function declarations, class declarations, single-variable declarations.
- Planned evolution:
	1. AST-first operation execution for common edits (imports/calls/object fields/scene transitions)
	2. Full codemod pipeline with deterministic transforms and strict verify gates before PR
