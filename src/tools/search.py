"""search.py — code search utility for agent context building.

Uses ripgrep (rg) when available, falls back to Python grep.
Returns formatted snippets suitable for LLM context injection.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".vite", "__pycache__"}
_CODE_EXTS  = {".js", ".ts", ".jsx", ".tsx", ".json", ".md"}


def search_code(
    project_dir: str,
    keyword: str,
    max_results: int = 25,
    context_lines: int = 2,
) -> str:
    """Search for *keyword* in the project and return formatted matches.

    Uses ripgrep when available, pure-Python grep otherwise.

    Returns a string like:
        src/classes/Hero.js:42:  this.level = level;
        src/scenes/BattleScene.js:107:  const level = ...
    or "[no matches]" if nothing found.
    """
    keyword = keyword.strip()
    if not keyword:
        return "[no keyword provided]"

    try:
        return _rg_search(project_dir, keyword, max_results, context_lines)
    except FileNotFoundError:
        log.debug("ripgrep not found, falling back to Python grep")
        return _py_search(project_dir, keyword, max_results)


def _rg_search(project_dir: str, keyword: str, max_results: int, ctx: int) -> str:
    skip_args = []
    for d in _SKIP_DIRS:
        skip_args += ["--glob", f"!{d}/**"]

    cmd = [
        "rg",
        "--fixed-strings",
        "--case-sensitive",
        "--line-number",
        f"--context={ctx}",
        f"--max-count={max_results}",
        "--no-heading",
        "--color=never",
        *skip_args,
        keyword,
        project_dir,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    output = result.stdout.strip()

    if not output:
        # Try case-insensitive fallback
        cmd[cmd.index("--fixed-strings")] = "--ignore-case"
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        output = result.stdout.strip()

    if not output:
        return f"[no matches for '{keyword}']"

    # Strip absolute prefix so paths are relative to project_dir
    prefix = str(project_dir).rstrip("/") + "/"
    lines = [l.replace(prefix, "", 1) for l in output.splitlines()]
    # Cap total output to keep context lean
    capped = lines[:max_results * 4]
    return "\n".join(capped)


def _py_search(project_dir: str, keyword: str, max_results: int) -> str:
    kw_lower = keyword.lower()
    matches: list[str] = []
    root = Path(project_dir)

    for path in root.rglob("*"):
        if path.is_dir() or path.suffix not in _CODE_EXTS:
            continue
        if any(skip in path.parts for skip in _SKIP_DIRS):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if kw_lower in line.lower():
                rel = path.relative_to(root)
                matches.append(f"{rel}:{i}:{line.rstrip()}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return "\n".join(matches) if matches else f"[no matches for '{keyword}']"


def list_definitions(project_dir: str, relative_path: str) -> str:
    """Extract class, function, and const-arrow-function names from a JS/TS file.

    Returns a compact skeleton (name + line number) — a lightweight "repo map"
    that lets TechExpert understand file structure without reading full content.
    Falls back to reading the whole file header if parsing yields nothing.

    Example output:
        src/classes/Hero.js (245 lines)
          L  1  class Hero
          L 18  constructor(id, data)
          L 42  takeDamage(dmg, attacker)
          L 78  const _applyStatMod = (hero, key, val) =>
    """
    target = Path(project_dir) / relative_path
    if not target.exists():
        return f"[file not found: {relative_path}]"

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)

    # Patterns ordered by specificity
    _PATTERNS = [
        # ES6 class declaration
        (re.compile(r"^(?:export\s+(?:default\s+)?)?class\s+(\w+)"), "class {}"),
        # export default function / async function
        (re.compile(r"^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\("), "function {}()"),
        # const/let/var foo = (...) => (arrow fn assigned to identifier)
        (re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("), "const {} = () =>"),
        # class method: two-space/tab indent + name( — not a keyword
        (re.compile(r"^[ \t]{1,4}(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{"), "  method {}()"),
    ]
    _KEYWORDS = {"if", "for", "while", "switch", "catch", "else", "return", "new", "await", "yield"}

    results: list[str] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        for pattern, fmt in _PATTERNS:
            m = pattern.match(line)
            if m:
                name = m.group(1)
                if name not in _KEYWORDS:
                    results.append(f"  L{i:4d}  {fmt.format(name)}")
                break

    if not results:
        # Fallback: first 20 lines as a header peek
        preview = "\n".join(f"  {l}" for l in lines[:20])
        return f"{relative_path} ({total} lines) — no definitions found\n{preview}"

    header = f"{relative_path} ({total} lines)"
    return header + "\n" + "\n".join(results)


def extract_task_keywords(task: str, max_keywords: int = 5) -> list[str]:
    """Extract likely code-relevant keywords from a free-form task description.

    Pulls camelCase, PascalCase, snake_case, and quoted words
    (e.g. 'SaveManager', 'hp_bar', 'gotoScene').
    Falls back to longest plain words.
    """
    # Quoted strings first — highest signal
    quoted = re.findall(r"['\"]([A-Za-z][A-Za-z0-9_]{2,})['\"]", task)

    # camelCase / PascalCase / snake_case identifiers
    idents = re.findall(r"\b([A-Z][a-zA-Z0-9]{3,}|[a-z][a-zA-Z0-9]{3,}[A-Z][a-zA-Z0-9]*|[a-z_]{4,})\b", task)

    # Vietnamese UI keywords are low-signal for code search — filter long ASCII only
    plain = [w for w in re.findall(r"[A-Za-z]{4,}", task) if w.isascii()]

    seen: set[str] = set()
    result: list[str] = []
    for kw in quoted + idents + plain:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
        if len(result) >= max_keywords:
            break
    return result
