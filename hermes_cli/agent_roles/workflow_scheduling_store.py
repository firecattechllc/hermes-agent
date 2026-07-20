"""Revisioned, project-isolated persistence for workflow scheduling."""

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

from .workflow_scheduling import (
    DEFAULT_SCHEDULING_CAPACITY,
    MAX_CLAIM_LEASE_SECONDS,
    CoordinationStatus,
    TERMINAL_COORDINATION_STATUSES,
    WorkflowExecutionIntent,
    _safe_text,
)


WORKFLOW_SCHEDULING_JOURNAL = "workflow-scheduling.jsonl"
_MUTABLE_REVISION_FIELDS = frozenset({
    "version", "status", "actor_id", "causation_id", "updated_at",
    "available_at", "claim_id", "claimed_by", "lease_expires_at", "reason",
    "evidence_refs",
})
_ALLOWED_TRANSITIONS = {
    CoordinationStatus.SCHEDULED: frozenset({
        CoordinationStatus.CLAIMED, CoordinationStatus.DEFERRED,
        CoordinationStatus.REFUSED, CoordinationStatus.CANCELLED,
    }),
    CoordinationStatus.DEFERRED: frozenset({
        CoordinationStatus.CLAIMED, CoordinationStatus.DEFERRED,
        CoordinationStatus.REFUSED, CoordinationStatus.CANCELLED,
    }),
    CoordinationStatus.CLAIMED: frozenset({
        CoordinationStatus.DEFERRED, CoordinationStatus.REFUSED,
        CoordinationStatus.CANCELLED, CoordinationStatus.EXPIRED,
        CoordinationStatus.COMPLETED,
    }),
}


def _safe_project_id(project_id: str) -> str:
    project_id = project_id.strip()
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ValueError("invalid workflow scheduling project_id")
    return project_id


class WorkflowSchedulingJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str = Field(..., min_length=1, max_length=128)
    intent_id: str = Field(..., min_length=1, max_length=128)
    intent_version: int = Field(..., ge=1)
    previous_fingerprint: Optional[str] = Field(default=None, min_length=64, max_length=64)
    intent: WorkflowExecutionIntent
    checksum: str = Field(..., min_length=64, max_length=64)

    @model_validator(mode="after")
    def _validate_record(self) -> "WorkflowSchedulingJournalRecord":
        if (
            self.project_id != self.intent.project_id
            or self.intent_id != self.intent.intent_id
            or self.intent_version != self.intent.version
        ):
            raise ValueError("workflow scheduling journal association mismatch")
        if self.checksum != self.calculate_checksum(
            self.journal_sequence, self.project_id, self.intent_id,
            self.intent_version, self.previous_fingerprint, self.intent,
        ):
            raise ValueError("workflow scheduling journal checksum mismatch")
        return self

    @staticmethod
    def calculate_checksum(sequence: int, project_id: str, intent_id: str, version: int, previous: Optional[str], intent: WorkflowExecutionIntent) -> str:
        payload = {
            "journal_sequence": sequence,
            "project_id": project_id,
            "intent_id": intent_id,
            "intent_version": version,
            "previous_fingerprint": previous,
            "intent": intent.model_dump(mode="json"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class WorkflowSchedulingStore:
    """Atomic local coordination ledger; no background consumer is included."""

    def __init__(self, root: Path, *, capacity: int = DEFAULT_SCHEDULING_CAPACITY) -> None:
        if capacity < 1 or capacity > 10_000:
            raise ValueError("workflow scheduling capacity must be between 1 and 10000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return self.root / _safe_project_id(project_id) / WORKFLOW_SCHEDULING_JOURNAL

    @contextmanager
    def write_lock(self, project_id: str) -> Iterator[None]:
        path = self.journal_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def create(self, intent: WorkflowExecutionIntent) -> WorkflowExecutionIntent:
        with self.write_lock(intent.project_id):
            records = self._read_unlocked(intent.project_id)
            latest = self._latest(records)
            existing = latest.get(intent.intent_id)
            if existing is not None:
                if existing.version == 1 and existing == intent:
                    return existing
                raise ValueError("workflow scheduling intent ID collision")
            if intent.version != 1:
                raise ValueError("new workflow scheduling intent must begin at version 1")
            active = sum(
                item.status not in TERMINAL_COORDINATION_STATUSES
                for item in latest.values()
            )
            if active >= self.capacity:
                raise OverflowError("workflow scheduling capacity reached")
            self._append_unlocked(intent, records, previous=None)
            return intent

    def get(self, project_id: str, intent_id: str) -> Optional[WorkflowExecutionIntent]:
        return self._latest(self._read(project_id)).get(intent_id.strip())

    def list(self, project_id: str, *, status: Optional[CoordinationStatus] = None) -> Tuple[WorkflowExecutionIntent, ...]:
        values = tuple(self._latest(self._read(project_id)).values())
        if status is not None:
            values = tuple(item for item in values if item.status == status)
        return tuple(sorted(values, key=lambda item: item.stable_sort_key))

    def recover_interrupted_tail(self, project_id: str) -> bool:
        """Remove only an unterminated final write, preserving valid records.

        Newline-terminated corruption is never repaired or skipped; normal
        replay continues to fail closed for those records.
        """
        with self.write_lock(project_id):
            path = self.journal_path(project_id)
            if not path.exists():
                return False
            data = path.read_bytes()
            if not data or data.endswith(b"\n"):
                self._read_unlocked(project_id)
                return False
            boundary = data.rfind(b"\n") + 1
            prefix = data[:boundary]
            # Validate every durable record before discarding the torn tail.
            if prefix:
                latest: dict[str, WorkflowExecutionIntent] = {}
                for line_number, raw in enumerate(prefix.decode("utf-8").splitlines(), 1):
                    try:
                        record = WorkflowSchedulingJournalRecord.model_validate_json(raw)
                    except Exception as exc:
                        raise ValueError(
                            f"corrupt workflow scheduling journal line {line_number}"
                        ) from exc
                    if (
                        record.journal_sequence != line_number
                        or record.project_id != _safe_project_id(project_id)
                    ):
                        raise ValueError(
                            "workflow scheduling journal sequence or project mismatch"
                        )
                    prior = latest.get(record.intent_id)
                    if prior is None:
                        if (
                            record.intent_version != 1
                            or record.previous_fingerprint is not None
                        ):
                            raise ValueError(
                                "invalid initial workflow scheduling revision"
                            )
                    else:
                        self._validate_revision(prior, record.intent)
                        if record.previous_fingerprint != prior.fingerprint:
                            raise ValueError(
                                "workflow scheduling revision chain is invalid"
                            )
                    latest[record.intent_id] = record.intent
            with path.open("r+b") as handle:
                handle.truncate(boundary)
                handle.flush()
                os.fsync(handle.fileno())
            # Full replay also verifies revision chains and project identity.
            self._read_unlocked(project_id)
            return True

    def claim(self, project_id: str, intent_id: str, *, claimed_by: str, timestamp: int, lease_seconds: int) -> WorkflowExecutionIntent:
        claimed_by = _safe_text(claimed_by, "claimed_by")
        if lease_seconds < 1 or lease_seconds > MAX_CLAIM_LEASE_SECONDS:
            raise ValueError("claim lease is outside the bounded range")
        with self.write_lock(project_id):
            records = self._read_unlocked(project_id)
            current = self._require_current(records, intent_id)
            if current.status not in {CoordinationStatus.SCHEDULED, CoordinationStatus.DEFERRED}:
                raise ValueError("workflow scheduling intent is not claimable")
            if timestamp < current.updated_at or timestamp < current.available_at:
                raise ValueError("workflow scheduling intent is not yet available")
            seed = f"{current.intent_id}|{current.version + 1}|{claimed_by}|{timestamp}"
            updated = self._revision(
                current, status=CoordinationStatus.CLAIMED, actor_id=claimed_by,
                timestamp=timestamp,
                claim_id=f"claim_{hashlib.sha256(seed.encode()).hexdigest()[:24]}",
                claimed_by=claimed_by, lease_expires_at=timestamp + lease_seconds,
                reason=None, evidence_refs=current.evidence_refs,
                available_at=current.available_at,
            )
            self._validate_revision(current, updated)
            self._append_unlocked(updated, records, previous=current.fingerprint)
            return updated

    def transition(self, project_id: str, intent_id: str, *, status: CoordinationStatus, actor_id: str, timestamp: int, reason: str, evidence_refs: Tuple[str, ...] = (), available_at: Optional[int] = None, expected_claim_id: Optional[str] = None) -> WorkflowExecutionIntent:
        actor_id = _safe_text(actor_id, "actor_id")
        reason = _safe_text(reason, "reason")
        with self.write_lock(project_id):
            records = self._read_unlocked(project_id)
            current = self._require_current(records, intent_id)
            if current.status in TERMINAL_COORDINATION_STATUSES:
                raise ValueError("terminal workflow scheduling intent is immutable")
            allowed = {
                CoordinationStatus.SCHEDULED: {CoordinationStatus.DEFERRED, CoordinationStatus.REFUSED, CoordinationStatus.CANCELLED},
                CoordinationStatus.DEFERRED: {CoordinationStatus.DEFERRED, CoordinationStatus.REFUSED, CoordinationStatus.CANCELLED},
                CoordinationStatus.CLAIMED: {CoordinationStatus.DEFERRED, CoordinationStatus.REFUSED, CoordinationStatus.CANCELLED, CoordinationStatus.EXPIRED, CoordinationStatus.COMPLETED},
            }
            if status not in allowed[current.status]:
                raise ValueError("illegal workflow scheduling state transition")
            if timestamp < current.updated_at:
                raise ValueError("workflow scheduling timestamp cannot move backwards")
            if current.status == CoordinationStatus.CLAIMED:
                if expected_claim_id != current.claim_id:
                    raise ValueError("workflow scheduling claim identity mismatch")
                if (
                    status != CoordinationStatus.EXPIRED
                    and current.lease_expires_at is not None
                    and timestamp >= current.lease_expires_at
                ):
                    raise ValueError(
                        "workflow scheduling claim lease has expired"
                    )
            elif expected_claim_id is not None:
                raise ValueError("unclaimed intent cannot accept claim identity")
            next_available = available_at if status == CoordinationStatus.DEFERRED else current.available_at
            if status == CoordinationStatus.DEFERRED and (
                next_available is None or next_available <= timestamp
            ):
                raise ValueError("deferred intent requires a future availability timestamp")
            updated = self._revision(
                current, status=status, actor_id=actor_id, timestamp=timestamp,
                claim_id=None, claimed_by=None, lease_expires_at=None,
                reason=reason,
                evidence_refs=tuple(dict.fromkeys(current.evidence_refs + evidence_refs)),
                available_at=next_available,
            )
            self._validate_revision(current, updated)
            self._append_unlocked(updated, records, previous=current.fingerprint)
            return updated

    @staticmethod
    def _revision(current: WorkflowExecutionIntent, **updates) -> WorkflowExecutionIntent:
        return WorkflowExecutionIntent.model_validate({
            **current.model_dump(mode="python"),
            **{key: value for key, value in updates.items() if key != "timestamp"},
            "version": current.version + 1,
            "updated_at": updates["timestamp"],
            "causation_id": current.fingerprint,
        })

    def _append_unlocked(self, intent: WorkflowExecutionIntent, records: Tuple[WorkflowSchedulingJournalRecord, ...], *, previous: Optional[str]) -> None:
        sequence = len(records) + 1
        record = WorkflowSchedulingJournalRecord(
            journal_sequence=sequence,
            project_id=intent.project_id,
            intent_id=intent.intent_id,
            intent_version=intent.version,
            previous_fingerprint=previous,
            intent=intent,
            checksum=WorkflowSchedulingJournalRecord.calculate_checksum(
                sequence, intent.project_id, intent.intent_id, intent.version,
                previous, intent,
            ),
        )
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        fd = os.open(self.journal_path(intent.project_id), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            remaining = memoryview((line + "\n").encode())
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("workflow scheduling journal write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _validate_revision(
        previous: WorkflowExecutionIntent,
        current: WorkflowExecutionIntent,
    ) -> None:
        if current.version != previous.version + 1:
            raise ValueError("workflow scheduling revision sequence is invalid")
        if current.causation_id != previous.fingerprint:
            raise ValueError("workflow scheduling revision causation is invalid")
        if current.updated_at < previous.updated_at:
            raise ValueError("workflow scheduling revision timestamp moved backwards")
        if current.status not in _ALLOWED_TRANSITIONS.get(previous.status, ()):
            raise ValueError("illegal workflow scheduling revision transition")
        previous_identity = previous.model_dump(exclude=_MUTABLE_REVISION_FIELDS)
        current_identity = current.model_dump(exclude=_MUTABLE_REVISION_FIELDS)
        if current_identity != previous_identity:
            raise ValueError("workflow scheduling immutable identity changed")
        if current.evidence_refs[:len(previous.evidence_refs)] != previous.evidence_refs:
            raise ValueError("workflow scheduling evidence history changed")
        if (
            current.status != CoordinationStatus.DEFERRED
            and current.available_at != previous.available_at
        ):
            raise ValueError("workflow scheduling availability changed illegally")

    def _read(self, project_id: str) -> Tuple[WorkflowSchedulingJournalRecord, ...]:
        with self.write_lock(project_id):
            return self._read_unlocked(project_id)

    def _read_unlocked(self, project_id: str) -> Tuple[WorkflowSchedulingJournalRecord, ...]:
        project_id = _safe_project_id(project_id)
        path = self.journal_path(project_id)
        if not path.exists():
            return ()
        records = []
        latest: dict[str, WorkflowExecutionIntent] = {}
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = WorkflowSchedulingJournalRecord.model_validate_json(raw)
            except Exception as exc:
                raise ValueError(f"corrupt workflow scheduling journal line {line_number}") from exc
            if record.journal_sequence != line_number or record.project_id != project_id:
                raise ValueError("workflow scheduling journal sequence or project mismatch")
            prior = latest.get(record.intent_id)
            if prior is None:
                if record.intent_version != 1 or record.previous_fingerprint is not None:
                    raise ValueError("invalid initial workflow scheduling revision")
            else:
                self._validate_revision(prior, record.intent)
                if record.previous_fingerprint != prior.fingerprint:
                    raise ValueError("workflow scheduling revision chain is invalid")
            latest[record.intent_id] = record.intent
            records.append(record)
        return tuple(records)

    @staticmethod
    def _latest(records: Tuple[WorkflowSchedulingJournalRecord, ...]) -> dict[str, WorkflowExecutionIntent]:
        latest = {}
        for record in records:
            latest[record.intent_id] = record.intent
        return latest

    def _require_current(self, records: Tuple[WorkflowSchedulingJournalRecord, ...], intent_id: str) -> WorkflowExecutionIntent:
        current = self._latest(records).get(intent_id.strip())
        if current is None:
            raise KeyError("workflow scheduling intent not found")
        return current
