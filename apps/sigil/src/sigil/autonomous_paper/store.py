"""Checksummed durable state for v2.0 autonomous Alpaca paper execution."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def initial_state() -> dict[str, Any]:
    now = timestamp()
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "generated_at": now,
        "environment": "paper",
        "live_execution": False,
        "broker": "alpaca_paper",
        "broker_submission": False,
        "activated": False,
        "paused": False,
        "kill_switch": True,
        "reconciliation_complete": False,
        "last_reconciled_at": None,
        "policy": {},
        "progress": {
            "scheduler_state": "idle",
            "current_cursor": 0,
            "current_batch": 0,
            "symbols_in_batch": [],
            "symbols_completed_cycle": 0,
            "total_eligible_symbols": 0,
            "coverage_percent": 0.0,
            "last_completed_symbol": None,
            "last_successful_research_at": None,
            "candidates_produced": 0,
            "proposals_produced": 0,
            "proposals_rejected": 0,
            "leading_rejection_reasons": {},
            "next_cycle_at": None,
            "state": "execution_disabled",
        },
        "candidates": [],
        "proposals": [],
        "rejections": [],
        "order_intents": [],
        "orders": [],
        "fills": [],
        "positions": [],
        "exit_plans": [],
        "exit_intents": [],
        "audit": [
            {
                "audit_id": "SIGIL-V2-INSTALL",
                "evidence_id": "SIGIL-V2-PAPER-DISABLED",
                "timestamp": now,
                "event": "paper_execution_installed_disabled",
                "environment": "paper",
                "live_execution": False,
                "broker_submission": False,
            }
        ],
    }


class PaperExecutionStore:
    """Atomic state with a process lock and fail-closed checksum validation."""

    def __init__(self, state_directory: Path) -> None:
        if not state_directory.is_absolute() or state_directory.is_symlink():
            raise ValueError("paper execution state directory must be absolute")
        self.directory = state_directory / "autonomous-paper-v2"
        self.path = self.directory / "state.json"
        self.lock_path = self.directory / "state.lock"

    @contextmanager
    def locked(self) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise RuntimeError("paper execution state files cannot be symlinks")
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            state = self._load_unlocked()
            yield state

    def load(self) -> dict[str, Any]:
        with self.locked() as state:
            return json.loads(json.dumps(state))

    def save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        state["generated_at"] = timestamp()
        envelope_core = {
            "schema_version": SCHEMA_VERSION,
            "environment": "paper",
            "payload": state,
        }
        envelope = {**envelope_core, "sha256": digest(envelope_core)}
        descriptor, temporary = tempfile.mkstemp(prefix=".paper-v2.", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(output.fileno(), 0o600)
                output.write(canonical(envelope))
                output.write(b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return initial_state()
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            core = {
                "schema_version": envelope["schema_version"],
                "environment": envelope["environment"],
                "payload": envelope["payload"],
            }
            if (
                envelope.get("sha256") != digest(core)
                or core["schema_version"] != SCHEMA_VERSION
                or core["environment"] != "paper"
                or core["payload"].get("environment") != "paper"
                or core["payload"].get("live_execution") is not False
            ):
                raise RuntimeError("paper execution state integrity validation failed")
            payload = core["payload"]
            payload.setdefault("exit_plans", [])
            payload.setdefault("exit_intents", [])
            return payload
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            raise RuntimeError("paper execution state integrity validation failed") from None
