"""Filesystem tools for reading and writing files in the target project."""

from __future__ import annotations

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
    """Write content to a file, creating parent directories as needed. Returns absolute path."""
    # Security: prevent path traversal using proper Path comparison
    base = Path(project_dir).resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"Path traversal attempt blocked: {relative_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


def list_project_files(project_dir: str, max_files: int = 100) -> list[str]:
    """List all relevant files in the project directory."""
    base = Path(project_dir)
    skip_dirs = {"node_modules", ".git", ".expo", "dist", "build", "__pycache__", ".next"}
    result = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and not any(skip in p.parts for skip in skip_dirs):
            result.append(str(p.relative_to(base)))
        if len(result) >= max_files:
            break
    return result


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
