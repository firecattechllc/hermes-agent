from __future__ import annotations

from pathlib import Path

import pytest

from hermes_docs_worker.budgets import (
    BudgetExceededError,
    DiffStats,
    RunDeadline,
    check_diff_budget,
    check_pr_frequency,
    diff_stats_for_paths,
)


def test_check_diff_budget_rejects_too_many_files(worker_config) -> None:
    stats = DiffStats(files_changed=worker_config.max_files_changed + 1, total_bytes_changed=10)
    with pytest.raises(BudgetExceededError):
        check_diff_budget(stats, worker_config)


def test_check_diff_budget_rejects_too_many_bytes(worker_config) -> None:
    stats = DiffStats(files_changed=1, total_bytes_changed=worker_config.max_diff_bytes + 1)
    with pytest.raises(BudgetExceededError):
        check_diff_budget(stats, worker_config)


def test_check_diff_budget_allows_within_limits(worker_config) -> None:
    check_diff_budget(DiffStats(files_changed=1, total_bytes_changed=10), worker_config)


def test_check_pr_frequency_allows_first_pr(worker_config) -> None:
    check_pr_frequency(last_pr_opened_at=None, now=1000.0, config=worker_config)


def test_check_pr_frequency_rejects_too_soon(worker_config) -> None:
    object.__setattr__(worker_config, "min_pr_interval_seconds", 3600)
    with pytest.raises(BudgetExceededError):
        check_pr_frequency(last_pr_opened_at=1000.0, now=1001.0, config=worker_config)


def test_check_pr_frequency_allows_after_interval(worker_config) -> None:
    object.__setattr__(worker_config, "min_pr_interval_seconds", 3600)
    check_pr_frequency(last_pr_opened_at=1000.0, now=1000.0 + 3601, config=worker_config)


def test_run_deadline_expires() -> None:
    clock = iter([0.0, 0.0, 10.0])
    deadline = RunDeadline(5, clock=lambda: next(clock))
    assert deadline.remaining() == 5.0
    with pytest.raises(BudgetExceededError):
        deadline.ensure_not_expired()


def test_run_deadline_bounded_timeout_never_below_floor() -> None:
    clock = iter([0.0, 100.0])
    deadline = RunDeadline(5, clock=lambda: next(clock))
    assert deadline.bounded_timeout(30) == pytest.approx(0.05)


def test_diff_stats_for_paths() -> None:
    stats = diff_stats_for_paths({Path("a.md"): "hello", Path("b.md"): "world!"})
    assert stats.files_changed == 2
    assert stats.total_bytes_changed == len(b"hello") + len(b"world!")
