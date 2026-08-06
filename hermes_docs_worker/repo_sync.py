"""Documentation repository synchronization guard.

Fails closed on exactly the conditions the governance contract lists: a
dirty checkout, a diverged local ``main``, or an unreachable remote. GitHub
remains canonical -- this module only ever fast-forwards the local ``main``
to match ``origin/main``; it has no code path that could push, merge into,
or otherwise mutate ``main``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.proc import run_argv


class RepoGuardError(RuntimeError):
    """The documentation checkout is not in a state this worker may safely
    operate on. Fail closed: no collection, generation, or git mutation may
    proceed past this error."""


@dataclass(frozen=True, slots=True)
class RepoSyncStatus:
    branch: str
    head_sha: str


def _git(config: DocsWorkerConfig, args: list[str]) -> str:
    result = run_argv(
        ("git", *args),
        cwd=config.docs_repo_path,
        timeout=config.max_subprocess_seconds,
    )
    if result.returncode != 0:
        raise RepoGuardError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout


def ensure_repo_synced(config: DocsWorkerConfig) -> RepoSyncStatus:
    """Verify the documentation checkout is clean and on an up-to-date,
    non-diverged ``main``, fast-forwarding it from ``origin/main`` if
    necessary. Raises :class:`RepoGuardError` on any dirty, diverged, or
    unreachable condition."""
    repo_path = config.docs_repo_path
    if not repo_path.exists():
        raise RepoGuardError(f"documentation repository path does not exist: {repo_path}")

    inside = run_argv(
        ("git", "rev-parse", "--is-inside-work-tree"),
        cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RepoGuardError(f"{repo_path} is not a git repository")

    status = _git(config, ["status", "--porcelain"])
    if status.strip():
        raise RepoGuardError(
            "documentation repository has uncommitted changes; refusing to run against "
            "a dirty checkout"
        )

    current_branch = _git(config, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if current_branch != config.main_branch:
        raise RepoGuardError(
            f"documentation repository is on {current_branch!r}, expected "
            f"{config.main_branch!r}; refusing to run"
        )

    fetch = run_argv(
        ("git", "fetch", "--quiet", config.git_remote_name, config.main_branch),
        cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    if fetch.returncode != 0:
        raise RepoGuardError(
            f"could not fetch {config.git_remote_name}/{config.main_branch} "
            f"(documentation repository unreachable): {fetch.stderr.strip()}"
        )

    remote_ref = f"{config.git_remote_name}/{config.main_branch}"
    local_sha = _git(config, ["rev-parse", "HEAD"]).strip()
    remote_sha = _git(config, ["rev-parse", remote_ref]).strip()

    if local_sha != remote_sha:
        merge_base = _git(config, ["merge-base", "HEAD", remote_ref]).strip()
        if merge_base != local_sha:
            # Local main has commits the remote doesn't -- a true
            # divergence, not just "behind." Never auto-resolve this.
            raise RepoGuardError(
                f"local {config.main_branch} has diverged from {remote_ref}; refusing to "
                "fast-forward (this requires human intervention)"
            )
        ff = run_argv(
            ("git", "merge", "--ff-only", remote_ref),
            cwd=repo_path,
            timeout=config.max_subprocess_seconds,
        )
        if ff.returncode != 0:
            raise RepoGuardError(
                f"fast-forward merge of {remote_ref} failed: {ff.stderr.strip()}"
            )

    head_sha = _git(config, ["rev-parse", "HEAD"]).strip()
    return RepoSyncStatus(branch=config.main_branch, head_sha=head_sha)
