.PHONY: install run run-no-git run-no-test web web-reload test lint clean help

VENV     := .venv
PYTHON   := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
AGENT    := $(VENV)/bin/python -m src.main

TASK      ?= ""
DIR       ?= ""
REVISIONS ?= 3

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate ## Create venv + install deps
	$(PIP) install --upgrade pip -q
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/playwright install chromium

run: ## Run agent pipeline  TASK="..." [DIR="..."] [REVISIONS=3]
	@[ -n "$(TASK)" ] || (echo "❌ TASK is required"; exit 1)
	$(AGENT) run "$(TASK)" $(if $(filter-out "",$(DIR)),--dir "$(DIR)") --revisions $(REVISIONS)

run-no-git: ## Run without git/PR  TASK="..."
	@[ -n "$(TASK)" ] || (echo "❌ TASK is required"; exit 1)
	$(AGENT) run "$(TASK)" $(if $(filter-out "",$(DIR)),--dir "$(DIR)") --no-git --revisions $(REVISIONS)

run-no-test: ## Run without browser test  TASK="..."
	@[ -n "$(TASK)" ] || (echo "❌ TASK is required"; exit 1)
	$(AGENT) run "$(TASK)" $(if $(filter-out "",$(DIR)),--dir "$(DIR)") --no-test --revisions $(REVISIONS)

web: ## Start backend :8000 + Next.js dev :3000 (no build needed)
	cd ui && npm install
	$(AGENT) serve &
	cd ui && npm run dev

web-reload: ## Start backend with auto-reload + Next.js dev :3000
	$(AGENT) serve --reload &
	cd ui && npm run dev

test: ## Run tests
	$(VENV)/bin/pytest tests/ -v

lint: ## Syntax check all Python files
	$(PYTHON) -m py_compile src/main.py src/orchestrator.py src/state.py \
	  src/llm/__init__.py src/agents/*.py src/tools/*.py src/web/server.py
	@echo "✅ OK"

clean: ## Remove venv + cache
	rm -rf $(VENV) __pycache__ src/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete


clean: ## Remove venv + cache
	rm -rf $(VENV) __pycache__ src/__pycache__ .pytest_cache
	find . -name "*.pyc" -delete
