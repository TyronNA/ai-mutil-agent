"""CLI entry point for the multi-agent Expo builder."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator import Orchestrator

cli = typer.Typer(
    name="agent",
    help="AI Multi-Agent Expo Builder — Gemini 3.0 Flash + Git + Browser Testing",
    add_completion=False,
)
console = Console()


@cli.command()
def run(
    task: str = typer.Argument(..., help="What to build or change in the Expo app"),
    project_dir: Optional[str] = typer.Option(
        None, "--dir", "-d",
        help="Path to the Expo project (default: EXPO_PROJECT_DIR from .env)",
    ),
    no_git: bool = typer.Option(False, "--no-git", help="Skip git checkout + PR"),
    no_test: bool = typer.Option(False, "--no-test", help="Skip browser screenshot test"),
    revisions: int = typer.Option(3, "--revisions", "-r", help="Max review cycles per subtask"),
) -> None:
    """
    Run the multi-agent pipeline on your Expo project.

    Examples:\n
        agent run "Add a dark mode toggle to the settings screen"\n
        agent run "Create a new profile screen with avatar upload" --dir ~/Projects/my-app\n
        agent run "Fix the login form validation" --no-test
    """
    if not os.environ.get("GEMINI_API_KEY"):
        console.print("[red]GEMINI_API_KEY not set — create .env from .env.example[/red]")
        raise typer.Exit(1)

    # Resolve project directory
    target_dir = project_dir or os.environ.get("EXPO_PROJECT_DIR", "")
    if target_dir:
        target_dir = str(Path(target_dir).expanduser().resolve())
        if not Path(target_dir).exists():
            console.print(f"[red]Project directory does not exist: {target_dir}[/red]")
            raise typer.Exit(1)

    orchestrator = Orchestrator()
    orchestrator.run(
        task=task,
        project_dir=target_dir,
        git_enabled=not no_git,
        test_enabled=not no_test,
        max_revisions=revisions,
    )


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
    uvicorn.run("src.web.server:app", host=h, port=p, reload=reload)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()

