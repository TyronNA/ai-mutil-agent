"""GameLoader — reads the Mộng Võ Lâm source tree and builds LLM context.

Context is split into two tiers for token efficiency:

**Static tier** (Gemini Context Cache) — near-never changes during a session:
  - CLAUDE.md          (conventions, architecture rules, UI theme)
  - src/constants.js   (UI_THEME, combat tuning values)
  - src/config.js      (scene registry)
  - src/utils/crispText.js, sceneTransition.js
  - docs/PLAN.md       (first 100 lines — architecture notes)
  - src/data/heroes.js (first 60 lines — shape reference)

**Dynamic tier** (inline per-call, never cached) — Dev modifies these:
  - src/classes/CombatEngine.js, Hero.js, StatusProcessor.js, etc.
  - src/scenes/BattleScene.js, src/classes/HeroSprite.js
  - src/data/equipment.js, stages.js

TechExpert gets static cache + dynamic inline during planning.
DevAgent per-subtask cache uses static global cache + specific files only.
QAAgent uses in-memory written_files — no disk re-read needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.llm import create_cache
from src.tools.filesystem import read_file, read_multiple_files, list_project_files

log = logging.getLogger(__name__)

# ── Static tier: cached in Gemini Context Cache ──────────────────────────────
# These files define project conventions and never change mid-session.
# Caching them means TechExpert + DevAgent pay 0 tokens on reads 2+.
_STATIC_FULL_FILES = [
    "CLAUDE.md",
    "src/constants.js",
    "src/config.js",
    "src/utils/crispText.js",
    "src/utils/sceneTransition.js",
]
_STATIC_PREVIEW_FILES = [
    ("docs/PLAN.md",       100),   # roadmap / architecture notes
    ("src/data/heroes.js",  60),   # hero roster shape (avoids huge data dump)
]

# ── Dynamic tier: inline per-call, never cached ───────────────────────────────
# DevAgent will modify these files; caching them would waste tokens recreating
# the cache after every write, or cause agents to read stale cached content.
_DYNAMIC_FULL_FILES = [
    "src/classes/Hero.js",
    "src/classes/StatusProcessor.js",
    "src/classes/PassiveRegistry.js",
    "src/classes/TargetingSystem.js",
    "src/classes/BattleGrid.js",
    "src/classes/GachaSystem.js",
    "src/data/equipment.js",
]
_DYNAMIC_CAPPED_FILES = [
    "src/classes/CombatEngine.js",
    "src/classes/HeroSprite.js",
    "src/classes/SaveManager.js",
    "src/scenes/BattleScene.js",
]
_DYNAMIC_PREVIEW_FILES = [
    ("src/data/stages.js",  80),
]

# Chars per capped file (~600 lines × ~80 chars)
_CAP_CHARS = 48_000
# Budget ceilings
_STATIC_BUDGET  =  40_000   # static tier is intentionally lean
_DYNAMIC_BUDGET = 110_000   # dynamic tier for TechExpert planning


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


def build_static_context(project_dir: str) -> str:
    """Build the **static** context tier — conventions, config, utils.

    This is the only content that goes into the Gemini Context Cache.
    It is intentionally small and stable so the cache stays valid across
    multiple pipeline runs without needing recreation.
    """
    parts: list[str] = []
    total = 0

    for rel in _STATIC_FULL_FILES:
        block = read_multiple_files(project_dir, [rel], max_total=_CAP_CHARS)
        parts.append(block)
        total += len(block)
        if total >= _STATIC_BUDGET:
            break

    if total < _STATIC_BUDGET:
        for rel, max_lines in _STATIC_PREVIEW_FILES:
            block = _read_preview(project_dir, rel, max_lines)
            parts.append(block)
            total += len(block)

    log.info("Static context built — %d sections, ~%d chars", len(parts), total)
    return "\n\n".join(parts)


def build_dynamic_context(project_dir: str) -> str:
    """Build the **dynamic** context tier — classes and scenes Dev may modify.

    This is NEVER cached.  It is injected inline into TechExpert's planning
    prompt so the planner can reason about the actual current code state.
    DevAgent does NOT receive this; it only receives the specific files for
    its subtask via the per-subtask cache.
    """
    parts: list[str] = []
    total = 0

    for rel in _DYNAMIC_FULL_FILES:
        block = read_multiple_files(project_dir, [rel], max_total=_CAP_CHARS)
        parts.append(block)
        total += len(block)
        if total >= _DYNAMIC_BUDGET:
            break

    if total < _DYNAMIC_BUDGET:
        for rel in _DYNAMIC_CAPPED_FILES:
            block = _read_capped(project_dir, rel)
            parts.append(block)
            total += len(block)
            if total >= _DYNAMIC_BUDGET:
                break

    if total < _DYNAMIC_BUDGET:
        for rel, max_lines in _DYNAMIC_PREVIEW_FILES:
            block = _read_preview(project_dir, rel, max_lines)
            parts.append(block)
            total += len(block)

    # File tree always included so TechExpert knows what exists
    file_list = list_project_files(project_dir, max_files=120)
    skip = {"node_modules", ".git", "dist", "build", ".vite"}
    filtered = [f for f in file_list if not any(s in f for s in skip)]
    parts.append("=== PROJECT FILE TREE ===\n" + "\n".join(filtered))

    log.info("Dynamic context built — %d sections, ~%d chars", len(parts), total)
    return "\n\n".join(parts)


def build_game_context(project_dir: str) -> str:
    """Build the full context string (static + dynamic) for backward compatibility.

    Prefer using build_static_context() + build_dynamic_context() separately
    so the static portion can be cached independently.
    """
    static  = build_static_context(project_dir)
    dynamic = build_dynamic_context(project_dir)
    total   = len(static) + len(dynamic)
    log.info("Full game context: static=%d + dynamic=%d = ~%d chars total", len(static), len(dynamic), total)
    return static + "\n\n" + dynamic


def load_game_context(
    project_dir: str,
    use_cache: bool = True,
    cache_ttl: int = 1800,
) -> tuple[str, str, Optional[str]]:
    """Load game context and optionally create a Gemini context cache.

    Only the **static** tier (conventions, config, utils) is sent to the cache.
    The **dynamic** tier (classes, scenes) is returned inline and injected
    into TechExpert's planning prompt only — never cached.

    Returns:
        (static_context, dynamic_context, cache_name)
        - static_context : convention files (what went into the cache)
        - dynamic_context: class/scene files (inline for TechExpert only)
        - cache_name     : Gemini cache resource name, or None if cache skipped
    """
    static_ctx  = build_static_context(project_dir)
    dynamic_ctx = build_dynamic_context(project_dir)

    cache_name: Optional[str] = None
    if use_cache:
        system_hint = (
            "You are working on Mộng Võ Lâm, a Phaser 4 + Vite H5 wuxia card battle RPG. "
            "The following contains the project conventions, UI theme, and configuration. "
            "Use it as your primary reference for all architectural and convention decisions."
        )
        cache_name = create_cache(system_hint, static_ctx, ttl_seconds=cache_ttl)
        if cache_name:
            log.info(
                "Static context cached: %s (~%d chars, ttl=%ds)",
                cache_name, len(static_ctx), cache_ttl,
            )
        else:
            log.info(
                "Static context cache skipped (below token threshold, ~%d chars) — will inline",
                len(static_ctx),
            )

    return static_ctx, dynamic_ctx, cache_name
