"""Resource budgets and timeouts sized for a Raspberry Pi 5.

Every ceiling here is enforced *before* a mutating action (a commit, a
push, a PR create) rather than after -- a run that would exceed a budget
aborts the mutation and still records why, but never partially applies it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from hermes_docs_worker.config import DocsWorkerConfig


class BudgetExceededError(RuntimeError):
    """A run would exceed a configured resource, diff-size, file-count, or
    PR-frequency budget. Fail closed: the caller must not proceed with the
    mutating action this budget guards."""


@dataclass(frozen=True, slots=True)
class DiffStats:
    files_changed: int
    total_bytes_changed: int


def check_diff_budget(stats: DiffStats, config: DocsWorkerConfig) -> None:
    if stats.files_changed > config.max_files_changed:
        raise BudgetExceededError(
            f"generated change touches {stats.files_changed} files, exceeding the "
            f"configured maximum of {config.max_files_changed}"
        )
    if stats.total_bytes_changed > config.max_diff_bytes:
        raise BudgetExceededError(
            f"generated change is {stats.total_bytes_changed} bytes, exceeding the "
            f"configured maximum of {config.max_diff_bytes}"
        )


def check_pr_frequency(
    *, last_pr_opened_at: Optional[float], now: float, config: DocsWorkerConfig
) -> None:
    """Refuse to open a new PR sooner than ``min_pr_interval_seconds`` after
    the previous one this worker opened."""
    if last_pr_opened_at is None:
        return
    elapsed = now - last_pr_opened_at
    if elapsed < config.min_pr_interval_seconds:
        raise BudgetExceededError(
            f"last automation PR was opened {elapsed:.0f}s ago, below the configured "
            f"minimum interval of {config.min_pr_interval_seconds}s"
        )


class RunDeadline:
    """A wall-clock deadline for one worker run.

    Every long-running phase (collection, generation, git/PR operations)
    should check :meth:`remaining` and pass it as a subprocess/HTTP
    timeout, and the orchestrator should call :meth:`ensure_not_expired`
    between phases so a stuck collector cannot silently consume the whole
    of ``max_run_seconds`` and beyond.
    """

    def __init__(self, max_run_seconds: float, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._start = clock()
        self._deadline = self._start + max_run_seconds

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def ensure_not_expired(self) -> None:
        if self.remaining() <= 0:
            raise BudgetExceededError("worker run exceeded its max_run_seconds budget")

    def bounded_timeout(self, requested: float) -> float:
        """The smaller of ``requested`` and what's left on the deadline,
        never less than a small positive floor so a caller always gets a
        usable timeout rather than zero."""
        return max(0.05, min(requested, self.remaining()))


def diff_stats_for_paths(paths_to_content: dict[Path, str]) -> DiffStats:
    """Conservative diff-size estimate from generated file contents (used
    before anything is staged in git, so the estimate is "bytes of new
    content," not a true unified-diff byte count)."""
    total_bytes = sum(len(content.encode("utf-8")) for content in paths_to_content.values())
    return DiffStats(files_changed=len(paths_to_content), total_bytes_changed=total_bytes)
