"""Append-only, checksummed persistence for governed model executions."""

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

from .model_execution import ModelExecutionEvidence


MODEL_EXECUTION_JOURNAL = "model-executions.jsonl"


class ModelExecutionJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    journal_sequence: int = Field(..., ge=1)
    execution_id: str
    idempotency_key: str
    evidence: ModelExecutionEvidence
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(sequence: int, evidence: ModelExecutionEvidence) -> str:
        value = {
            "journal_sequence": sequence, "execution_id": evidence.execution_id,
            "idempotency_key": evidence.idempotency_key,
            "evidence": evidence.model_dump(mode="json"),
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def _consistent(self) -> "ModelExecutionJournalRecord":
        if (
            self.execution_id != self.evidence.execution_id
            or self.idempotency_key != self.evidence.idempotency_key
            or self.checksum != self.calculate_checksum(self.journal_sequence, self.evidence)
        ):
            raise ValueError("model execution journal association mismatch")
        return self


class ModelExecutionStore:
    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("model execution capacity must be between 1 and 100000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    @property
    def journal_path(self) -> Path:
        return self.root / MODEL_EXECUTION_JOURNAL

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        with self._thread_lock, (self.root / ".model-executions.lock").open("a+") as handle:
            os.chmod(handle.name, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get(self, execution_id: str) -> Optional[ModelExecutionEvidence]:
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
        matches = [x.evidence for x in records if x.execution_id == execution_id.strip()]
        return matches[0] if matches else None

    def find_by_idempotency_key(self, key: str) -> Optional[ModelExecutionEvidence]:
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
        matches = [x.evidence for x in records if x.idempotency_key == key.strip()]
        return matches[0] if matches else None

    def list(self) -> Tuple[ModelExecutionEvidence, ...]:
        with self._lock():
            return tuple(x.evidence for x in self._read_unlocked(recover_torn_tail=True))

    def save(self, evidence: ModelExecutionEvidence) -> ModelExecutionEvidence:
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
            matches = [
                x.evidence for x in records
                if x.execution_id == evidence.execution_id
                or x.idempotency_key == evidence.idempotency_key
            ]
            if matches:
                if len(matches) == 1 and matches[0] == evidence:
                    return evidence
                raise ValueError("model execution identity or idempotency collision")
            if len(records) >= self.capacity:
                raise OverflowError("model execution capacity reached")
            sequence = len(records) + 1
            journal = ModelExecutionJournalRecord(
                journal_sequence=sequence, execution_id=evidence.execution_id,
                idempotency_key=evidence.idempotency_key, evidence=evidence,
                checksum=ModelExecutionJournalRecord.calculate_checksum(sequence, evidence),
            )
            line = json.dumps(journal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            fd = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                remaining = memoryview((line + "\n").encode())
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("model execution journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return evidence

    def _read_unlocked(self, *, recover_torn_tail: bool) -> Tuple[ModelExecutionJournalRecord, ...]:
        if not self.journal_path.exists():
            return ()
        raw = self.journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if recover_torn_tail:
                fd = os.open(self.journal_path, os.O_WRONLY)
                try:
                    os.ftruncate(fd, boundary)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            raw = raw[:boundary]
        records = []
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            try:
                item = ModelExecutionJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"corrupt model execution journal line {number}") from exc
            if item.journal_sequence != number:
                raise ValueError("model execution journal sequence mismatch")
            if any(
                prior.execution_id == item.execution_id
                or prior.idempotency_key == item.idempotency_key
                for prior in records
            ):
                raise ValueError("duplicate model execution journal identity")
            records.append(item)
        return tuple(records)
