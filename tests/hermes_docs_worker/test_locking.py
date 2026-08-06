from __future__ import annotations

from pathlib import Path

import pytest

from hermes_docs_worker.locking import AlreadyRunningError, LockUnavailableError, run_lock


def test_lock_excludes_concurrent_holder(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "run.lock"
    with run_lock(lock_path):
        with pytest.raises(AlreadyRunningError):
            with run_lock(lock_path):
                pass  # pragma: no cover - must never be reached


def test_lock_is_released_after_the_with_block(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "run.lock"
    with run_lock(lock_path):
        pass
    with run_lock(lock_path):
        pass  # a second, sequential acquisition must succeed


def test_lock_rejects_symlink_path(tmp_path: Path) -> None:
    real_target = tmp_path / "real.lock"
    real_target.write_text("", encoding="utf-8")
    symlink_path = tmp_path / "symlink.lock"
    symlink_path.symlink_to(real_target)
    with pytest.raises(LockUnavailableError):
        with run_lock(symlink_path):
            pass  # pragma: no cover
