.PHONY: install setup run web test lint clean help

# ── Defaults ────────────────────────────────────────────────────────────────
VENV        := .venv
PYTHON      := $(VENV)/bin/python
PIP         := $(VENV)/bin/pip
AGENT       := $(VENV)/bin/python -m src.main

# ── Help ─────────────────────────────────────────────────────────────────────
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Usage examples:"
	@echo "    make run TASK=\"Add dark mode toggle\""
	@echo "    make run TASK=\"Add dark mode toggle\" DIR=~/Projects/my-app"
	@echo "    make web"

# ── Setup ────────────────────────────────────────────────────────────────────
$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate ## Create venv and install all dependencies
	$(PIP) install --upgrade pip -q
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/playwright install chromium
	@echo ""
	@echo "✅ Install complete. Next: copy .env and fill in your keys:"
	@echo "   cp .env.example .env && open .env"

setup: install ## Alias for install
	@echo ""
	@echo "👉 Edit .env and set:"
	@echo "   GEMINI_API_KEY   — from https://aistudio.google.com/apikey"
	@echo "   GITHUB_TOKEN     — from https://github.com/settings/tokens (repo scope)"
	@echo "   GITHUB_REPO      — e.g. your-username/your-expo-app"
	@echo "   EXPO_PROJECT_DIR — absolute path to your local Expo project"

# ── Run agents ───────────────────────────────────────────────────────────────
TASK    ?= ""
DIR     ?= ""
REVISIONS ?= 3

run: ## Run the agent pipeline (TASK="..." required, DIR="..." optional)
	@if [ -z "$(TASK)" ]; then \
	  echo "❌ TASK is required. Usage: make run TASK=\"Add dark mode\""; exit 1; \
	fi
	$(AGENT) run "$(TASK)" \
	  $(if $(filter-out "",$(DIR)),--dir "$(DIR)") \
	  --revisions $(REVISIONS)

run-no-git: ## Run without git/PR (TASK="..." required)
	@if [ -z "$(TASK)" ]; then \
	  echo "❌ TASK is required. Usage: make run-no-git TASK=\"Add dark mode\""; exit 1; \
	fi
	$(AGENT) run "$(TASK)" \
	  $(if $(filter-out "",$(DIR)),--dir "$(DIR)") \
	  --no-git --revisions $(REVISIONS)

run-no-test: ## Run without browser screenshot (TASK="..." required)
	@if [ -z "$(TASK)" ]; then \
	  echo "❌ TASK is required."; exit 1; \
	fi
	$(AGENT) run "$(TASK)" \
	  $(if $(filter-out "",$(DIR)),--dir "$(DIR)") \
	  --no-test --revisions $(REVISIONS)

# ── Web UI ───────────────────────────────────────────────────────────────────
web: ## Start the Web UI at http://localhost:8000
	@echo "🚀 Starting Web UI at http://localhost:8000 ..."
	$(AGENT) serve

web-reload: ## Start Web UI with auto-reload (development mode)
	$(AGENT) serve --reload

# ── Dev ──────────────────────────────────────────────────────────────────────
test: ## Run all tests
	$(VENV)/bin/pytest tests/ -v

lint: ## Run basic syntax check
	$(PYTHON) -m py_compile src/main.py src/orchestrator.py src/state.py \
	  src/llm/__init__.py src/agents/*.py src/tools/*.py src/web/server.py
	@echo "✅ No syntax errors"

clean: ## Remove venv and cached files
	rm -rf $(VENV) __pycache__ src/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete
	@echo "✅ Cleaned"
