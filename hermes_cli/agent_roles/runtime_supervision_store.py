"""Append-only, project-isolated runtime supervision health journal."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator


RUNTIME_SUPERVISION_JOURNAL = "runtime-supervisions.jsonl"

# Maximum staleness tolerance: 10 minutes in seconds
DEFAULT_STALENESS_THRESHOLD_SECONDS = 600


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid runtime supervision project_id")
    return value


class SupervisionStatus(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    DEGRADED = "degraded"
    RECOVERED = "recovered"


class SupervisionJournalRecord(BaseModel):
    """One immutable supervision health event in the append-only journal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str
    execution_id: str
    revision: int = Field(..., ge=1)
    status: SupervisionStatus
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    observed_at: int = Field(..., ge=0)
    last_heartbeat_at: Optional[int] = Field(default=None, ge=0)
    started_at: int = Field(..., ge=0)
    heartbeat_age_seconds: int = Field(..., ge=0)
    heartbeat_threshold_seconds: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=1024)
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        sequence: int,
        project_id: str,
        execution_id: str,
        revision: int,
        status: SupervisionStatus,
        actor_id: str,
        correlation_id: str,
        causation_id: str,
        observed_at: int,
        last_heartbeat_at: Optional[int],
        started_at: int,
        heartbeat_age_seconds: int,
        heartbeat_threshold_seconds: int,
        reason: str,
    ) -> str:
        payload = {
            "journal_sequence": sequence,
            "project_id": _safe_project_id(project_id),
            "execution_id": execution_id,
            "revision": revision,
            "status": status.value,
            "actor_id": actor_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "observed_at": observed_at,
            "last_heartbeat_at": last_heartbeat_at,
            "started_at": started_at,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_threshold_seconds": heartbeat_threshold_seconds,
            "reason": reason,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "SupervisionJournalRecord":
        computed = SupervisionJournalRecord.calculate_checksum(
            self.journal_sequence,
            self.project_id,
            self.execution_id,
            self.revision,
            self.status,
            self.actor_id,
            self.correlation_id,
            self.causation_id,
            self.observed_at,
            self.last_heartbeat_at,
            self.started_at,
            self.heartbeat_age_seconds,
            self.heartbeat_threshold_seconds,
            self.reason,
        )
        if self.checksum != computed:
            raise ValueError("runtime supervision journal checksum mismatch")
        return self


class RuntimeSupervisionStore:
    """Append-only supervision health journal with torn-tail recovery."""

    def __init__(self, root: Path, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("runtime supervision capacity must be between 1 and 100000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return self.root / _safe_project_id(project_id) / RUNTIME_SUPERVISION_JOURNAL

    @contextmanager
    def _write_lock(self, project_id: str) -> Iterator[None]:
        path = self.journal_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        lock_path = path.with_suffix(".lock")
        with self._thread_lock:
            with lock_path.open("a+", encoding="utf-8") as handle:
                os.chmod(lock_path, 0o600)
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def observe(
        self,
        project_id: str,
        execution_id: str,
        status: SupervisionStatus,
        actor_id: str,
        correlation_id: str,
        causation_id: str,
        observed_at: int,
        last_heartbeat_at: Optional[int],
        started_at: int,
        heartbeat_threshold_seconds: int,
        reason: str,
    ) -> SupervisionJournalRecord:
        """Record one supervision observation, append-only and idempotent."""
        if last_heartbeat_at is not None and last_heartbeat_at < started_at:
            raise ValueError("heartbeat timestamp is before execution start")
        if last_heartbeat_at is not None and observed_at < last_heartbeat_at:
            raise ValueError("observed_at cannot precede last_heartbeat_at")
        heartbeat_age = 0
        if last_heartbeat_at is not None:
            heartbeat_age = observed_at - last_heartbeat_at
        elif started_at is not None:
            heartbeat_age = observed_at - started_at

        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            existing = tuple(
                item for item in records if item.execution_id == execution_id
            )
            next_revision = (existing[-1].revision + 1) if existing else 1
            sequence = len(records) + 1
            if sequence > self.capacity:
                raise OverflowError("runtime supervision capacity reached")

            computed_checksum = SupervisionJournalRecord.calculate_checksum(
                sequence,
                project_id,
                execution_id,
                next_revision,
                status,
                actor_id,
                correlation_id,
                causation_id,
                observed_at,
                last_heartbeat_at,
                started_at,
                heartbeat_age,
                heartbeat_threshold_seconds,
                reason,
            )
            record = SupervisionJournalRecord(
                journal_sequence=sequence,
                project_id=project_id,
                execution_id=execution_id,
                revision=next_revision,
                status=status,
                actor_id=actor_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                observed_at=observed_at,
                last_heartbeat_at=last_heartbeat_at,
                started_at=started_at,
                heartbeat_age_seconds=heartbeat_age,
                heartbeat_threshold_seconds=heartbeat_threshold_seconds,
                reason=reason,
                checksum=computed_checksum,
            )

            line = json.dumps(
                record.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            path = self.journal_path(project_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.chmod(path, 0o600)
                remaining = memoryview((line + "\n").encode())
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("runtime supervision journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return record

    def get_latest(
        self, project_id: str, execution_id: str
    ) -> Optional[SupervisionJournalRecord]:
        """Return the most recent supervision record for an execution, or None."""
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            matching = tuple(
                item for item in records if item.execution_id == execution_id
            )
            return matching[-1] if matching else None

    def history(
        self, project_id: str, execution_id: str
    ) -> Tuple[SupervisionJournalRecord, ...]:
        """Return all supervision records for an execution."""
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            return tuple(
                item for item in records if item.execution_id == execution_id
            )

    def list_executions(
        self, project_id: str, *, status: Optional[SupervisionStatus] = None
    ) -> Tuple[SupervisionJournalRecord, ...]:
        """Return the latest supervision record per execution, optionally filtered."""
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            latest: dict[str, SupervisionJournalRecord] = {}
            for item in records:
                existing = latest.get(item.execution_id)
                if existing is None or item.revision > existing.revision:
                    latest[item.execution_id] = item
            result = tuple(latest.values())
            if status is not None:
                result = tuple(item for item in result if item.status == status)
            return result

    def _read_unlocked(
        self, project_id: str, *, recover_torn_tail: bool = False
    ) -> Tuple[SupervisionJournalRecord, ...]:
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if recover_torn_tail and boundary > 0:
                fd = os.open(path, os.O_WRONLY)
                try:
                    os.ftruncate(fd, boundary)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            raw = raw[:boundary]
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("corrupt runtime supervision journal encoding") from exc
        records = []
        for line_number, line in enumerate(lines, 1):
            try:
                record = SupervisionJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt runtime supervision journal line {line_number}"
                ) from exc
            if record.journal_sequence != line_number:
                raise ValueError("runtime supervision journal sequence mismatch")
            if record.project_id != _safe_project_id(project_id):
                raise ValueError("runtime supervision project mismatch")
            records.append(record)
        return tuple(records)
