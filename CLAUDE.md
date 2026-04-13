# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install           # Create venv, install deps, install Playwright Chromium
make game TASK="..."   # Run game pipeline (requires GAME_PROJECT_DIR in .env)
make game-no-git       # Run game pipeline without git commit/PR creation
make web               # FastAPI :8000 + Next.js dashboard :3001
make web-reload        # FastAPI reload mode + Next.js dashboard :3001
# In separate terminal: cd $GAME_PROJECT_DIR && npm run dev  → game at :3000
make test              # Run pytest
make lint              # Python syntax check
make clean             # Remove venv + caches
```

CLI entry point (after `make install`, `source .venv/bin/activate`):
```bash
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
- `GAME_PROJECT_DIR` — path to the Mộng Võ Lâm game repo
- `WEBHOOK_URL` — optional Slack/Discord notification
- `MODEL` — override default `gemini-3-flash-preview`

Vertex AI credentials must be in `config/vertex-ai.json` (service account key, not committed).

## Architecture

This repository is an orchestrator/runtime that builds and modifies an external target repository (Mộng Võ Lâm game) via `GAME_PROJECT_DIR`.
The repository now ships one production pipeline: game automation.

### Game Pipeline (`src/orchestrator_game.py`)
Builds Next.js 16 + TypeScript + React + Tailwind + Zustand features for the Mộng Võ Lâm game.
The Cocos battle engine runs in a separate iframe embedded via `GameView.tsx`; Next.js is the outer shell.
1. **GameLoader** (`src/context/game_loader.py`) — loads game source into static (cached: types, tokens, bridge, atoms) and dynamic (store, api, features, pages) tiers; only static tier goes into Gemini Context Cache
2. **TechExpertAgent** (Gemini Pro) — plans subtasks, test scenarios, and architectural constraints
3. **DevAgent** + **QAAgent** loop — parallel worker loop with revision passes
4. **TechExpertAgent** — final architecture review before commit (Flash + reasoning budget)
5. **Git tooling** + **NotifierAgent** — commit/push/PR + notifications

State is `GameAgentState` (`src/state_game.py`).

### LLM Layer (`src/llm/__init__.py`)
- **Backend**: Vertex AI via `google-genai` SDK with service account auth
- **Models**: `gemini-3-flash-preview` (default, fast) and `gemini-2.5-pro` (used for planning when `pro=True`)
- **Retry**: exponential backoff on 429 and 5xx errors
- **Context Cache**: `create_context_cache(content)` caches static context (game source, codebase conventions) for reuse across multiple calls within a subtask. Falls back to full prompt if content is below the 32K token threshold.
- **Thinking tokens**: `thinking_budget=4096` for TechExpert planning by default (8192 when Pro planning mode is enabled), `thinking_budget=1024` for QA static analysis, and `thinking_budget=1024` for TechExpert final review
- **Structured output**: pass a Pydantic model as `response_schema` for JSON mode

## Repository Structure

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
│       └── 
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

## Prompt and Brain Assets

- Pipeline agent prompts live in source code:
	- `src/agents/tech_expert.py`
	- `src/agents/dev.py`
	- `src/agents/qa.py`
- Mate chat persona prompt composition uses:
	- `prompt/mate/base.md`
	- `prompt/mate/soul.md`
	- `prompt/mate/memory.md`
- Workspace instruction layer for Copilot:
	- `.github/copilot-instructions.md`

Current workspace customization coverage is instruction-only (no dedicated `.github/skills`, `.github/prompts`, `.github/agents`, `.github/instructions` yet).

### Web Server (`src/web/server.py`)
FastAPI app with:
- `POST /run` — starts pipeline in a background thread, returns `session_id`
- `GET /ws/{session_id}` — WebSocket that streams real-time agent progress via `asyncio.Queue`
- `GET /agents` — agent metadata (name, icon, role, description)
- Serves Next.js export from `ui/out/` when available

### Web UI Navigation (`ui/app/page.tsx`)
- Main views: `pipeline`, `tasks`, `queue`, `analytics`, `preview`
- Desktop top tabs include all five main views above
- Mobile bottom navigation includes all five main views above (including `preview`)
- Mobile `pipeline` view has sub-tabs: `form` and `feed`

### Key Design Decisions
- **Coder always returns diffs file content** — never patches all. Reviewer reads the file from disk after Coder writes it.
- **TechExpert planning — search-first, no full dump**: `_build_plan_prompt` runs keyword code search before including the dynamic context. The 120K dynamic context is only sent as a fallback when search returns no results, saving ~43K tokens on typical tasks.
- **Subtask parallelization** is safe because the Planner is instructed to assign non-overlapping files per subtask.
- **Convention extraction** comes from GameLoader static context tier (`src/types/game.ts`, `tailwind.config.ts`, `src/lib/game-bridge/index.ts`, atoms), then flows into planner/coder/reviewer prompts.
- **QA receives unified diff** (original → written) instead of full file content. Originals are captured in `subtask.original_files` before first Dev write. This reduces QA prompt from ~10–22K tokens to ~500–2K tokens per subtask.
- **Progress callbacks**: `state.log(msg, agent=name)` appends to `state.messages` and fires an optional `progress_cb` — used by the Web server to push WebSocket updates.

## Code-Editing Best Practices (Default Policy)

- Prefer semantic edits (symbol/function-level intent) over raw string replacement.
- Use smallest safe diff; avoid opportunistic refactors outside subtask scope.
- Treat find/replace patching as transport format, not as reasoning strategy.
- Require objective verification gates before PR path: lint pass, build pass, and relevant tests.
- Escalate model depth on high-risk change sets (combat/save/status/core flow) instead of forcing fast-path edits.
- If patch matching is unstable, retry with context-aware/fuzzy matching; do not rewrite whole files unless strictly necessary.

## AST Migration Roadmap (TypeScript/TSX Target Repo)

Current status: **Level 1 (Hybrid) implemented** in Dev patching.

1. **Level 1 — Hybrid (active)**
	- Keep find/replace for fast-path.
	- On patch mismatch, fallback to AST identity matching (imports/functions/classes/variables) and replace by node range.
	- Code path: `src/agents/dev.py` + `src/tools/js_ast_patch.py`.

2. **Level 2 — AST-first (planned)**
	- Generate semantic edit intents first (e.g., add import, replace call target, add object field).
	- Execute via AST transforms by operation type; fallback to text patch only when unsupported.
	- Add operation-level metrics (success/fallback rate) to session telemetry.

3. **Level 3 — Full codemod pipeline (planned)**
	- Run deterministic codemod stage on target game repo for high-confidence transformations.
	- Enforce verify gates automatically: `npm run lint` → `npm run build` → orchestrator tests.
	- Block PR creation on codemod/apply failures or verification regressions.

## Game Pipeline Invariants (enforced by QAAgent)

These constraints are architectural rules the DevAgent must follow and QAAgent validates:
- All TypeScript types from `src/types/game.ts` — never define duplicate interfaces ad-hoc
- Zustand store (`useGameStore`) for ALL shared game state — never `useState` for collection/team/gold
- Tailwind design tokens only: `panel`, `header`, `gold`, `gold-dim`, `label`, `sub`, `dim`, `ok`, `warn`, `tier.*` — no arbitrary hex colors or inline styles
- Component hierarchy: atoms → molecules → organisms → templates — no circular imports
- All API calls via `src/lib/api/client.ts` — never raw `fetch()` in components
- `GameBridge.getInstance().sendCommand()` + `onGameEvent()` — never raw `postMessage` calls
- Next.js App Router routing: `useRouter()` / `redirect()` — never `window.location.href`
- Vietnamese text must include full diacritics (e.g., `'Chọn'` not `'Chon'`)
- Combat formula: `final = rawDmg * (DEF_K / (DEF_K + DEF)) * crit`
- Status effects: `{type: 'stun'|..., remaining: N}` matching `EffectType` in `src/types/game.ts`
