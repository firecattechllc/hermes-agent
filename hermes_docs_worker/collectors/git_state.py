"""Git repository state and relevant recent commits for the Hermes source
checkout on Titan.

Read-only ``git`` calls only (``rev-parse``, ``status --porcelain``,
``log``, ``branch --show-current``) -- no fetch, no checkout, no write of
any kind against the Hermes source tree. Values read directly from the
``.git`` directory are a live, currently-observed signal, so
``Verified`` is appropriate here (unlike a collector that only inspects
source files).
"""

from __future__ import annotations

import time
from typing import Tuple

from hermes_docs_worker.config import DocsWorkerConfig
from hermes_docs_worker.evidence import EvidenceFact, make_fact
from hermes_docs_worker.proc import run_argv
from hermes_docs_worker.status import StatusValue

SOURCE = "git_state"
_LOG_COMMIT_LIMIT = 5


def collect(config: DocsWorkerConfig, *, now: int | None = None) -> Tuple[EvidenceFact, ...]:
    observed_at = now if now is not None else int(time.time())
    repo_path = config.hermes_source_dir

    if not repo_path.exists():
        return (
            make_fact(
                category="git_state", label="repository", status=StatusValue.UNKNOWN,
                detail="Hermes source directory does not exist", source=SOURCE,
                collected_at=observed_at,
            ),
        )

    inside = run_argv(
        ("git", "rev-parse", "--is-inside-work-tree"), cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return (
            make_fact(
                category="git_state", label="repository", status=StatusValue.UNKNOWN,
                detail="Hermes source directory is not a git repository", source=SOURCE,
                collected_at=observed_at,
            ),
        )

    facts: list[EvidenceFact] = []

    head = run_argv(
        ("git", "rev-parse", "--short", "HEAD"), cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    branch = run_argv(
        ("git", "branch", "--show-current"), cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    dirty = run_argv(
        ("git", "status", "--porcelain"), cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )

    head_sha = head.stdout.strip() if head.returncode == 0 else "unknown"
    branch_name = branch.stdout.strip() if branch.returncode == 0 else "unknown"
    is_dirty = bool(dirty.stdout.strip()) if dirty.returncode == 0 else None

    detail = f"HEAD={head_sha} branch={branch_name} dirty={is_dirty}"
    facts.append(
        make_fact(
            category="git_state", label="repository", status=StatusValue.VERIFIED,
            detail=detail, source=SOURCE, collected_at=observed_at,
        )
    )

    log = run_argv(
        ("git", "log", f"-{_LOG_COMMIT_LIMIT}", "--pretty=format:%h %s"), cwd=repo_path,
        timeout=config.max_subprocess_seconds,
    )
    if log.returncode == 0 and log.stdout.strip():
        subjects = log.stdout.strip().splitlines()
        facts.append(
            make_fact(
                category="git_state", label="recent_commits", status=StatusValue.VERIFIED,
                detail="; ".join(subjects)[:1024], source=SOURCE, collected_at=observed_at,
            )
        )

    return tuple(facts)
