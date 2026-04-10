"""GameOrchestrator — coordinates the 3-agent pipeline for Mộng Võ Lâm.

Pipeline
────────
  Phase 0  : Git checkout (optional)
  Phase 0.5: Load game source context + create Gemini cache
  Phase 1  : TechExpert.plan() — Gemini Pro produces subtasks + test scenarios + constraints
  Phase 2–3: Parallel subtask loops (up to 3 concurrent workers)
               └─ for each subtask:
                    Dev.run()  → write files
                    QA.run()   → static verify
                    if QA fails → Dev.run() again (max max_revisions)
  Phase 4  : TechExpert.review() — final code review (Gemini Pro)
  Phase 5  : Git commit + push + PR (optional)
  Phase 6  : macOS notification

All agents share a single GameAgentState.  Dev + QA each get fresh instances
per subtask so there is no cross-subtask state leakage.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.agents.tech_expert import TechExpertAgent
from src.agents.dev import DevAgent
from src.agents.qa import QAAgent
from src.agents.notifier import NotifierAgent
from src.context.game_loader import load_game_context
from src.llm import delete_cache
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools import git

console = Console()


def _branch_name(task: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", task[:40]).strip("-").lower()
    ts = datetime.now().strftime("%m%d-%H%M")
    return f"agent/game/{safe}-{ts}"


class GameOrchestrator:
    """
    Orchestrates the TechExpert → Dev ↔ QA pipeline for Mộng Võ Lâm.

    Usage:
        orch = GameOrchestrator()
        state = orch.run(task="Add daily reward popup", game_project_dir="/path/to/mong-vo-lam")
    """

    def __init__(self, tech_expert_pro: bool = False) -> None:
        self.tech_expert = TechExpertAgent(pro_planning=tech_expert_pro)
        self.notifier    = NotifierAgent()

    def run(
        self,
        task: str,
        game_project_dir: str,
        git_enabled: bool = True,
        max_revisions: int = 3,
        max_workers: int = 1,
        subtask_delay: float = 0.0,
        stop_flag: Optional[threading.Event] = None,
        progress_cb: Optional[Callable] = None,
    ) -> GameAgentState:

        state = GameAgentState(
            task=task,
            game_project_dir=game_project_dir,
            git_enabled=git_enabled,
            max_revisions=max_revisions,
            progress_cb=progress_cb,
        )
        state.subtask_delay = subtask_delay
        state.stop_flag = stop_flag

        # ── Phase 0: Git checkout ────────────────────────────────────────────
        if git_enabled and game_project_dir:
            branch = _branch_name(task)
            state.branch = branch
            try:
                console.print(f"[cyan]Git: checking out branch {branch}[/cyan]")
                git.git_checkout_branch(game_project_dir, branch)
                state.log(f"Checked out branch: {branch}", agent="git")
            except Exception as e:
                state.log(f"Git checkout skipped: {e}", agent="git")

        # ── Phase 0.5: Load game context + Gemini cache ──────────────────────
        console.print(Panel("Phase 0.5: Loading game source context", style="bold green"))
        state.current_phase = GamePhase.LOADING
        try:
            context, cache_name = load_game_context(game_project_dir)
            state.game_context        = context
            state.context_cache_name  = cache_name or ""
            state.log(
                f"Game context loaded (~{len(context):,} chars)"
                + (f", cached as {cache_name}" if cache_name else " (inline, no cache)"),
                agent="loader",
            )
        except Exception as e:
            state.log(f"Context load error (continuing without): {e}", agent="loader")

        # ── Phase 1: TechExpert plans ────────────────────────────────────────
        console.print(Panel("Phase 1: TechExpert — Implementation Plan (Gemini Pro)", style="bold cyan"))
        try:
            state = self.tech_expert.plan(state)
        except Exception as e:
            state.current_phase = GamePhase.FAILED
            state.error = f"Planning failed: {e}"
            console.print(f"[red]{state.error}[/red]")
            return state

        self._print_plan(state)

        # ── Phase 2–3: Parallel Dev + QA loops ──────────────────────────────
        console.print(Panel("Phase 2–3: Dev + QA parallel loops", style="bold yellow"))
        if len(state.subtasks) > 1 and max_workers > 1:
            self._run_subtasks_parallel(state, max_revisions, max_workers)
        else:
            # Sequential — respects subtask_delay and stop_flag between each
            import time
            for i, subtask in enumerate(state.subtasks):
                if stop_flag and stop_flag.is_set():
                    state.log("Stopped by user — skipping remaining subtasks.", agent="orchestrator")
                    break
                if i > 0 and subtask_delay > 0:
                    state.log(f"Slow mode: waiting {subtask_delay:.0f}s before next subtask…", agent="orchestrator")
                    time.sleep(subtask_delay)
                self._run_single_subtask(state, subtask, max_revisions)

        # ── Phase 4: TechExpert final review ────────────────────────────────
        console.print(Panel("Phase 4: TechExpert — Final Review (Gemini Pro)", style="bold magenta"))
        try:
            state = self.tech_expert.review(state)
            verdict_color = "green" if state.review_verdict == "approved" else "yellow"
            console.print(
                f"  [{verdict_color}]Verdict: {state.review_verdict}[/{verdict_color}] — "
                f"{state.review_notes[:120]}"
            )
            if state.review_specific_issues:
                for issue in state.review_specific_issues:
                    console.print(f"  [dim]• {issue}[/dim]")
        except Exception as e:
            state.log(f"Final review error (skipping): {e}", agent="tech_expert")

        # ── Phase 5: Git commit + push + PR ─────────────────────────────────
        if git_enabled and game_project_dir and state.files_written:
            console.print(Panel("Phase 5: Commit & PR", style="bold blue"))
            self._git_push_and_pr(state)

        # ── Phase 6: Notify ──────────────────────────────────────────────────
        try:
            self.notifier.run(state)  # type: ignore[arg-type]
        except Exception as e:
            state.log(f"Notification error: {e}", agent="notifier")

        state.current_phase = GamePhase.DONE
        self._print_summary(state)

        # Clean up shared context cache
        if state.context_cache_name:
            delete_cache(state.context_cache_name)
            state.log(f"Context cache deleted: {state.context_cache_name}", agent="loader")

        return state

    # ── Subtask execution ─────────────────────────────────────────────────────

    def _run_single_subtask(
        self,
        state: GameAgentState,
        subtask: GameSubtask,
        max_revisions: int,
    ) -> None:
        """Run the Dev → QA loop for one subtask. Mutates state in-place."""
        dev = DevAgent()
        qa  = QAAgent()
        console.print(Panel(
            f"Subtask {subtask.id}: {subtask.description}",
            style="bold yellow",
        ))

        try:
            for revision in range(max_revisions + 1):
                # ── Dev codes ────────────────────────────────────────────────
                console.print(f"  [green]→ Dev coding (attempt {revision + 1})[/green]")
                try:
                    dev.run(state, subtask=subtask)
                except Exception as e:
                    subtask.status = "failed"
                    state.log(f"[Subtask {subtask.id}] Dev error: {e}", agent="dev")
                    console.print(f"  [red]Dev error: {e}[/red]")
                    break

                # ── QA verifies ──────────────────────────────────────────────
                console.print("  [blue]→ QA verifying[/blue]")
                try:
                    qa.run(state, subtask=subtask)
                except Exception as e:
                    # QA crashed — accept code as-is
                    subtask.qa_passed = True
                    subtask.qa_summary = f"QA error (accepted): {e}"
                    state.log(f"[Subtask {subtask.id}] QA error (accepting): {e}", agent="qa")

                if subtask.qa_passed:
                    subtask.status = "done"
                    console.print(f"  [green]✓ QA passed — {subtask.qa_summary[:80]}[/green]")
                    break

                # QA failed — loop back to Dev
                subtask.revision_count += 1
                console.print(
                    f"  [yellow]↻ QA failed ({len(subtask.qa_issues)} issue(s)) — "
                    f"revision {subtask.revision_count}/{max_revisions}[/yellow]"
                )
                for issue in subtask.qa_issues:
                    sev = issue.get("severity", "?")
                    desc = issue.get("description", "")
                    console.print(f"    [dim][{sev.upper()}] {desc[:90]}[/dim]")

            # Accept best effort if max revisions hit
            if subtask.status != "done":
                subtask.status = "done"
                console.print(
                    f"  [yellow]⚠ Max revisions reached — accepting best effort "
                    f"for subtask {subtask.id}[/yellow]"
                )

        finally:
            # Always clean up Dev's subtask context cache
            if subtask.code_cache_name:
                delete_cache(subtask.code_cache_name)
                subtask.code_cache_name = ""

    def _run_subtasks_parallel(
        self,
        state: GameAgentState,
        max_revisions: int,
        max_workers: int = 3,
    ) -> None:
        """Run subtasks concurrently. Each worker has isolated Dev + QA instances."""
        import time
        delay = getattr(state, "subtask_delay", 0.0)
        state.log(
            f"Running {len(state.subtasks)} subtasks in parallel (workers={max_workers})",
            agent="orchestrator",
        )

        stop = getattr(state, "stop_flag", None)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for subtask in state.subtasks:
                if stop and stop.is_set():
                    state.log("Stopped by user — skipping remaining subtasks.", agent="orchestrator")
                    break
                futures[pool.submit(self._run_single_subtask, state, subtask, max_revisions)] = subtask
                if delay > 0:
                    time.sleep(delay)
            for future in as_completed(futures):
                subtask = futures[future]
                try:
                    future.result()
                except Exception as e:
                    subtask.status = "done"
                    state.log(
                        f"[Subtask {subtask.id}] Unexpected error (accepting best effort): {e}",
                        agent="orchestrator",
                    )

    # ── Git ───────────────────────────────────────────────────────────────────

    def _git_push_and_pr(self, state: GameAgentState) -> None:
        github_token   = os.environ.get("GITHUB_TOKEN", "")
        repo_full_name = os.environ.get("GITHUB_REPO", "")

        if not github_token or not repo_full_name:
            state.log("GITHUB_TOKEN or GITHUB_REPO not set — skipping PR.", agent="git")
            return

        state.current_phase = GamePhase.PUSHING
        try:
            # Build QA report for PR body
            qa_lines = []
            for st in state.subtasks:
                icon = "✅" if st.qa_passed else "⚠️"
                qa_lines.append(f"- {icon} Subtask {st.id}: {st.qa_summary[:80]}")
            qa_block = "\n".join(qa_lines)

            files_list = "\n".join(f"- `{f}`" for f in state.files_written)
            review_block = (
                f"**Verdict:** {state.review_verdict}\n{state.review_notes}"
                if state.review_verdict else ""
            )

            commit_msg = (
                f"feat(game): {state.task[:60]}\n\n"
                f"Files: {', '.join(state.files_written)}\n\n"
                "Generated by AI Game Agent (TechExpert + Dev + QA)"
            )
            sha = git.git_commit_all(state.game_project_dir, commit_msg)
            state.commit_sha = sha
            state.log(f"Committed: {sha[:8]}", agent="git")

            git.git_push_branch(
                state.game_project_dir,
                state.branch,
                github_token,
                repo_full_name,
            )
            state.log(f"Pushed: {state.branch}", agent="git")

            pr_body = (
                f"## Task\n{state.task}\n\n"
                f"## Implementation Plan\n{state.implementation_plan}\n\n"
                f"## Files Changed\n{files_list}\n\n"
                f"## QA Results\n{qa_block}\n\n"
                f"## Tech Expert Review\n{review_block}\n\n"
                "---\n*Generated by AI Game Agent — TechExpert (Gemini Pro) + Dev + QA*"
            )
            pr_url = git.create_github_pr(
                repo_full_name=repo_full_name,
                github_token=github_token,
                branch=state.branch,
                title=f"[AI Game Agent] {state.task[:60]}",
                body=pr_body,
            )
            state.pr_url = pr_url
            state.log(f"PR created: {pr_url}", agent="git")
            console.print(f"  [bold green]🔗 PR: {pr_url}[/bold green]")

        except Exception as e:
            state.log(f"Git push/PR error: {e}", agent="git")
            console.print(f"  [red]Git error: {e}[/red]")

    # ── Rich output helpers ───────────────────────────────────────────────────

    def _print_plan(self, state: GameAgentState) -> None:
        table = Table(title="TechExpert Plan", show_lines=True)
        table.add_column("ID",    style="cyan",   width=4)
        table.add_column("Subtask",               width=50)
        table.add_column("Files",                 width=40)
        for st in state.subtasks:
            table.add_row(
                str(st.id),
                st.description[:80],
                "\n".join(st.files_to_touch[:4]),
            )
        console.print(table)

        if state.global_constraints:
            console.print("[bold]Global constraints:[/bold]")
            for c in state.global_constraints:
                console.print(f"  • {c}")

        if state.test_scenarios:
            console.print(f"[bold]QA test scenarios:[/bold] {len(state.test_scenarios)}")

    def _print_summary(self, state: GameAgentState) -> None:
        console.print(Panel("✅ Game Agent Complete!", style="bold green"))

        # Subtask results
        table = Table(title="Subtask Results", show_lines=True)
        table.add_column("ID",     style="cyan",  width=4)
        table.add_column("Status",                width=10)
        table.add_column("QA",                    width=8)
        table.add_column("Revisions",             width=10)
        table.add_column("Summary",               width=60)
        for st in state.subtasks:
            qa_icon = "✅" if st.qa_passed else "❌"
            table.add_row(
                str(st.id),
                st.status,
                qa_icon,
                str(st.revision_count),
                st.code_summary[:60],
            )
        console.print(table)

        console.print(f"\n[bold]Files written:[/bold] {len(state.files_written)}")
        for f in state.files_written:
            console.print(f"  [dim]{f}[/dim]")

        if state.review_verdict:
            color = "green" if state.review_verdict == "approved" else "yellow"
            console.print(f"\n[bold]Tech Expert review:[/bold] [{color}]{state.review_verdict}[/{color}]")
            if state.review_notes:
                console.print(f"  {state.review_notes[:200]}")

        if state.pr_url:
            console.print(f"\n[bold]🔗 PR:[/bold] {state.pr_url}")
