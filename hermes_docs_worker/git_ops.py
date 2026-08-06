"""Git branch/commit/push for the automation branch only.

This module structurally cannot merge, push to ``main``, delete a remote
branch, or tag a release: there is no function here that runs ``git
merge`` against anything but a local fast-forward in
:mod:`hermes_docs_worker.repo_sync`, no function that runs ``git push`` with
``--force``, ``--delete``, or a ref other than the automation branch it
just created, and no function that runs ``git tag``. These are not runtime
checks layered on top of a general-purpose git wrapper -- the capability is
simply absent from the module's surface.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Sequence

from hermes_docs_worker.config import (
    AUTOMATION_BRANCH_PREFIX,
    COMMIT_MESSAGE_PREFIX,
    DocsWorkerConfig,
)
from hermes_docs_worker.proc import run_argv


class GitOpsError(RuntimeError):
    """A git branch/commit/push operation failed or was refused."""


def branch_name(*, now: Optional[float] = None) -> str:
    """``automation/titan-docs-YYYYMMDD-HHMM``."""
    ts = now if now is not None else time.time()
    return AUTOMATION_BRANCH_PREFIX + time.strftime("%Y%m%d-%H%M", time.gmtime(ts))


def commit_message(*, now: Optional[float] = None) -> str:
    """``Update Titan fleet evidence YYYY-MM-DD``."""
    ts = now if now is not None else time.time()
    return COMMIT_MESSAGE_PREFIX + time.strftime("%Y-%m-%d", time.gmtime(ts))


def _run(config: DocsWorkerConfig, args: Sequence[str]) -> str:
    result = run_argv(
        ("git", *args), cwd=config.docs_repo_path, timeout=config.max_subprocess_seconds
    )
    if result.returncode != 0:
        raise GitOpsError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def create_automation_branch(config: DocsWorkerConfig, branch: str) -> None:
    if not branch.startswith(AUTOMATION_BRANCH_PREFIX):
        raise GitOpsError(
            f"refusing to create branch {branch!r}: must start with "
            f"{AUTOMATION_BRANCH_PREFIX!r}"
        )
    _run(config, ["checkout", "-b", branch])


def return_to_main(config: DocsWorkerConfig) -> None:
    """Check the local documentation checkout back out onto ``main``.

    Called at the end of every run that created an automation branch
    (success or failure) so the *next* scheduled run's
    :func:`hermes_docs_worker.repo_sync.ensure_repo_synced` guard -- which
    requires the checkout to already be on ``main`` -- doesn't fail closed
    just because this run left the working tree on
    ``automation/titan-docs-...``. Never touches ``main``'s content, only
    which branch is checked out.
    """
    _run(config, ["checkout", config.main_branch])


def stage_and_commit(
    config: DocsWorkerConfig, relative_paths: Sequence[Path], message: str
) -> Optional[str]:
    """Stage exactly ``relative_paths`` (never ``git add -A``) and commit.

    Returns the new commit SHA, or ``None`` if there was nothing to commit
    (idempotency: the caller is expected to have already confirmed the
    generated content differs from what's on disk, but this is the last,
    authoritative check -- an empty ``git diff --cached`` after staging
    means no commit is made).
    """
    if not relative_paths:
        return None
    _run(config, ["add", "--", *[str(p) for p in relative_paths]])
    staged = _run(config, ["diff", "--cached", "--name-only"])
    if not staged.strip():
        return None
    _run(
        config,
        [
            "-c",
            f"user.name={config.git_user_name}",
            "-c",
            f"user.email={config.git_user_email}",
            "commit",
            "-m",
            message,
        ],
    )
    return _run(config, ["rev-parse", "HEAD"]).strip()


def push_automation_branch(config: DocsWorkerConfig, branch: str) -> None:
    """Push ``branch`` to the configured remote. Refuses anything that
    isn't the automation branch, and never passes ``--force``, ``--delete``,
    or a tag refspec."""
    if not branch.startswith(AUTOMATION_BRANCH_PREFIX):
        raise GitOpsError(f"refusing to push non-automation branch {branch!r}")
    if branch in (config.main_branch, "main", "master"):
        raise GitOpsError("refusing to push to a trunk branch")
    _run(config, ["push", config.git_remote_name, f"{branch}:{branch}"])
