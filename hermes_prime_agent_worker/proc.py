"""Single choke point for every ``prime-agent`` subprocess invocation.

Argv-only (never ``shell=True``), a minimal explicit environment allowlist
(no credential, no full host environment inheritance), a hard wall-clock
timeout enforced by this module regardless of what Prime Agent's own
``--autonomous-timeout-ms`` does, and bounded output capture. Mirrors
``hermes_docs_worker.proc``'s role in that package.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from hermes_prime_agent_worker.config import (
    ALLOWED_EXECUTABLE_BASENAME,
    PrimeAgentWorkerConfig,
)

_GRACE_PERIOD_SECONDS = 5.0


class ProcInvocationError(ValueError):
    """Raised before a subprocess is ever started -- an argv or environment
    precondition was violated."""


@dataclass(frozen=True, slots=True)
class ProcResult:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_seconds: float


def build_environment(config: PrimeAgentWorkerConfig) -> Mapping[str, str]:
    """Explicit allowlist. Prime Agent receives no SSH keys, no cloud
    credentials, no repo secrets, and none of the host's ambient
    environment beyond what it needs to run its own private Node runtime
    and locate its own state."""
    return {
        "PATH": f"{config.node_bin_dir}:/usr/bin:/bin",
        "HOME": str(config.home_dir),
        "XDG_DATA_HOME": str(config.xdg_data_home),
        "USER": "hermes-prime-agent",
        "LANG": "C.UTF-8",
        "PRIME_AGENT_INSTALLER_PLAIN": "1",
    }


def _assert_argv_safe(config: PrimeAgentWorkerConfig, argv: Sequence[str]) -> None:
    if not argv:
        raise ProcInvocationError("argv must not be empty")
    if argv[0] != str(config.executable):
        raise ProcInvocationError(
            f"argv[0] must be exactly the configured executable path "
            f"({config.executable}), got {argv[0]!r}"
        )
    if Path(argv[0]).name != ALLOWED_EXECUTABLE_BASENAME:
        raise ProcInvocationError(
            f"refusing to execute a binary not named {ALLOWED_EXECUTABLE_BASENAME!r}"
        )
    for arg in argv:
        if "\n" in arg or "\r" in arg:
            raise ProcInvocationError("argv entries must not contain newlines")


def run_bounded(
    config: PrimeAgentWorkerConfig,
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: Optional[float] = None,
) -> ProcResult:
    """Run ``argv`` (must start with the configured Prime Agent executable)
    with a hard wall-clock bound, minimal environment, and bounded output
    capture. Never uses a shell. On timeout, sends SIGTERM to the whole
    process group, waits a grace period, then SIGKILL."""

    _assert_argv_safe(config, argv)
    if not cwd.is_absolute():
        raise ProcInvocationError("cwd must be an absolute path")

    bound = (
        timeout_seconds
        if timeout_seconds is not None
        else float(config.timeout_seconds)
    )
    bound = min(bound, float(config.timeout_seconds))

    env = dict(build_environment(config))
    started = time.monotonic()

    process = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=bound)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        try:
            stdout, stderr = process.communicate(timeout=_GRACE_PERIOD_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            stdout, stderr = process.communicate(timeout=_GRACE_PERIOD_SECONDS)

    duration = time.monotonic() - started

    truncated = False
    max_bytes = config.max_output_bytes
    if len(stdout.encode("utf-8", errors="replace")) > max_bytes:
        stdout = stdout.encode("utf-8", errors="replace")[:max_bytes].decode(
            "utf-8", errors="replace"
        )
        truncated = True
    if len(stderr.encode("utf-8", errors="replace")) > max_bytes:
        stderr = stderr.encode("utf-8", errors="replace")[:max_bytes].decode(
            "utf-8", errors="replace"
        )
        truncated = True

    return ProcResult(
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        truncated=truncated,
        duration_seconds=duration,
    )


def _terminate_process_group(process: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _kill_process_group(process: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
