"""CLI entry point for the multi-agent system."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator_game import GameOrchestrator

cli = typer.Typer(
    name="agent",
    help="AI Multi-Agent Game Builder — Mộng Võ Lâm (Gemini via Vertex AI)",
    add_completion=False,
)
console = Console()


@cli.command()
def serve(
    host: str = typer.Option(
        None, "--host", help="Host (default: WEB_HOST from .env or 0.0.0.0)"
    ),
    port: int = typer.Option(
        None, "--port", "-p", help="Port (default: WEB_PORT from .env or 8000)"
    ),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
) -> None:
    """Start the web UI server (FastAPI + WebSocket)."""
    import uvicorn

    h = host or os.environ.get("WEB_HOST", "0.0.0.0")
    p = port or int(os.environ.get("WEB_PORT", "8000"))
    console.print(f"[cyan]Starting web UI at http://{h}:{p}[/cyan]")
    uvicorn.run(
        "src.web.server:app",
        host=h,
        port=p,
        reload=reload,
        log_level="info",
    )


@cli.command()
def game(
    task: str = typer.Argument(..., help="What to build or fix in the game"),
    project_dir: Optional[str] = typer.Option(
        None, "--dir", "-d",
        help="Path to Mộng Võ Lâm project (default: GAME_PROJECT_DIR from .env)",
    ),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git checkout + PR"),
    revisions: int = typer.Option(3, "--revisions", "-r", help="Max QA revision cycles per subtask"),
    workers: int = typer.Option(3, "--workers", "-w", help="Max parallel subtask workers"),
    max_subtasks: int = typer.Option(5, "--max-subtasks", "-s", help="Max subtasks TechExpert may create (1 = single atomic task)"),
    enqueue: bool = typer.Option(False, "--enqueue-suggestions", help="Auto-add QA out-of-scope suggestions to queue"),
) -> None:
    """
    Run the Game Agent pipeline on Mộng Võ Lâm.

    3-agent system: TechExpert (Gemini Pro) → Dev → QA (parallel, up to --workers subtasks).

    Examples:\n
        agent game "Add daily reward popup with gold/stamina rewards"\n
        agent game "Fix silence debuff not blocking Ultimate" --dir ~/Projects/game-ai/mong-vo-lam\n
        agent game "Implement VFXManager slash and impact effects" --workers 2 --no-git
    """
    from src.orchestrator_game import GameOrchestrator

    _creds = Path(__file__).parent.parent / "config" / "vertex-ai.json"
    if not _creds.exists():
        console.print(f"[red]Vertex AI credentials not found: {_creds}[/red]")
        raise typer.Exit(1)

    target_dir = project_dir or os.environ.get("GAME_PROJECT_DIR", "")
    if not target_dir:
        console.print(
            "[red]No game project directory. "
            "Use --dir or set GAME_PROJECT_DIR in .env[/red]"
        )
        raise typer.Exit(1)

    target_dir = str(Path(target_dir).expanduser().resolve())
    if not Path(target_dir).exists():
        console.print(f"[red]Game project directory not found: {target_dir}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]🎮 Game Agent — task:[/bold cyan] {task}")
    console.print(f"[dim]Project: {target_dir}[/dim]")

    orchestrator = GameOrchestrator()
    orchestrator.run(
        task=task,
        game_project_dir=target_dir,
        git_enabled=not no_git,
        max_revisions=revisions,
        max_workers=workers,
        max_subtasks=max_subtasks,
        enqueue_suggestions=enqueue,
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

