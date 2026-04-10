# AI Multi-Agent Expo Builder

A production-grade multi-agent system where specialized AI agents collaborate to build and modify Expo React Native apps — with automatic git branching, code review, browser screenshot testing, and GitHub PR creation.

**Stack:** Gemini 3.0 Flash · Custom Orchestrator · Playwright · FastAPI WebSocket · GitHub API

---

## Architecture

```
User (CLI or Web UI)
        ↓
   Orchestrator  ← coordinates all agents
        ↓
① Git Agent      → checkout new branch from main
② Planner Agent  → read codebase, plan subtasks
③ Coder Agent    → implements code, writes files to disk
④ Reviewer Agent → reviews code, requests fixes if needed (loop)
⑤ Tester Agent   → start Expo web, Playwright screenshot
⑥ Git Agent      → commit + push + create GitHub PR (with screenshots)
⑦ Notifier       → macOS desktop notification + optional webhook
        ↓
You receive PR link → review → approve & merge
```

---

## Quick Start

### 1. Install

```bash
git clone <this-repo>
cd ai-mutil-agent

make install
```

This creates a Python venv, installs all dependencies, and installs Playwright Chromium.

---

### 2. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in:

| Key | Where to get it |
|---|---|
| `GITHUB_TOKEN` | [github.com/settings/tokens](https://github.com/settings/tokens) — needs `repo` scope |
| `GITHUB_REPO` | e.g. `your-username/your-expo-app` |
| `EXPO_PROJECT_DIR` | Absolute path to your local Expo project |

---

### 3. Run

#### Option A — CLI

```bash
# Basic: agent reads EXPO_PROJECT_DIR from .env
make run TASK="Add a dark mode toggle to the settings screen"

# With explicit project path
make run TASK="Add a profile screen with avatar" DIR=~/Projects/my-app

# Skip git/PR (just write files)
make run-no-git TASK="Fix login form validation"

# Skip browser screenshot (faster)
make run-no-test TASK="Refactor the home screen"

# More review cycles
make run TASK="Add complex feature" REVISIONS=5
```

#### Option B — Web UI

```bash
make web
# → opens http://localhost:8000
```

Fill in the task form, hit **Run Agents**, and watch the real-time log stream. When done, the PR link appears in the UI and a macOS notification fires.

---

## All Makefile Commands

```
make help          Show all commands
make install       Create venv + install everything
make run           Run pipeline (TASK="..." required)
make run-no-git    Run without git/PR
make run-no-test   Run without browser test
make web           Start Web UI at http://localhost:8000
make web-reload    Start Web UI with auto-reload
make test          Run test suite
make lint          Check for syntax errors
make clean         Remove venv and cache
```

---

## Project Structure

```
src/
├── main.py              # CLI entry point (Typer)
├── orchestrator.py      # Core pipeline coordinator
├── state.py             # AgentState dataclass
├── agents/
│   ├── base.py          # Base class (Gemini calls)
│   ├── planner.py       # Reads codebase → creates subtasks
│   ├── coder.py         # Writes code files to disk
│   ├── reviewer.py      # Reviews code, requests revisions
│   ├── tester.py        # Playwright browser screenshot
│   └── notifier.py      # macOS notification + webhook
├── tools/
│   ├── git.py           # Git checkout, commit, push, PR
│   ├── filesystem.py    # Read/write files in Expo project
│   ├── browser.py       # Start Expo web + take screenshots
│   └── notify.py        # macOS osascript + webhook
├── llm/
│   └── __init__.py      # google-genai SDK wrapper
└── web/
    ├── server.py         # FastAPI + WebSocket server
    └── static/
        └── index.html    # Web UI
```

---

## Extending — Add a New Agent

1. Create `src/agents/my_agent.py` inheriting from `BaseAgent`
2. Define `name`, `system_prompt`, and `run(state, **kwargs)` method
3. Wire it into `src/orchestrator.py` in the correct phase

