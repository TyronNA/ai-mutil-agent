"""Orchestrator — coordinates all agents in the pipeline."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.agents.analyzer import AnalyzerAgent
from src.agents.planner import PlannerAgent
from src.agents.coder import CoderAgent
from src.agents.reviewer import ReviewerAgent
from src.agents.tester import TesterAgent
from src.agents.notifier import NotifierAgent
from src.state import AgentState, Phase, Subtask
from src.tools import git
from src.llm import delete_cache

console = Console()


def _branch_name(task: str) -> str:
    """Return a shared nightly branch name based on today's date.
    All queue tasks on the same day accumulate commits on one branch → one PR.
    """
    date_suffix = datetime.now().strftime("%d%m%Y")
    return f"night-mate-{date_suffix}"


class Orchestrator:
    """
    Full pipeline:
      git checkout -> analyze codebase -> plan -> [code -> review] loop -> test -> git push+PR -> notify
    """

    def __init__(self) -> None:
        self.analyzer = AnalyzerAgent()
        self.planner = PlannerAgent()
        self.coder = CoderAgent()
        self.reviewer = ReviewerAgent()
        self.tester = TesterAgent()
        self.notifier = NotifierAgent()

    def run(
        self,
        task: str,
        project_dir: str = "",
        git_enabled: bool = True,
        test_enabled: bool = True,
        max_revisions: int = 3,
        progress_cb: Optional[Callable] = None,
    ) -> AgentState:
        state = AgentState(
            task=task,
            project_dir=project_dir,
            git_enabled=git_enabled,
            test_enabled=test_enabled,
            max_revisions=max_revisions,
            progress_cb=progress_cb,
        )

        # ── Phase 0: Git checkout ────────────────────────────────────────────
        if git_enabled:
            branch = _branch_name(task)
            state.branch = branch
            if project_dir:
                try:
                    console.print(f"[cyan]Git: checking out branch {branch}[/cyan]")
                    git.git_checkout_branch(project_dir, branch)
                    state.log(f"Checked out branch: {branch}", agent="git")
                except Exception as e:
                    state.log(f"Git checkout skipped: {e}", agent="git")

        # ── Phase 0.5: Codebase analysis ────────────────────────────────────
        # Reads source files, extracts conventions, detects pre-existing TS errors.
        # Results flow to Planner, Coder, and Reviewer via state.codebase_context.
        if project_dir:
            console.print(Panel("Phase 0.5: Analyzing codebase", style="bold green"))
            try:
                state = self.analyzer.run(state)
            except Exception as e:
                state.log(f"Analysis error (continuing without context): {e}", agent="analyzer")

        # ── Phase 1: Planning ────────────────────────────────────────────────
        console.print(Panel("Phase 1: Planning", style="bold cyan"))
        try:
            state = self.planner.run(state)
        except Exception as e:
            state.current_phase = Phase.FAILED
            state.error = f"Planning failed: {e}"
            console.print(f"[red]{state.error}[/red]")
            return state

        self._print_subtasks(state)

        # ── Phase 2–3: Code → Review loop ────────────────────────────────────
        # Run subtasks in parallel (up to 3 at a time) — each gets its own
        # Coder+Reviewer pair so they don't share mutable state.
        if len(state.subtasks) > 1:
            self._run_subtasks_parallel(state, max_revisions)
        else:
            for subtask in state.subtasks:
                self._run_single_subtask(state, subtask, max_revisions)

        # ── Phase 4: Browser test + screenshot ───────────────────────────────
        if test_enabled and project_dir:
            console.print(Panel("Phase 4: Browser Testing", style="bold magenta"))
            try:
                state = self.tester.run(state)
            except Exception as e:
                state.log(f"Tester error (skipping): {e}", agent="tester")

        # ── Phase 5: Git commit + push + PR ──────────────────────────────────
        if git_enabled and project_dir:
            console.print(Panel("Phase 5: Commit & PR", style="bold blue"))
            self._git_push_and_pr(state)

        # ── Phase 6: Notify ──────────────────────────────────────────────────
        try:
            state = self.notifier.run(state)
        except Exception as e:
            state.log(f"Notification error: {e}", agent="notifier")

        state.current_phase = Phase.DONE
        self._print_summary(state)
        return state

    def _run_single_subtask(self, state: AgentState, subtask: Subtask, max_revisions: int) -> None:
        """Run the code→review loop for one subtask (mutates state in-place)."""
        coder = CoderAgent()
        reviewer = ReviewerAgent()
        console.print(Panel(f"Subtask {subtask.id}: {subtask.description}", style="bold yellow"))

        try:
            for revision in range(max_revisions + 1):
                console.print(f"  [green]→ Coding (attempt {revision + 1})[/green]")
                try:
                    coder.run(state, subtask=subtask)
                except Exception as e:
                    subtask.status = "failed"
                    console.print(f"  [red]Coding error: {e}[/red]")
                    break

                console.print("  [blue]→ Reviewing[/blue]")
                try:
                    reviewer.run(state, subtask=subtask)
                except Exception as e:
                    subtask.status = "done"
                    console.print(f"  [yellow]Review error (accepting): {e}[/yellow]")
                    break

                if subtask.status == "done":
                    console.print(f"  [green]✓ {subtask.review_feedback[:80]}[/green]")
                    break

                console.print(f"  [yellow]↻ {subtask.review_feedback[:80]}[/yellow]")

            if subtask.status != "done":
                subtask.status = "done"
                console.print(f"  [yellow]⚠ Accepted best effort for subtask {subtask.id}[/yellow]")
        finally:
            # Always clean up the Gemini context cache for this subtask
            if subtask.code_cache_name:
                delete_cache(subtask.code_cache_name)

    def _run_subtasks_parallel(self, state: AgentState, max_revisions: int, max_workers: int = 3) -> None:
        """Run subtasks concurrently (up to max_workers at a time).
        
        Each worker has its own Coder+Reviewer instances to avoid shared state.
        Writes from different subtasks are safe because each writes different files.
        """
        state.log(f"Running {len(state.subtasks)} subtasks in parallel (workers={max_workers})", agent="orchestrator")

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_single_subtask, state, subtask, max_revisions): subtask
                for subtask in state.subtasks
            }
            for future in as_completed(futures):
                subtask = futures[future]
                try:
                    future.result()
                except Exception as e:
                    subtask.status = "done"
                    state.log(f"Subtask {subtask.id} error (accepting best effort): {e}", agent="orchestrator")

    def _git_push_and_pr(self, state: AgentState) -> None:
        github_token = os.environ.get("GITHUB_TOKEN", "")
        repo_full_name = os.environ.get("GITHUB_REPO", "")

        if not github_token or not repo_full_name:
            state.log("GITHUB_TOKEN or GITHUB_REPO not set — skipping PR.", agent="git")
            return

        state.current_phase = Phase.PUSHING
        try:
            # Commit
            commit_msg = (
                f"feat: {state.task[:60]}\n\n"
                f"Files: {', '.join(state.files_written)}\n\n"
                "Generated by AI Multi-Agent System (Gemini 3.0 Flash)"
            )
            sha = git.git_commit_all(state.project_dir, commit_msg)
            state.commit_sha = sha
            state.log(f"Committed: {sha[:8]}", agent="git")

            # Push
            git.git_push_branch(state.project_dir, state.branch, github_token, repo_full_name)
            state.log(f"Pushed branch: {state.branch}", agent="git")

            # Build PR body
            files_list = "\n".join(f"- `{f}`" for f in state.files_written)
            screenshots_md = ""
            if state.screenshots:
                screenshots_md = "\n### Screenshots\n" + "\n".join(
                    f"![screenshot](.agent/screenshots/{p.split('/')[-1]})" for p in state.screenshots
                )

            pr_body = (
                f"## Task\n{state.task}\n\n"
                f"## Plan\n{state.plan_summary}\n\n"
                f"## Files Changed\n{files_list}"
                f"{screenshots_md}\n\n"
                "---\n*Generated by AI Multi-Agent System (Gemini 3.0 Flash)*"
            )

            pr_url = git.create_github_pr(
                repo_full_name=repo_full_name,
                github_token=github_token,
                branch=state.branch,
                title=f"[AI Agent] {state.branch}",
                body=pr_body,
            )
            state.pr_url = pr_url
            state.log(f"PR created: {pr_url}", agent="git")
            console.print(f"  [bold green]🔗 PR: {pr_url}[/bold green]")

        except Exception as e:
            state.log(f"Git push/PR error: {e}", agent="git")
            console.print(f"  [red]Git error: {e}[/red]")

    def _print_subtasks(self, state: AgentState) -> None:
        table = Table(title="Subtasks")
        table.add_column("ID", style="cyan")
        table.add_column("Description")
        table.add_column("Files")
        for st in state.subtasks:
            table.add_row(str(st.id), st.description, ", ".join(st.files_to_touch[:3]))
        console.print(table)

    def _print_summary(self, state: AgentState) -> None:
        console.print(Panel("✅ Complete!", style="bold green"))
        console.print(f"Files written: {len(state.files_written)}")
        for f in state.files_written:
            console.print(f"  [dim]{f}[/dim]")
        if state.pr_url:
            console.print(f"\n[bold]🔗 PR:[/bold] {state.pr_url}")
        if state.screenshots:
            console.print(f"[bold]📸 Screenshots:[/bold] {len(state.screenshots)}")
            for s in state.screenshots:
                console.print(f"  [dim]{s}[/dim]")

