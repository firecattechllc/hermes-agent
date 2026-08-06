"""Single-run guard.

Governance requirement: "use a lock so only one run occurs at a time." A
worker run is triggered by three independent systemd timers (evidence,
daily, weekly); without a lock, an hourly evidence-collection run and a
daily consolidated run could interleave writes to the same documentation
checkout. The lock is a non-blocking ``flock`` -- a run that finds the lock
held does not queue or wait, it exits immediately with
:class:`AlreadyRunningError` so an overlapping systemd timer firing produces
a clean, loggable skip rather than a pile-up of blocked processes.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from typing import Iterator


class AlreadyRunningError(RuntimeError):
    """Another worker run currently holds the lock."""


class LockUnavailableError(RuntimeError):
    """The lock file/directory could not be prepared (bad state root,
    symlink, permissions). Fail closed rather than run unlocked."""


@contextlib.contextmanager
def run_lock(lock_path: Path) -> Iterator[None]:
    """Hold the single-run lock for the duration of the ``with`` block.

    Raises :class:`AlreadyRunningError` immediately (never blocks) if
    another process already holds it, and :class:`LockUnavailableError` if
    the lock file cannot be safely opened.
    """
    if lock_path.is_symlink():
        raise LockUnavailableError(f"lock path {lock_path} must not be a symlink")
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_path.parent.is_symlink():
        raise LockUnavailableError(f"lock directory {lock_path.parent} must not be a symlink")

    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError as error:
        raise LockUnavailableError(f"cannot open lock file {lock_path}: {error}") from error

    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AlreadyRunningError(
                f"another Titan documentation worker run already holds {lock_path}"
            ) from error

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()

        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
