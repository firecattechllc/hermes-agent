"""Append-only, project-isolated runtime execution revision storage."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runtime_execution import RuntimeExecutionRecord, RuntimeExecutionState


RUNTIME_EXECUTION_JOURNAL = "runtime-executions.jsonl"


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid runtime execution project_id")
    return value


class RuntimeExecutionJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str
    execution_id: str
    revision: int = Field(..., ge=1)
    record: RuntimeExecutionRecord
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(sequence: int, record: RuntimeExecutionRecord) -> str:
        payload = {
            "journal_sequence": sequence,
            "project_id": record.project_id,
            "execution_id": record.execution_id,
            "revision": record.revision,
            "record": record.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeExecutionJournalRecord":
        if (
            self.project_id != self.record.project_id
            or self.execution_id != self.record.execution_id
            or self.revision != self.record.revision
            or self.checksum != self.calculate_checksum(
                self.journal_sequence, self.record
            )
        ):
            raise ValueError("runtime execution journal association mismatch")
        return self


class RuntimeExecutionStore:
    def __init__(self, root: Path, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("runtime execution capacity must be between 1 and 100000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return self.root / _safe_project_id(project_id) / RUNTIME_EXECUTION_JOURNAL

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

    def create(self, record: RuntimeExecutionRecord) -> RuntimeExecutionRecord:
        if record.revision != 1 or record.state != RuntimeExecutionState.READY:
            raise ValueError("runtime execution creation requires ready revision 1")
        with self._write_lock(record.project_id):
            records = self._read_unlocked(record.project_id, recover_torn_tail=True)
            matches = tuple(
                item.record for item in records
                if item.execution_id == record.execution_id
                or item.record.dispatch_id == record.dispatch_id
            )
            if matches:
                if len(matches) == 1 and matches[0] == record:
                    return record
                raise ValueError("runtime execution identity collision")
            return self._append_unlocked(records, record)

    def append(
        self, record: RuntimeExecutionRecord, *, expected_revision: int
    ) -> RuntimeExecutionRecord:
        with self._write_lock(record.project_id):
            records = self._read_unlocked(record.project_id, recover_torn_tail=True)
            history = tuple(
                item.record for item in records if item.execution_id == record.execution_id
            )
            if not history:
                raise KeyError(record.execution_id)
            current = history[-1]
            if current.revision != expected_revision:
                raise ValueError("runtime execution revision conflict")
            if record.revision != expected_revision + 1:
                raise ValueError("runtime execution revision must be contiguous")
            self._validate_transition(current, record)
            return self._append_unlocked(records, record)

    def get(
        self, project_id: str, execution_id: str
    ) -> Optional[RuntimeExecutionRecord]:
        history = tuple(
            item.record for item in self._read(project_id)
            if item.execution_id == execution_id.strip()
        )
        return history[-1] if history else None

    def history(
        self, project_id: str, execution_id: str
    ) -> Tuple[RuntimeExecutionRecord, ...]:
        return tuple(
            item.record for item in self._read(project_id)
            if item.execution_id == execution_id.strip()
        )

    def find_by_dispatch(
        self, project_id: str, dispatch_id: str
    ) -> Optional[RuntimeExecutionRecord]:
        matches = tuple(
            item for item in self.list(project_id)
            if item.dispatch_id == dispatch_id.strip()
        )
        if len(matches) > 1:
            raise ValueError("multiple runtime executions for one dispatch")
        return matches[0] if matches else None

    def list(
        self, project_id: str, *, state: Optional[RuntimeExecutionState] = None
    ) -> Tuple[RuntimeExecutionRecord, ...]:
        latest: dict[str, RuntimeExecutionRecord] = {}
        for item in self._read(project_id):
            latest[item.execution_id] = item.record
        records = tuple(latest[key] for key in sorted(latest))
        if state is not None:
            records = tuple(item for item in records if item.state == state)
        return records

    @staticmethod
    def _validate_transition(
        previous: RuntimeExecutionRecord, current: RuntimeExecutionRecord
    ) -> None:
        immutable = {
            key for key in type(previous).model_fields
            if key not in {
                "revision", "state", "causation_id", "updated_at",
                "started_at", "completed_at", "last_heartbeat_at", "reason",
                "result", "session", "evidence_refs",
            }
        }
        if any(getattr(previous, key) != getattr(current, key) for key in immutable):
            raise ValueError("runtime execution authority changed across revisions")
        allowed = {
            RuntimeExecutionState.READY: {RuntimeExecutionState.RUNNING},
            RuntimeExecutionState.RUNNING: {
                RuntimeExecutionState.RUNNING, RuntimeExecutionState.SUCCEEDED,
                RuntimeExecutionState.FAILED, RuntimeExecutionState.CANCELLED,
                RuntimeExecutionState.BLOCKED, RuntimeExecutionState.POLICY_DENIED,
            },
        }
        if previous.state not in allowed or current.state not in allowed[previous.state]:
            raise ValueError("illegal runtime execution lifecycle transition")
        if current.updated_at < previous.updated_at:
            raise ValueError("runtime execution timestamp regressed")
        if current.causation_id != previous.fingerprint:
            raise ValueError("runtime execution causation chain mismatch")
        if previous.evidence_refs != current.evidence_refs[:len(previous.evidence_refs)]:
            raise ValueError("runtime execution evidence history changed")

    def _append_unlocked(self, records, record):
        if len(records) >= self.capacity:
            raise OverflowError("runtime execution capacity reached")
        sequence = len(records) + 1
        journal = RuntimeExecutionJournalRecord(
            journal_sequence=sequence, project_id=record.project_id,
            execution_id=record.execution_id, revision=record.revision,
            record=record,
            checksum=RuntimeExecutionJournalRecord.calculate_checksum(sequence, record),
        )
        line = json.dumps(
            journal.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        path = self.journal_path(record.project_id)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.chmod(path, 0o600)
            remaining = memoryview((line + "\n").encode())
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("runtime execution journal write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def _read(self, project_id: str) -> Tuple[RuntimeExecutionJournalRecord, ...]:
        with self._write_lock(project_id):
            return self._read_unlocked(project_id, recover_torn_tail=True)

    def _read_unlocked(
        self, project_id: str, *, recover_torn_tail: bool = False
    ) -> Tuple[RuntimeExecutionJournalRecord, ...]:
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if recover_torn_tail:
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
            raise ValueError("corrupt runtime execution journal encoding") from exc
        records = []
        previous_by_execution: dict[str, RuntimeExecutionRecord] = {}
        for line_number, line in enumerate(lines, 1):
            try:
                journal = RuntimeExecutionJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt runtime execution journal line {line_number}"
                ) from exc
            if (
                journal.journal_sequence != line_number
                or journal.project_id != _safe_project_id(project_id)
            ):
                raise ValueError("runtime execution journal sequence or project mismatch")
            previous = previous_by_execution.get(journal.execution_id)
            if previous is None:
                if journal.revision != 1:
                    raise ValueError("runtime execution history does not begin at revision 1")
            else:
                if journal.revision != previous.revision + 1:
                    raise ValueError("runtime execution revision history is not contiguous")
                self._validate_transition(previous, journal.record)
            previous_by_execution[journal.execution_id] = journal.record
            records.append(journal)
        return tuple(records)
