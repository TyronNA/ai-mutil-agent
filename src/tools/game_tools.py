"""game_tools.py — build and lint utilities for the Mộng Võ Lâm Phaser game.

Provides objective (non-LLM) feedback to the QA pipeline:
  - run_js_linter()   — ESLint on written files; Node syntax check as fallback
  - run_game_build()  — Vite/npm build; returns (success, error_log)

Both functions are intentionally side-effect-free on the pipeline state —
they only read the already-written files and report results.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Hard cap on linter/build output returned to the LLM to avoid context bloat
_MAX_OUTPUT_CHARS = 4_000


def run_js_linter(project_dir: str, files: list[str] | None = None) -> str:
    """Run ESLint on the project (or a specific list of relative file paths).

    Falls back to `node --check <file>` (syntax-only) when ESLint is absent.
    Returns a formatted string of issues, or "[linter: no issues found]".

    The output is capped to _MAX_OUTPUT_CHARS to keep LLM prompt size bounded.
    """
    base = Path(project_dir)

    # Resolve target paths — default to entire src/ dir
    targets: list[str]
    if files:
        # Validate paths are inside project; skip missing files
        targets = [
            str(base / f)
            for f in files
            if (base / f).exists() and _is_safe(base, base / f)
        ]
        if not targets:
            return "[linter: no target files found on disk]"
    else:
        targets = [str(base / "src")]

    # ── Try ESLint ────────────────────────────────────────────────────────────
    eslint_bin = base / "node_modules" / ".bin" / "eslint"
    if eslint_bin.exists():
        try:
            result = subprocess.run(
                [str(eslint_bin), "--format=compact", "--no-eslintrc", "--env=browser,es2022", *targets],
                capture_output=True,
                text=True,
                cwd=str(base),
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            if not output or result.returncode == 0:
                return "[linter: no issues found]"
            return _cap(f"[ESLint]\n{output}")
        except subprocess.TimeoutExpired:
            log.warning("ESLint timed out after 30s")
        except Exception as exc:
            log.debug("ESLint failed: %s", exc)

    # ── Fallback: node --check (syntax errors only) ───────────────────────────
    node_bin = shutil.which("node")
    if not node_bin:
        return "[linter: neither ESLint nor node found — skipping]"

    js_files = [t for t in targets if t.endswith((".js", ".mjs", ".cjs"))]
    if not js_files:
        return "[linter: no .js files in target list]"

    issues: list[str] = []
    for filepath in js_files:
        try:
            result = subprocess.run(
                [node_bin, "--check", filepath],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                rel = Path(filepath).relative_to(base)
                issues.append(f"{rel}:\n{(result.stderr or result.stdout).strip()}")
        except subprocess.TimeoutExpired:
            log.warning("node --check timed out for %s", filepath)
        except Exception as exc:
            log.debug("node --check failed for %s: %s", filepath, exc)

    if not issues:
        return "[linter: no syntax errors found]"
    return _cap("[node --check]\n" + "\n\n".join(issues))


def run_game_build(project_dir: str, timeout: int = 120) -> tuple[bool, str]:
    """Run `npm run build` in the game project directory (Vite).

    Returns:
        (True, "[build: success]")              — clean build
        (False, "<capped error output>")        — build failed with errors

    Timeout defaults to 120 seconds; increase for large projects.
    """
    base = Path(project_dir)
    npm_bin = shutil.which("npm")
    if not npm_bin:
        return False, "[build: npm not found — skipping]"

    if not (base / "package.json").exists():
        return False, f"[build: no package.json in {project_dir}]"

    try:
        result = subprocess.run(
            [npm_bin, "run", "build", "--", "--logLevel=error"],
            capture_output=True,
            text=True,
            cwd=str(base),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"[build: timed out after {timeout}s]"
    except Exception as exc:
        return False, f"[build: unexpected error — {exc}]"

    if result.returncode == 0:
        return True, "[build: success]"

    raw = (result.stderr + result.stdout).strip()
    return False, _cap(f"[build failed]\n{raw}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_safe(base: Path, target: Path) -> bool:
    """Return True if *target* is inside *base* (prevents path traversal)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _cap(text: str) -> str:
    """Truncate output to _MAX_OUTPUT_CHARS to protect LLM context window."""
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + f"\n[... truncated at {_MAX_OUTPUT_CHARS} chars]"
