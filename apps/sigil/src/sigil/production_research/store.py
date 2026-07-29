"""Checksummed atomic state for production research and shadow outcomes."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import checksum

SCHEMA_VERSION = 1


def now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def initial_state() -> dict[str, Any]:
    now = now_text()
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 1,
        "environment": "paper",
        "live_execution": False,
        "shadow_mode": True,
        "paper_promotion_approved": False,
        "strategy": {},
        "progress": {
            "state": "collecting_market_data",
            "current_batch": 0,
            "current_cursor": 0,
            "symbols_researched": 0,
            "research_successes": 0,
            "research_failures": 0,
            "scored_count": 0,
            "hard_rejected_count": 0,
            "evidence_complete_count": 0,
            "evidence_incomplete_count": 0,
            "evidence_completeness": "0",
            "candidates_produced": 0,
            "proposals_generated": 0,
            "leading_rejection_reasons": {},
            "last_completed_research": None,
            "provider_status": "unconfigured",
            "market_data_freshness": "unavailable",
            "next_cycle_at": None,
        },
        "research_results": [],
        "candidates": [],
        "proposals": [],
        "shadow_positions": [],
        "shadow_outcomes": [],
        "validation_reports": [],
        "safety_defects": [],
        "audit": [
            {
                "audit_id": "SIGIL-V21-AUD-00000001",
                "evidence_id": "SIGIL-V21-SHADOW-DEFAULT",
                "event": "shadow_mode_installed_enabled",
                "timestamp": now,
            }
        ],
    }


class ProductionResearchStore:
    def __init__(self, state_directory: Path) -> None:
        if not state_directory.is_absolute() or state_directory.is_symlink():
            raise ValueError("production research state directory must be absolute")
        self.directory = state_directory / "production-research-v2.1"
        self.path = self.directory / "state.json"
        self.lock_path = self.directory / "state.lock"

    @contextmanager
    def locked(self) -> Iterator[dict[str, Any]]:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise RuntimeError("production research state cannot use symlinks")
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield self._load()

    def load(self) -> dict[str, Any]:
        with self.locked() as state:
            return json.loads(json.dumps(state))

    def save(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        state["generated_at"] = now_text()
        core = {
            "schema_version": SCHEMA_VERSION,
            "environment": "paper",
            "payload": state,
        }
        envelope = {**core, "sha256": checksum(core)}
        descriptor, temporary = tempfile.mkstemp(prefix=".production-research.", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(envelope, output, sort_keys=True, separators=(",", ":"))
                output.write("\n")
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

    def _load(self) -> dict[str, Any]:
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
                envelope["sha256"] != checksum(core)
                or core["schema_version"] != SCHEMA_VERSION
                or core["environment"] != "paper"
                or core["payload"]["live_execution"] is not False
                or not isinstance(core["payload"]["shadow_mode"], bool)
            ):
                raise RuntimeError("production research state integrity failed")
            return core["payload"]
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            raise RuntimeError("production research state integrity failed") from None
