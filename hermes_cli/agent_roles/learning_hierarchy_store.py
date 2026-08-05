"""Append-only Step 31 learning-decision evidence persistence."""

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

from .learning_hierarchy import LearningDecision


LEARNING_HIERARCHY_JOURNAL = "learning-hierarchy.jsonl"


class LearningJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    decision_id: str
    idempotency_key: str
    decision: LearningDecision
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        sequence: int,
        decision: LearningDecision,
    ) -> str:
        value = {
            "journal_sequence": sequence,
            "decision_id": decision.decision_id,
            "idempotency_key": decision.idempotency_key,
            "decision": decision.model_dump(mode="json"),
        }
        return hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def _consistent(self) -> "LearningJournalRecord":
        if (
            self.decision_id != self.decision.decision_id
            or self.idempotency_key != self.decision.idempotency_key
            or self.checksum
            != self.calculate_checksum(
                self.journal_sequence,
                self.decision,
            )
        ):
            raise ValueError("learning journal association mismatch")
        return self


class InMemoryLearningHierarchyStore:
    def __init__(self) -> None:
        self._records: dict[str, LearningDecision] = {}
        self._keys: dict[str, str] = {}

    def get(self, decision_id: str) -> Optional[LearningDecision]:
        return self._records.get(decision_id)

    def find_by_idempotency_key(
        self,
        key: str,
    ) -> Optional[LearningDecision]:
        identity = self._keys.get(key)
        return None if identity is None else self._records[identity]

    def list(self) -> Tuple[LearningDecision, ...]:
        return tuple(
            self._records[key] for key in sorted(self._records)
        )

    def save(self, decision: LearningDecision) -> LearningDecision:
        current = (
            self.get(decision.decision_id)
            or self.find_by_idempotency_key(decision.idempotency_key)
        )
        if current:
            if current == decision:
                return current
            raise ValueError(
                "learning decision identity or idempotency collision"
            )
        self._records[decision.decision_id] = decision
        self._keys[decision.idempotency_key] = decision.decision_id
        return decision


class LearningHierarchyStore:
    def __init__(self, root: Path, *, capacity: int = 10_000) -> None:
        if not 1 <= capacity <= 100_000:
            raise ValueError(
                "learning hierarchy capacity must be between 1 and 100000"
            )
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    @property
    def journal_path(self) -> Path:
        return self.root / LEARNING_HIERARCHY_JOURNAL

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        lock_path = self.root / ".learning-hierarchy.lock"
        with self._thread_lock, lock_path.open("a+") as handle:
            os.chmod(handle.name, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def get(self, decision_id: str) -> Optional[LearningDecision]:
        return next(
            (
                item.decision
                for item in self._read()
                if item.decision_id == decision_id.strip()
            ),
            None,
        )

    def find_by_idempotency_key(
        self,
        key: str,
    ) -> Optional[LearningDecision]:
        return next(
            (
                item.decision
                for item in self._read()
                if item.idempotency_key == key.strip()
            ),
            None,
        )

    def list(self) -> Tuple[LearningDecision, ...]:
        return tuple(item.decision for item in self._read())

    def save(self, decision: LearningDecision) -> LearningDecision:
        with self._lock():
            records = self._read_unlocked(recover_torn_tail=True)
            matches = [
                item.decision
                for item in records
                if (
                    item.decision_id == decision.decision_id
                    or item.idempotency_key == decision.idempotency_key
                )
            ]
            if matches:
                if len(matches) == 1 and matches[0] == decision:
                    return decision
                raise ValueError(
                    "learning decision identity or idempotency collision"
                )
            if len(records) >= self.capacity:
                raise OverflowError("learning hierarchy capacity reached")

            sequence = len(records) + 1
            item = LearningJournalRecord(
                journal_sequence=sequence,
                decision_id=decision.decision_id,
                idempotency_key=decision.idempotency_key,
                decision=decision,
                checksum=LearningJournalRecord.calculate_checksum(
                    sequence,
                    decision,
                ),
            )
            payload = (
                json.dumps(
                    item.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()

            fd = os.open(
                self.journal_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError(
                            "learning journal write made no progress"
                        )
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)

            return decision

    def _read(self) -> Tuple[LearningJournalRecord, ...]:
        with self._lock():
            return self._read_unlocked(recover_torn_tail=True)

    def _read_unlocked(
        self,
        *,
        recover_torn_tail: bool,
    ) -> Tuple[LearningJournalRecord, ...]:
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
                item = LearningJournalRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt learning hierarchy journal line {number}"
                ) from exc

            if item.journal_sequence != number:
                raise ValueError(
                    "learning hierarchy journal sequence mismatch"
                )
            if any(
                old.decision_id == item.decision_id
                or old.idempotency_key == item.idempotency_key
                for old in records
            ):
                raise ValueError(
                    "learning hierarchy journal identity mismatch"
                )
            records.append(item)

        return tuple(records)
