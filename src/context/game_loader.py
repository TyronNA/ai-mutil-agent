"""GameLoader — reads the Mộng Võ Lâm source tree and builds LLM context.

Loads:
  - CLAUDE.md  (conventions, architecture rules, UI theme)
  - docs/PLAN.md (roadmap, current phase, known bugs)
  - src/constants.js (UI_THEME, combat tuning values)
  - src/config.js (scene registry)
  - src/classes/CombatEngine.js (core combat logic — pure JS contract)
  - src/classes/Hero.js, StatusProcessor.js, PassiveRegistry.js, TargetingSystem.js
  - src/classes/SaveManager.js (persistence layer)
  - src/classes/HeroSprite.js (visual layer)
  - src/data/heroes.js (hero roster — first 120 lines)
  - src/data/stages.js (stage data — first 80 lines)
  - src/data/equipment.js

The result is a single context string (≈ 80–120K chars) optionally cached
via Gemini Context Cache so all 3 agents share the same warm cache.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.llm import create_cache
from src.tools.filesystem import read_file, read_multiple_files, list_project_files

log = logging.getLogger(__name__)

# Files loaded in FULL (highest signal for agents)
_FULL_FILES = [
    "CLAUDE.md",
    "src/constants.js",
    "src/config.js",
    "src/classes/Hero.js",
    "src/classes/StatusProcessor.js",
    "src/classes/PassiveRegistry.js",
    "src/classes/TargetingSystem.js",
    "src/classes/BattleGrid.js",
    "src/classes/GachaSystem.js",
    "src/data/equipment.js",
    "src/utils/crispText.js",
    "src/utils/sceneTransition.js",
]

# Large files — loaded but hard-capped at 600 lines to protect context window
_CAPPED_FILES = [
    "src/classes/CombatEngine.js",
    "src/classes/HeroSprite.js",
    "src/classes/SaveManager.js",
    "src/scenes/BattleScene.js",
]

# Data files — first N lines only (enough to see the shape)
_PREVIEW_FILES = [
    ("src/data/heroes.js",  120),
    ("src/data/stages.js",  80),
    ("docs/PLAN.md",        200),
]

# Chars per capped file (~600 lines × ~80 chars = 48K)
_CAP_CHARS = 48_000
# Total budget for context string
_TOTAL_BUDGET = 150_000


def _read_capped(project_dir: str, rel_path: str, cap: int = _CAP_CHARS) -> str:
    content = read_file(project_dir, rel_path, max_chars=cap)
    if not content:
        return f"=== {rel_path} ===\n[not found]"
    return f"=== {rel_path} ===\n{content}"


def _read_preview(project_dir: str, rel_path: str, max_lines: int) -> str:
    target = Path(project_dir) / rel_path
    if not target.exists():
        return f"=== {rel_path} ===\n[not found]"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    preview = "\n".join(lines[:max_lines])
    suffix = f"\n... [{len(lines) - max_lines} more lines omitted]" if len(lines) > max_lines else ""
    return f"=== {rel_path} ===\n{preview}{suffix}"


def build_game_context(project_dir: str) -> str:
    """Build a single context string from the game source tree.

    Returns a formatted string ready to be embedded in an LLM prompt or
    stored in a Gemini context cache.
    """
    parts: list[str] = []
    total = 0

    # ── 1. Full files ────────────────────────────────────────────────────────
    for rel in _FULL_FILES:
        block = read_multiple_files(project_dir, [rel], max_total=_CAP_CHARS)
        parts.append(block)
        total += len(block)
        if total > _TOTAL_BUDGET:
            break

    # ── 2. Capped large files ────────────────────────────────────────────────
    if total < _TOTAL_BUDGET:
        for rel in _CAPPED_FILES:
            block = _read_capped(project_dir, rel)
            parts.append(block)
            total += len(block)
            if total > _TOTAL_BUDGET:
                break

    # ── 3. Preview files (PLAN + data shapes) ───────────────────────────────
    if total < _TOTAL_BUDGET:
        for rel, max_lines in _PREVIEW_FILES:
            block = _read_preview(project_dir, rel, max_lines)
            parts.append(block)
            total += len(block)

    # ── 4. File tree ─────────────────────────────────────────────────────────
    file_list = list_project_files(project_dir, max_files=120)
    skip = {"node_modules", ".git", "dist", "build", ".vite"}
    filtered = [f for f in file_list if not any(s in f for s in skip)]
    tree_block = "=== PROJECT FILE TREE ===\n" + "\n".join(filtered)
    parts.append(tree_block)

    log.info(
        "Game context built — %d sections, ~%d chars total",
        len(parts),
        total,
    )
    return "\n\n".join(parts)


def load_game_context(
    project_dir: str,
    use_cache: bool = True,
    cache_ttl: int = 1800,
) -> tuple[str, Optional[str]]:
    """Load game context and optionally create a Gemini context cache.

    Returns:
        (context_str, cache_name)
        cache_name is None if caching failed or was disabled.
    """
    context = build_game_context(project_dir)

    cache_name: Optional[str] = None
    if use_cache:
        # System instruction for the cache — shared by all 3 agents
        system_hint = (
            "You are working on Mộng Võ Lâm, a Phaser 4 + Vite H5 wuxia card battle RPG. "
            "The following is the full source context of the project. "
            "Use it as your primary reference for all code decisions."
        )
        cache_name = create_cache(system_hint, context, ttl_seconds=cache_ttl)
        if cache_name:
            log.info("Game context cached: %s (ttl=%ds)", cache_name, cache_ttl)
        else:
            log.info("Game context cache skipped (content below min token threshold) — using inline context")

    return context, cache_name
