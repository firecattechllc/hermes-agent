"""Append-only, project-isolated workflow dispatch outcome persistence."""

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

from .workflow_dispatch import WorkflowDispatchOutcome, WorkflowDispatchStatus


WORKFLOW_DISPATCH_JOURNAL = "workflow-dispatch-outcomes.jsonl"


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid workflow dispatch project_id")
    return value


class WorkflowDispatchJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str
    dispatch_id: str
    outcome: WorkflowDispatchOutcome
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        sequence: int, project_id: str, dispatch_id: str,
        outcome: WorkflowDispatchOutcome,
    ) -> str:
        payload = {
            "journal_sequence": sequence, "project_id": project_id,
            "dispatch_id": dispatch_id,
            "outcome": outcome.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "WorkflowDispatchJournalRecord":
        if (
            self.project_id != self.outcome.project_id
            or self.dispatch_id != self.outcome.dispatch_id
        ):
            raise ValueError("workflow dispatch journal association mismatch")
        if self.checksum != self.calculate_checksum(
            self.journal_sequence, self.project_id, self.dispatch_id, self.outcome
        ):
            raise ValueError("workflow dispatch journal checksum mismatch")
        return self


class WorkflowDispatchStore:
    def __init__(self, root: Path, *, capacity: int = 256) -> None:
        if capacity < 1 or capacity > 10_000:
            raise ValueError("workflow dispatch capacity must be between 1 and 10000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return self.root / _safe_project_id(project_id) / WORKFLOW_DISPATCH_JOURNAL

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

    def append(self, outcome: WorkflowDispatchOutcome) -> WorkflowDispatchOutcome:
        with self._write_lock(outcome.project_id):
            records = self._read_unlocked(outcome.project_id, recover_torn_tail=True)
            for record in records:
                if (
                    record.outcome.intent_id == outcome.intent_id
                    and record.dispatch_id != outcome.dispatch_id
                ):
                    raise ValueError("workflow dispatch intent collision")
                if record.dispatch_id != outcome.dispatch_id:
                    continue
                if record.outcome != outcome:
                    raise ValueError("workflow dispatch ID collision")
                return record.outcome
            if len(records) >= self.capacity:
                raise OverflowError("workflow dispatch capacity reached")
            sequence = len(records) + 1
            record = WorkflowDispatchJournalRecord(
                journal_sequence=sequence, project_id=outcome.project_id,
                dispatch_id=outcome.dispatch_id, outcome=outcome,
                checksum=WorkflowDispatchJournalRecord.calculate_checksum(
                    sequence, outcome.project_id, outcome.dispatch_id, outcome
                ),
            )
            line = json.dumps(
                record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            path = self.journal_path(outcome.project_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.chmod(path, 0o600)
                remaining = memoryview((line + "\n").encode())
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("workflow dispatch journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return outcome

    def get(
        self, project_id: str, dispatch_id: str
    ) -> Optional[WorkflowDispatchOutcome]:
        dispatch_id = dispatch_id.strip()
        return next(
            (record.outcome for record in self._read(project_id)
             if record.dispatch_id == dispatch_id), None
        )

    def find_by_intent(
        self, project_id: str, intent_id: str
    ) -> Optional[WorkflowDispatchOutcome]:
        intent_id = intent_id.strip()
        matches = tuple(
            record.outcome for record in self._read(project_id)
            if record.outcome.intent_id == intent_id
        )
        if len(matches) > 1:
            raise ValueError("multiple workflow dispatch outcomes for one intent")
        return matches[0] if matches else None

    def list(
        self, project_id: str, *, status: Optional[WorkflowDispatchStatus] = None
    ) -> Tuple[WorkflowDispatchOutcome, ...]:
        outcomes = tuple(record.outcome for record in self._read(project_id))
        if status is not None:
            outcomes = tuple(item for item in outcomes if item.status == status)
        return tuple(sorted(outcomes, key=lambda item: item.dispatch_id))

    def _read(self, project_id: str) -> Tuple[WorkflowDispatchJournalRecord, ...]:
        with self._write_lock(project_id):
            return self._read_unlocked(project_id)

    def _read_unlocked(
        self, project_id: str, *, recover_torn_tail: bool = False
    ) -> Tuple[WorkflowDispatchJournalRecord, ...]:
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
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("corrupt workflow dispatch journal encoding") from exc
        records = []
        for line_number, line in enumerate(
            text.splitlines(), 1
        ):
            try:
                record = WorkflowDispatchJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt workflow dispatch journal line {line_number}"
                ) from exc
            if (
                record.journal_sequence != line_number
                or record.project_id != _safe_project_id(project_id)
            ):
                raise ValueError("workflow dispatch journal sequence or project mismatch")
            records.append(record)
        return tuple(records)
