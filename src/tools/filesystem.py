"""Filesystem tools for reading and writing files in the target project."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Max characters per individual file sent to LLM (~40K chars ≈ ~10K tokens)
_MAX_FILE_CHARS = 40_000
# Max total context characters across all files in a single read_multiple_files call
_MAX_TOTAL_CHARS = 120_000


def read_file(project_dir: str, relative_path: str, max_chars: int = _MAX_FILE_CHARS) -> str:
    """Read a file from the project. Returns empty string if not found.
    Truncates to max_chars to protect LLM context window.
    """
    target = Path(project_dir) / relative_path
    if not target.exists():
        return ""
    content = target.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... truncated — file exceeds {max_chars} chars]"
    return content


def write_file(project_dir: str, relative_path: str, content: str) -> str:
    """Write content to a file atomically, creating parent directories as needed. Returns absolute path."""
    # Security: prevent path traversal — resolve symlinks on both sides before comparing
    base = Path(project_dir).resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path traversal attempt blocked: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file then rename so partial writes never leave a corrupt file
    tmp_fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".tmp_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return str(target)


def list_project_files(project_dir: str, max_files: int = 100) -> list[str]:
    """List all relevant files in the project directory."""
    base = Path(project_dir)
    skip_dirs = {
        "node_modules", ".git", ".expo", "dist", "build", "__pycache__", ".next",
        ".vite", ".turbo", ".vercel", ".cache", "coverage", ".nyc_output", ".output",
    }
    result = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not any(skip in p.parts for skip in skip_dirs):
            result.append(str(p.relative_to(base)))
        if len(result) >= max_files:
            break
    return result


def read_file_lines(project_dir: str, relative_path: str, start_line: int, end_line: int) -> str:
    """Read a specific line range from a file. Lines are 1-indexed, inclusive.

    Returns lines with their line numbers prepended — ideal for targeted reads
    when only a section of a large file is needed (saves context tokens).
    Returns an error message if the file is not found.
    """
    target = Path(project_dir) / relative_path
    if not target.exists():
        return f"[file not found: {relative_path}]"
    all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    # Clamp to valid range
    start = max(1, start_line)
    end = min(len(all_lines), end_line)
    if start > end:
        return f"[invalid range {start_line}–{end_line} for {relative_path} ({len(all_lines)} lines total)]"
    selected = all_lines[start - 1 : end]
    numbered = [f"{start + i:5d}: {line}" for i, line in enumerate(selected)]
    return f"=== {relative_path} (lines {start}–{end} of {len(all_lines)}) ===\n" + "\n".join(numbered)


def read_multiple_files(project_dir: str, relative_paths: list[str], max_total: int = _MAX_TOTAL_CHARS) -> str:
    """Read multiple files and return their contents formatted for LLM context.
    Stops adding files once total characters exceed max_total.
    """
    parts = []
    total = 0
    for path in relative_paths:
        content = read_file(project_dir, path)
        if content:
            entry = f"=== {path} ===\n{content}"
        else:
            entry = f"=== {path} ===\n[file not found or empty]"
        total += len(entry)
        parts.append(entry)
        if total >= max_total:
            parts.append(f"[Context budget reached — {len(relative_paths) - len(parts)} file(s) omitted]")
            break
    return "\n\n".join(parts)
