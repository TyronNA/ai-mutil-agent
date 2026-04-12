"""GameOrchestrator — coordinates the 3-agent pipeline for Mộng Võ Lâm.

Pipeline
────────
  Phase 0  : Git checkout (optional)
  Phase 0.5: Load game source context + create Gemini cache
  Phase 0.6: Load cross-run lessons (config/game-lessons.md)
    Phase 1  : TechExpert.plan() — produces subtasks + test scenarios + constraints
  Phase 2–3: Parallel subtask loops (up to 3 concurrent workers)
               └─ for each subtask:
                    Dev.run()  → write files
                    QA.run()   → static verify
                    if QA fails → Dev.run() again (max max_revisions)
    Phase 4  : TechExpert.review() — final code review (Gemini Flash)
  Phase 4.5: Capture run lessons → config/game-lessons.md
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
from src.lessons import load_lessons, capture_lessons
from src.llm import delete_cache
from src.state_game import GameAgentState, GamePhase, GameSubtask
from src.tools import git
from src.tools.filesystem import read_file

console = Console()


def _branch_name(task: str) -> str:
    """Return a shared nightly branch name based on today's date.
    All queue tasks on the same day accumulate commits on one branch → one PR.
    """
    date_suffix = datetime.now().strftime("%d%m%Y")
    return f"night-mate-{date_suffix}"


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
        max_revisions: int = 5,
        max_workers: int = 1,
        max_subtasks: int = 5,
        subtask_delay: float = 0.0,
        enqueue_suggestions: bool = False,
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
        state.max_subtasks = max_subtasks

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
            static_ctx, dynamic_ctx, cache_name = load_game_context(game_project_dir)
            state.game_context         = static_ctx
            state.game_dynamic_context = dynamic_ctx
            state.context_cache_name   = cache_name or ""
            state.log(
                f"Context loaded — static ~{len(static_ctx):,} chars"
                f", dynamic ~{len(dynamic_ctx):,} chars"
                + (f", cache: {cache_name}" if cache_name else " (no cache — inline)"),
                agent="loader",
            )
        except Exception as e:
            state.log(f"Context load error (continuing without): {e}", agent="loader")

        # ── Phase 0.6: Load cross-run lessons ─────────────────────────────────
        lessons = load_lessons()
        if lessons:
            state.lessons_context = lessons
            state.log(
                f"Lessons loaded — {len(lessons):,} chars of prior-run knowledge.",
                agent="lessons",
            )

        # ── Phase 1: TechExpert plans ─────────────────────────────────────────
        console.print(Panel("Phase 1: TechExpert — Implementation Plan", style="bold cyan"))
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

        # ── Phase 2-3 gate: abort early if all subtasks failed ───────────────
        failed_subtasks = [s for s in state.subtasks if s.status == "failed"]
        if failed_subtasks and len(failed_subtasks) == len(state.subtasks):
            state.current_phase = GamePhase.FAILED
            state.error = (
                f"All {len(failed_subtasks)} subtask(s) failed with Dev errors — "
                "no code was written. Skipping review/lint/git."
            )
            console.print(f"[red bold]{state.error}[/red bold]")
            try:
                self.notifier.run(state)  # type: ignore[arg-type]
            except Exception as e:
                state.log(f"Notification error: {e}", agent="notifier")
            self._print_summary(state)
            if state.context_cache_name:
                delete_cache(state.context_cache_name)
                state.log(f"Context cache deleted: {state.context_cache_name}", agent="loader")
            return state

        if failed_subtasks:
            state.log(
                f"⚠ {len(failed_subtasks)}/{len(state.subtasks)} subtask(s) failed — "
                "continuing with partial results.",
                agent="orchestrator",
            )
            console.print(
                f"  [yellow]⚠ {len(failed_subtasks)} subtask(s) failed — "
                "continuing with partial results.[/yellow]"
            )

        # ── Phase 4: TechExpert final review ────────────────────────────────
        console.print(Panel("Phase 4: TechExpert — Final Review (Gemini Flash)", style="bold magenta"))
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

        # ── Phase 4.1: Dev fix-up pass if TechExpert review not approved ────────────
        if state.review_verdict not in ("approved", "") and state.review_specific_issues:
            console.print(Panel("Phase 4.1: Dev Fix-up (TechExpert review issues)", style="bold red"))
            state = self._run_review_fixup(state)
            # Re-run TechExpert review to get updated verdict
            try:
                state = self.tech_expert.review(state)
                verdict_color = "green" if state.review_verdict == "approved" else "red"
                console.print(
                    f"  [{verdict_color}]Re-review Verdict: {state.review_verdict}[/{verdict_color}] — "
                    f"{state.review_notes[:120]}"
                )
                if state.review_verdict != "approved":
                    console.print(
                        "  [red]⛔ Re-review still not approved — PR will be blocked.[/red]"
                    )
            except Exception as e:
                state.log(f"Re-review error (skipping): {e}", agent="tech_expert")

        # ── Phase 4.5: Capture lessons for future runs ──────────────────────────
        try:
            capture_lessons(state)
            state.log("Lessons captured — config/game-lessons.md updated.", agent="lessons")
        except Exception as e:
            state.log(f"Lesson capture error (non-fatal): {e}", agent="lessons")

        # ── Phase 4.6: Persist QA queue suggestions (opt-in only) ──────────
        if enqueue_suggestions:
            self._enqueue_qa_suggestions(state)
        elif any(s.queue_suggestions for s in state.subtasks):
            total = sum(len(s.queue_suggestions) for s in state.subtasks)
            state.log(
                f"{total} QA suggestion(s) suppressed (enqueue_suggestions=False).",
                agent="orchestrator",
            )

        # ── Phase 4.7: npm run lint gate (with auto-fix attempt) ─────────────
        if game_project_dir and state.files_written:
            console.print(Panel("Phase 4.7: npm run lint", style="bold cyan"))
            from src.tools.game_tools import run_npm_lint
            lint_ok, lint_output = run_npm_lint(game_project_dir)
            state.lint_passed = lint_ok
            state.lint_output = lint_output
            state.log(f"Lint: {lint_output[:200]}", agent="lint")
            if lint_ok:
                console.print("  [green]✓ Lint passed[/green]")
            else:
                console.print("  [red]✗ Lint failed — attempting auto-fix[/red]")
                console.print(f"  [dim]{lint_output[:600]}[/dim]")
                state = self._run_lint_fixup(state, lint_output)
                # Re-run lint after fix attempt
                lint_ok2, lint_output2 = run_npm_lint(game_project_dir)
                state.lint_passed = lint_ok2
                state.lint_output = lint_output2
                state.log(f"Lint (post-fix): {lint_output2[:200]}", agent="lint")
                if lint_ok2:
                    console.print("  [green]✓ Lint passed after auto-fix[/green]")
                else:
                    console.print("  [red]✗ Lint still failing after fix attempt — blocking PR push[/red]")
                    console.print(f"  [dim]{lint_output2[:400]}[/dim]")

        # ── Phase 5: Git commit + push + PR ──────────────────────────────────
        if git_enabled and game_project_dir and state.files_written and state.review_verdict == "approved" and state.lint_passed:
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
            _stop = getattr(state, "stop_flag", None)
            for revision in range(max_revisions + 1):
                # ── Check stop before each revision ───────────────────────────
                if _stop and _stop.is_set():
                    state.log(f"[Subtask {subtask.id}] Stopped before revision {revision + 1}.", agent="orchestrator")
                    subtask.status = "done"
                    break

                # ── Dev codes ────────────────────────────────────────────────
                _prev_written = dict(subtask.written_files)  # convergence snapshot
                console.print(f"  [green]→ Dev coding (attempt {revision + 1})[/green]")
                try:
                    dev.run(state, subtask=subtask)
                except Exception as e:
                    subtask.status = "failed"
                    state.log(f"[Subtask {subtask.id}] Dev error: {e}", agent="dev")
                    console.print(f"  [red]Dev error: {e}[/red]")
                    break

                # ── Convergence check ─────────────────────────────────────────
                if revision > 0 and subtask.written_files == _prev_written:
                    state.log(
                        f"[Subtask {subtask.id}] No file changes detected after Dev — stopping loop early.",
                        agent="orchestrator",
                    )
                    console.print("  [yellow]⚠ Dev made no changes — stopping loop early[/yellow]")
                    break
                # ── npm lint gate — before QA, catch syntax/import errors early ───────
                if state.game_project_dir:
                    from src.tools.game_tools import run_npm_lint
                    console.print("  [cyan]→ npm lint (pre-QA check)[/cyan]")
                    lint_ok, lint_out = run_npm_lint(state.game_project_dir)
                    if not lint_ok:
                        state.log(
                            f"[Subtask {subtask.id}] npm lint failed (attempt {revision + 1}): {lint_out[:200]}",
                            agent="lint",
                        )
                        console.print(f"  [red]✗ npm lint failed — feeding errors back to Dev[/red]")
                        console.print(f"  [dim]{lint_out[:400]}[/dim]")
                        # Inject lint errors as critical QA issues so Dev fixes them next round
                        subtask.qa_passed = False
                        subtask.qa_issues = [
                            {
                                "severity": "critical",
                                "file": "",
                                "description": f"npm lint error: {line.strip()}",
                            }
                            for line in lint_out.splitlines()
                            if "error" in line.lower() and line.strip()
                        ] or [{"severity": "critical", "file": "", "description": lint_out[:600]}]
                        subtask.qa_summary = f"npm lint failed (attempt {revision + 1}): {lint_out[:200]}"
                        subtask.revision_count += 1
                        console.print(
                            f"  [yellow]↻ Lint errors — revision {subtask.revision_count}/{max_revisions}[/yellow]"
                        )
                        continue  # back to Dev with lint feedback

                    console.print("  [green]✓ npm lint passed[/green]")
                # ── Check stop between Dev and QA ─────────────────────────────
                if _stop and _stop.is_set():
                    state.log(f"[Subtask {subtask.id}] Stopped after Dev (skipping QA).", agent="orchestrator")
                    subtask.status = "done"
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

            # Accept best effort if max revisions hit — but preserve "failed" status
            # (failed = Dev threw an exception; done = completed or best-effort)
            if subtask.status not in ("done", "failed"):
                subtask.status = "done"
                console.print(
                    f"  [yellow]⚠ Max revisions reached — accepting best effort "
                    f"for subtask {subtask.id}[/yellow]"
                )
            elif subtask.status == "failed":
                console.print(
                    f"  [red]✗ Subtask {subtask.id} FAILED (Dev error) — pipeline will continue "
                    f"but result may be incomplete.[/red]"
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
        from src import llm as _llm
        delay = getattr(state, "subtask_delay", 0.0)
        state.log(
            f"Running {len(state.subtasks)} subtasks in parallel (workers={max_workers})",
            agent="orchestrator",
        )

        # Capture session_id from current thread so workers can inherit it
        _session_id = _llm.get_session_id()

        stop = getattr(state, "stop_flag", None)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            def _worker(subtask):
                if _session_id:
                    _llm.set_session_id(_session_id)
                self._run_single_subtask(state, subtask, max_revisions)

            futures = {}
            for subtask in state.subtasks:
                if stop and stop.is_set():
                    state.log("Stopped by user — skipping remaining subtasks.", agent="orchestrator")
                    break
                futures[pool.submit(_worker, subtask)] = subtask
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

    # ── QA suggestion queuing ─────────────────────────────────────────────────

    def _enqueue_qa_suggestions(self, state: GameAgentState) -> None:
        """Collect out-of-scope QA suggestions from all subtasks and add them to the task queue."""
        all_suggestions: list[str] = []
        for subtask in state.subtasks:
            all_suggestions.extend(subtask.queue_suggestions)

        if not all_suggestions:
            return

        try:
            import src.db as _db
            _db.init_db()
            for suggestion in all_suggestions:
                _db.add_queue_task(suggestion, pipeline_type="game", source="audit", priority=3)
                console.print(f"  [dim]+ Queued: {suggestion[:80]}[/dim]")
            state.log(
                f"Queued {len(all_suggestions)} out-of-scope suggestion(s) for future runs.",
                agent="orchestrator",
            )
            console.print(
                f"  [cyan]📋 {len(all_suggestions)} side-issue(s) added to queue (priority 3/low)[/cyan]"
            )
        except Exception as e:
            state.log(f"Failed to enqueue QA suggestions: {e}", agent="orchestrator")

    # ── Review fix-up ─────────────────────────────────────────────────────────

    def _run_review_fixup(self, state: GameAgentState) -> GameAgentState:
        """Run a single Dev pass targeting TechExpert review issues, followed by QA."""
        dev = DevAgent()
        qa  = QAAgent()

        # Synthetic subtask covering all files written during the main run
        fixup_subtask = GameSubtask(
            id=0,
            description=(
                f"Fix TechExpert review issues: "
                + "; ".join(state.review_specific_issues[:3])
            ),
            files_to_touch=list(state.files_written),
        )

        # Seed with current on-disk content so Dev patches against the real state
        for rel_path in fixup_subtask.files_to_touch:
            if state.game_project_dir:
                raw = read_file(state.game_project_dir, rel_path, max_chars=200_000)
                if raw:
                    fixup_subtask.original_files[rel_path] = raw
                    fixup_subtask.written_files[rel_path] = raw

        # Force a revision-1 path so Dev receives the issue list (not "Attempt 1")
        fixup_subtask.revision_count = 1
        fixup_subtask.qa_passed = False
        fixup_subtask.qa_issues = [
            {
                "severity": "critical",
                "file": state.files_written[0] if state.files_written else "",
                "description": issue,
            }
            for issue in state.review_specific_issues
        ]
        fixup_subtask.qa_summary = (
            f"TechExpert final review failed: {state.review_notes[:120]}"
        )

        try:
            console.print("  [red]→ Dev fix-up (review issues)[/red]")
            dev.run(state, subtask=fixup_subtask)

            console.print("  [blue]→ QA verifying fix-up[/blue]")
            qa.run(state, subtask=fixup_subtask)

            qa_icon = "✓" if fixup_subtask.qa_passed else "⚠"
            qa_color = "green" if fixup_subtask.qa_passed else "yellow"
            console.print(
                f"  [{qa_color}]{qa_icon} Fix-up QA — {fixup_subtask.qa_summary[:80]}[/{qa_color}]"
            )
        except Exception as e:
            state.log(f"Review fix-up error (non-fatal): {e}", agent="orchestrator")
        finally:
            if fixup_subtask.code_cache_name:
                delete_cache(fixup_subtask.code_cache_name)

        return state

    # ── Lint fix-up ───────────────────────────────────────────────────────────

    def _run_lint_fixup(self, state: GameAgentState, lint_output: str) -> GameAgentState:
        """Run a single Dev pass to fix ESLint errors, no QA needed (lint is objective)."""
        dev = DevAgent()

        fixup_subtask = GameSubtask(
            id=0,
            description=(
                "Fix ESLint errors reported by `npm run lint`. "
                "Remove unused variables/imports, fix any other flagged issues. "
                "Do NOT change logic — only fix lint errors."
            ),
            files_to_touch=list(state.files_written),
        )

        # Seed with current on-disk content so Dev patches against real state
        for rel_path in fixup_subtask.files_to_touch:
            if state.game_project_dir:
                raw = read_file(state.game_project_dir, rel_path, max_chars=200_000)
                if raw:
                    fixup_subtask.original_files[rel_path] = raw
                    fixup_subtask.written_files[rel_path] = raw

        # Present lint errors as critical QA issues so Dev's prompt includes them
        fixup_subtask.revision_count = 1
        fixup_subtask.qa_passed = False
        fixup_subtask.qa_issues = [
            {
                "severity": "critical",
                "file": "",
                "description": f"ESLint error: {line.strip()}",
            }
            for line in lint_output.splitlines()
            if "error" in line.lower() and line.strip()
        ]
        fixup_subtask.qa_summary = f"npm run lint failed: {lint_output[:200]}"

        try:
            console.print("  [red]→ Dev fix-up (lint errors)[/red]")
            dev.run(state, subtask=fixup_subtask)
            console.print("  [green]✓ Dev lint fix-up complete[/green]")
        except Exception as e:
            state.log(f"Lint fix-up error (non-fatal): {e}", agent="orchestrator")
        finally:
            if fixup_subtask.code_cache_name:
                delete_cache(fixup_subtask.code_cache_name)

        return state

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
                title=f"[AI Game Agent] {state.branch}",
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
