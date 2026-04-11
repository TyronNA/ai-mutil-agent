"""Git tools: checkout branch, commit, push, create GitHub PR."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from github import Github, GithubException

_TOKEN_RE = re.compile(r"https://[^@\s]+@")


def _mask(text: str) -> str:
    """Redact any https://TOKEN@ patterns to avoid leaking credentials in errors."""
    return _TOKEN_RE.sub("https://***@", text)


def _run(cmd: list[str], cwd: str, timeout: int = 60) -> str:
    """Run a git command, raise RuntimeError on failure."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        cmd_str = _mask(" ".join(cmd))
        stderr = _mask(result.stderr.strip())
        raise RuntimeError(f"Git error ({cmd_str}): {stderr}")
    return result.stdout.strip()


def git_checkout_branch(project_dir: str, branch: str, base: str = "main") -> None:
    """
    Fetch latest base branch and checkout a new branch from it.
    If branch already exists locally, just switch to it.
    """
    # Try to pull latest base
    try:
        _run(["git", "fetch", "origin", base], cwd=project_dir)
        _run(["git", "checkout", base], cwd=project_dir)
        _run(["git", "pull", "origin", base, "--ff-only"], cwd=project_dir)
    except RuntimeError:
        pass  # no remote yet, or offline — work with local HEAD

    # Create or switch to feature branch
    try:
        _run(["git", "checkout", "-b", branch], cwd=project_dir)
    except RuntimeError:
        _run(["git", "checkout", branch], cwd=project_dir)


def git_diff(project_dir: str) -> str:
    """Return the current unstaged + staged diff."""
    try:
        staged = _run(["git", "diff", "--staged"], cwd=project_dir)
        unstaged = _run(["git", "diff"], cwd=project_dir)
        return (staged + "\n" + unstaged).strip()
    except RuntimeError:
        return ""


def git_commit_all(project_dir: str, message: str) -> str:
    """Stage all changes and commit. Returns the commit SHA."""
    _run(["git", "add", "-A"], cwd=project_dir)

    # Check if there's actually anything to commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_dir, capture_output=True, text=True, timeout=30
    )
    if not status.stdout.strip():
        # Nothing new — return current HEAD SHA
        return _run(["git", "rev-parse", "HEAD"], cwd=project_dir)

    _run(["git", "commit", "-m", message], cwd=project_dir)
    return _run(["git", "rev-parse", "HEAD"], cwd=project_dir)


def git_push_branch(project_dir: str, branch: str, github_token: str, repo_full_name: str) -> None:
    """Set remote (with auth token) and force-push the branch."""
    remote_url = f"https://{github_token}@github.com/{repo_full_name}.git"
    # Remove and re-add origin to ensure token is in URL
    try:
        _run(["git", "remote", "remove", "origin"], cwd=project_dir)
    except RuntimeError:
        pass
    _run(["git", "remote", "add", "origin", remote_url], cwd=project_dir)
    _run(["git", "push", "-u", "origin", branch, "--force"], cwd=project_dir)


def create_github_pr(
    repo_full_name: str,
    github_token: str,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
) -> str:
    """Create a GitHub Pull Request. Returns the PR URL."""
    g = Github(github_token)
    repo = g.get_repo(repo_full_name)

    # Fall back to default branch if base doesn't exist
    try:
        repo.get_branch(base)
    except GithubException:
        base = repo.default_branch

    try:
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
        return pr.html_url
    except GithubException as e:
        if e.status == 422:
            # PR already exists — find and return it
            pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
            for p in pulls:
                return p.html_url
        raise
