from __future__ import annotations

import pytest

from hermes_docs_worker.proc import SubprocessGuardError, run_argv


def test_rejects_single_string_argv() -> None:
    with pytest.raises(SubprocessGuardError):
        run_argv("git status", timeout=1)


def test_rejects_unknown_executable() -> None:
    with pytest.raises(SubprocessGuardError):
        run_argv(["rm", "-rf", "/"], timeout=1)


def test_rejects_empty_argv() -> None:
    with pytest.raises(SubprocessGuardError):
        run_argv([], timeout=1)


def test_rejects_non_positive_timeout() -> None:
    with pytest.raises(SubprocessGuardError):
        run_argv(["git", "status"], timeout=0)


def test_runs_allowed_executable() -> None:
    result = run_argv(["git", "--version"], timeout=5)
    assert result.returncode == 0
    assert "git version" in result.stdout
