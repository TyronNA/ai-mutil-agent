"""Cross-run lessons — capture patterns from each pipeline run and inject them
into subsequent runs so agents improve over time (inspired by Hermes' skills system).

Lesson file: config/game-lessons.md (gitignored, local to the machine).

Format on disk:
  ## qa_violations
  - [CRITICAL] src/scenes/BattleScene.js — CombatEngine must not import Phaser (4× in last 10 runs)
  ...

  ## patch_failures
  - src/classes/CombatEngine.js failed 3× (stale context — include more surrounding lines)
  ...

  ## hot_files
  - src/scenes/BattleScene.js (touched most often, high bug rate)
  ...

  ## last_runs
  - 2026-04-11: Add daily reward popup — 2 subtasks, 3 revisions, QA passed — approved
  ...
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.state_game import GameAgentState

log = logging.getLogger(__name__)

_LESSONS_FILE = Path(__file__).parent.parent / "config" / "game-lessons.md"
_MAX_VIOLATIONS = 20
_MAX_PATCH_FAILURES = 10
_MAX_HOT_FILES = 10
_MAX_LAST_RUNS = 15


# ── Load ──────────────────────────────────────────────────────────────────────

def load_lessons() -> str:
    """Return the lessons file content as a string, or '' if not yet created."""
    if not _LESSONS_FILE.exists():
        return ""
    try:
        return _LESSONS_FILE.read_text(encoding="utf-8")
    except OSError as e:
        log.warning("Could not read lessons file: %s", e)
        return ""


# ── Capture ───────────────────────────────────────────────────────────────────

def capture_lessons(state: "GameAgentState") -> None:
    """Parse the completed pipeline state and merge observations into the lessons file."""
    # ── Collect raw observations from this run ───────────────────────────────
    violation_counts: Counter = Counter()   # "FILE — description" → count
    patch_fail_counts: Counter = Counter()  # rel_path → count
    file_touch_counts: Counter = Counter()  # rel_path → count
    total_revisions = 0

    for subtask in state.subtasks:
        # QA violations
        for issue in subtask.qa_issues:
            if issue.get("severity") in ("critical", "warning"):
                key = f"{issue.get('file', '?')} — {issue.get('description', '')[:100]}"
                violation_counts[key] += 1
        # Patch failures
        for rel_path, failed in subtask.patch_failures.items():
            patch_fail_counts[rel_path] += len(failed)
        # File touch frequency
        for f in subtask.files_to_touch:
            file_touch_counts[f] += 1
        # Total revisions across subtasks
        total_revisions += subtask.revision_count

    # ── Load existing lessons ────────────────────────────────────────────────
    existing = _parse_existing(_LESSONS_FILE)

    # ── Merge new counts into existing tallies ───────────────────────────────
    _merge_counter(existing["qa_violations"], violation_counts, _MAX_VIOLATIONS)
    _merge_counter(existing["patch_failures"], patch_fail_counts, _MAX_PATCH_FAILURES)
    _merge_counter(existing["hot_files"], file_touch_counts, _MAX_HOT_FILES)

    # ── Append run summary ───────────────────────────────────────────────────
    date_str = datetime.now().strftime("%Y-%m-%d")
    run_entry = (
        f"- {date_str}: {state.task[:60]} — "
        f"{len(state.subtasks)} subtask(s), {total_revisions} revision(s), "
        f"verdict: {state.review_verdict or 'n/a'}"
    )
    existing["last_runs"].insert(0, run_entry)
    existing["last_runs"] = existing["last_runs"][:_MAX_LAST_RUNS]

    # ── Write back ───────────────────────────────────────────────────────────
    _write_lessons(existing)
    log.info("Lessons updated: %s", _LESSONS_FILE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_existing(path: Path) -> dict:
    """Parse the existing markdown file into sections."""
    sections: dict = {
        "qa_violations": Counter(),
        "patch_failures": Counter(),
        "hot_files": Counter(),
        "last_runs": [],
    }
    if not path.exists():
        return sections

    current_section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## qa_violations"):
            current_section = "qa_violations"
        elif line.startswith("## patch_failures"):
            current_section = "patch_failures"
        elif line.startswith("## hot_files"):
            current_section = "hot_files"
        elif line.startswith("## last_runs"):
            current_section = "last_runs"
        elif line.startswith("- ") and current_section:
            entry = line[2:].strip()
            if current_section == "last_runs":
                sections["last_runs"].append("- " + entry)
            elif " (×" in entry:
                # e.g. "src/foo.js — description (×3)"
                parts = entry.rsplit(" (×", 1)
                key = parts[0].strip()
                try:
                    count = int(parts[1].rstrip(")"))
                except (ValueError, IndexError):
                    count = 1
                sections[current_section][key] = count
    return sections


def _merge_counter(existing: Counter, new: Counter, max_items: int) -> None:
    for key, count in new.items():
        existing[key] += count
    # Trim to top N
    top = existing.most_common(max_items)
    existing.clear()
    existing.update(dict(top))


def _write_lessons(sections: dict) -> None:
    _LESSONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Game Agent Lessons\n"]

    lines.append("## qa_violations\n")
    for key, count in sections["qa_violations"].most_common():
        lines.append(f"- {key} (×{count})")
    lines.append("")

    lines.append("## patch_failures\n")
    for key, count in sections["patch_failures"].most_common():
        lines.append(f"- {key} (×{count})")
    lines.append("")

    lines.append("## hot_files\n")
    for key, count in sections["hot_files"].most_common():
        lines.append(f"- {key} (×{count})")
    lines.append("")

    lines.append("## last_runs\n")
    for entry in sections["last_runs"]:
        lines.append(entry)
    lines.append("")

    _LESSONS_FILE.write_text("\n".join(lines), encoding="utf-8")
