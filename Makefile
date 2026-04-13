.PHONY: install game game-no-git web web-reload test lint check-pro check-pro3 clean help

VENV     := .venv
PYTHON   := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
AGENT    := $(VENV)/bin/python -m src.main

TASK      ?= ""
DIR       ?= ""
REVISIONS ?= 3
WORKERS   ?= 3
SUBTASKS  ?= 5

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate ## Create venv + install deps
	$(PIP) install --upgrade pip -q
	$(PIP) install -e ".[dev]"

game: ## Run game pipeline  TASK="..." [DIR="..."] [REVISIONS=3] [WORKERS=3] [SUBTASKS=5]
	@[ -n "$(TASK)" ] || (echo "❌ TASK is required"; exit 1)
	$(AGENT) game "$(TASK)" $(if $(filter-out "",$(DIR)),--dir "$(DIR)") --revisions $(REVISIONS) --workers $(WORKERS) --max-subtasks $(SUBTASKS)

game-no-git: ## Run game pipeline without git/PR  TASK="..."
	@[ -n "$(TASK)" ] || (echo "❌ TASK is required"; exit 1)
	$(AGENT) game "$(TASK)" $(if $(filter-out "",$(DIR)),--dir "$(DIR)") --no-git --revisions $(REVISIONS) --workers $(WORKERS) --max-subtasks $(SUBTASKS)

web: ## Start backend :8000 + Next.js dashboard :3001 (no build needed)
	cd ui && npm install
	@if lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Backend already running on :8000, reusing existing process"; \
	else \
		$(AGENT) serve & \
	fi
	cd ui && npm run dev

web-reload: ## Start backend with auto-reload + Next.js dashboard :3001
	@if lsof -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Backend already running on :8000, reusing existing process"; \
	else \
		$(AGENT) serve --reload & \
	fi
	cd ui && npm run dev

game-preview: ## Start game dev server at :3000 (GAME_PROJECT_DIR)
	cd "$$(python3 -c "import os; print(os.environ.get('GAME_PROJECT_DIR',''))" 2>/dev/null || echo "${GAME_PROJECT_DIR}")" && npm run dev -- --port 3000

test: ## Run tests
	$(VENV)/bin/pytest tests/ -v

lint: ## Syntax check all Python files
	$(PYTHON) -m py_compile src/main.py src/orchestrator_game.py src/state_game.py src/db.py src/lessons.py \
	  src/context/*.py src/llm/__init__.py src/agents/*.py src/tools/*.py src/web/server.py
	@echo "✅ OK"

clean: ## Remove venv + cache
	rm -rf $(VENV) __pycache__ src/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete

docker: ## Run backend :8000 + UI :3001 in Docker (builds images if not exists)
	docker compose up --build -d