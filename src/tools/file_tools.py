"""Virtual filesystem tools for agents."""

from __future__ import annotations

import os
from pathlib import Path

from src.state import AgentState


def write_files_to_disk(state: AgentState, output_dir: str = "output") -> list[str]:
    """Write all files from state to disk under output_dir. Returns list of written paths."""
    written = []
    for rel_path, content in state.files.items():
        # Prevent path traversal
        safe_path = Path(output_dir) / Path(rel_path).name if ".." in rel_path else Path(output_dir) / rel_path
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        written.append(str(safe_path))
    return written
