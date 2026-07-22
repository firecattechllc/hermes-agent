"""Append-only, checksummed Step 28 intelligence evidence persistence."""

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

from .intelligence_engine import IntelligenceEvidence


INTELLIGENCE_JOURNAL = "intelligence-optimizations.jsonl"


class IntelligenceJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    journal_sequence: int = Field(..., ge=1)
    optimization_id: str
    idempotency_key: str
    evidence: IntelligenceEvidence
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(sequence: int, evidence: IntelligenceEvidence) -> str:
        value = {"journal_sequence": sequence, "optimization_id": evidence.optimization_id, "idempotency_key": evidence.idempotency_key, "evidence": evidence.model_dump(mode="json")}
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @model_validator(mode="after")
    def _consistent(self) -> "IntelligenceJournalRecord":
        if self.optimization_id != self.evidence.optimization_id or self.idempotency_key != self.evidence.idempotency_key or self.checksum != self.calculate_checksum(self.journal_sequence, self.evidence):
            raise ValueError("intelligence journal association mismatch")
        return self


class InMemoryIntelligenceStore:
    def __init__(self) -> None:
        self._records: dict[str, IntelligenceEvidence] = {}
        self._keys: dict[str, str] = {}

    def get(self, optimization_id: str) -> Optional[IntelligenceEvidence]:
        return self._records.get(optimization_id)

    def find_by_idempotency_key(self, key: str) -> Optional[IntelligenceEvidence]:
        identity = self._keys.get(key)
        return None if identity is None else self._records[identity]

    def save(self, evidence: IntelligenceEvidence) -> IntelligenceEvidence:
        current = self.get(evidence.optimization_id) or self.find_by_idempotency_key(evidence.idempotency_key)
        if current:
            if current == evidence:
                return current
            raise ValueError("intelligence identity or idempotency collision")
        self._records[evidence.optimization_id] = evidence
        self._keys[evidence.idempotency_key] = evidence.optimization_id
        return evidence


class IntelligenceStore:
    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        if not 1 <= capacity <= 100_000:
            raise ValueError("intelligence capacity must be between 1 and 100000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    @property
    def journal_path(self) -> Path:
        return self.root / INTELLIGENCE_JOURNAL

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        with self._thread_lock, (self.root / ".intelligence-optimizations.lock").open("a+") as handle:
            os.chmod(handle.name, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get(self, optimization_id: str) -> Optional[IntelligenceEvidence]:
        return next((item.evidence for item in self._read() if item.optimization_id == optimization_id.strip()), None)

    def find_by_idempotency_key(self, key: str) -> Optional[IntelligenceEvidence]:
        return next((item.evidence for item in self._read() if item.idempotency_key == key.strip()), None)

    def list(self) -> Tuple[IntelligenceEvidence, ...]:
        return tuple(item.evidence for item in self._read())

    def save(self, evidence: IntelligenceEvidence) -> IntelligenceEvidence:
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
            matches = [item.evidence for item in records if item.optimization_id == evidence.optimization_id or item.idempotency_key == evidence.idempotency_key]
            if matches:
                if len(matches) == 1 and matches[0] == evidence:
                    return evidence
                raise ValueError("intelligence identity or idempotency collision")
            if len(records) >= self.capacity:
                raise OverflowError("intelligence capacity reached")
            sequence = len(records) + 1
            item = IntelligenceJournalRecord(journal_sequence=sequence, optimization_id=evidence.optimization_id, idempotency_key=evidence.idempotency_key, evidence=evidence, checksum=IntelligenceJournalRecord.calculate_checksum(sequence, evidence))
            payload = (json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n").encode()
            fd = os.open(self.journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("intelligence journal write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return evidence

    def _read(self) -> Tuple[IntelligenceJournalRecord, ...]:
        with self._lock():
            return self._read_unlocked(recover_torn_tail=True)

    def _read_unlocked(self, *, recover_torn_tail: bool) -> Tuple[IntelligenceJournalRecord, ...]:
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
        for number, line in enumerate(raw.decode().splitlines(), 1):
            try:
                item = IntelligenceJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"corrupt intelligence journal line {number}") from exc
            if item.journal_sequence != number or any(old.optimization_id == item.optimization_id or old.idempotency_key == item.idempotency_key for old in records):
                raise ValueError("intelligence journal sequence or identity mismatch")
            records.append(item)
        return tuple(records)
