"""Append-only receipts for exact consumption of approved runtime recovery."""

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

from .runtime_recovery_store import RuntimeRecoveryAction


RUNTIME_RECOVERY_EXECUTION_JOURNAL = "runtime-recovery-executions.jsonl"


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid runtime recovery execution project_id")
    return value


class RuntimeRecoveryExecutionState(str, Enum):
    EXECUTED = "executed"
    HANDOFF_REQUIRED = "handoff_required"


class RuntimeRecoveryExecutionRecord(BaseModel):
    """Immutable receipt proving consumption of exact recovery authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    recovery_execution_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    source_execution_fingerprint: str = Field(..., min_length=64, max_length=64)
    action: RuntimeRecoveryAction
    state: RuntimeRecoveryExecutionState
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    executed_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)
    resulting_execution_revision: Optional[int] = Field(default=None, ge=1)
    resulting_execution_state: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        *,
        journal_sequence: int,
        recovery_execution_id: str,
        project_id: str,
        recovery_id: str,
        recovery_revision: int,
        execution_id: str,
        source_execution_fingerprint: str,
        action: RuntimeRecoveryAction,
        state: RuntimeRecoveryExecutionState,
        actor_id: str,
        correlation_id: str,
        causation_id: str,
        authorization_id: str,
        executed_at: int,
        reason: str,
        resulting_execution_revision: Optional[int],
        resulting_execution_state: Optional[str],
        evidence_refs: Tuple[str, ...],
    ) -> str:
        payload = {
            "journal_sequence": journal_sequence,
            "recovery_execution_id": recovery_execution_id,
            "project_id": _safe_project_id(project_id),
            "recovery_id": recovery_id,
            "recovery_revision": recovery_revision,
            "execution_id": execution_id,
            "source_execution_fingerprint": source_execution_fingerprint,
            "action": action.value,
            "state": state.value,
            "actor_id": actor_id,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "authorization_id": authorization_id,
            "executed_at": executed_at,
            "reason": reason,
            "resulting_execution_revision": resulting_execution_revision,
            "resulting_execution_state": resulting_execution_state,
            "evidence_refs": list(evidence_refs),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeRecoveryExecutionRecord":
        has_result = (
            self.resulting_execution_revision is not None
            and self.resulting_execution_state is not None
        )
        if self.state == RuntimeRecoveryExecutionState.EXECUTED and not has_result:
            raise ValueError("executed recovery requires resulting execution state")
        if self.state == RuntimeRecoveryExecutionState.HANDOFF_REQUIRED and has_result:
            raise ValueError("recovery handoff cannot claim runtime mutation")

        computed = self.calculate_checksum(
            journal_sequence=self.journal_sequence,
            recovery_execution_id=self.recovery_execution_id,
            project_id=self.project_id,
            recovery_id=self.recovery_id,
            recovery_revision=self.recovery_revision,
            execution_id=self.execution_id,
            source_execution_fingerprint=self.source_execution_fingerprint,
            action=self.action,
            state=self.state,
            actor_id=self.actor_id,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            authorization_id=self.authorization_id,
            executed_at=self.executed_at,
            reason=self.reason,
            resulting_execution_revision=self.resulting_execution_revision,
            resulting_execution_state=self.resulting_execution_state,
            evidence_refs=self.evidence_refs,
        )
        if self.checksum != computed:
            raise ValueError("runtime recovery execution checksum mismatch")
        return self


class RuntimeRecoveryExecutionStore:
    """Append-only, exactly-once recovery-consumption journal."""

    def __init__(self, root: Path, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError(
                "runtime recovery execution capacity must be between 1 and 100000"
            )
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return (
            self.root
            / _safe_project_id(project_id)
            / RUNTIME_RECOVERY_EXECUTION_JOURNAL
        )

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

    def create(
        self,
        *,
        recovery_execution_id: str,
        project_id: str,
        recovery_id: str,
        recovery_revision: int,
        execution_id: str,
        source_execution_fingerprint: str,
        action: RuntimeRecoveryAction,
        state: RuntimeRecoveryExecutionState,
        actor_id: str,
        correlation_id: str,
        causation_id: str,
        authorization_id: str,
        executed_at: int,
        reason: str,
        resulting_execution_revision: Optional[int] = None,
        resulting_execution_state: Optional[str] = None,
        evidence_refs: Tuple[str, ...] = (),
    ) -> RuntimeRecoveryExecutionRecord:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            existing = next(
                (
                    item
                    for item in records
                    if item.recovery_execution_id == recovery_execution_id
                ),
                None,
            )
            if existing is not None:
                return existing

            consumed = next(
                (item for item in records if item.recovery_id == recovery_id),
                None,
            )
            if consumed is not None:
                raise ValueError("runtime recovery authority was already consumed")

            sequence = len(records) + 1
            if sequence > self.capacity:
                raise OverflowError("runtime recovery execution capacity reached")

            values = {
                "journal_sequence": sequence,
                "recovery_execution_id": recovery_execution_id,
                "project_id": project_id,
                "recovery_id": recovery_id,
                "recovery_revision": recovery_revision,
                "execution_id": execution_id,
                "source_execution_fingerprint": source_execution_fingerprint,
                "action": action,
                "state": state,
                "actor_id": actor_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "authorization_id": authorization_id,
                "executed_at": executed_at,
                "reason": reason,
                "resulting_execution_revision": resulting_execution_revision,
                "resulting_execution_state": resulting_execution_state,
                "evidence_refs": tuple(dict.fromkeys(evidence_refs)),
            }
            checksum = RuntimeRecoveryExecutionRecord.calculate_checksum(**values)
            record = RuntimeRecoveryExecutionRecord(**values, checksum=checksum)

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
                        raise OSError(
                            "runtime recovery execution journal write made no progress"
                        )
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            return record

    def get(
        self,
        project_id: str,
        recovery_execution_id: str,
    ) -> Optional[RuntimeRecoveryExecutionRecord]:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            return next(
                (
                    item
                    for item in records
                    if item.recovery_execution_id == recovery_execution_id
                ),
                None,
            )

    def find_by_recovery(
        self,
        project_id: str,
        recovery_id: str,
    ) -> Optional[RuntimeRecoveryExecutionRecord]:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            return next(
                (item for item in records if item.recovery_id == recovery_id),
                None,
            )

    def list(
        self,
        project_id: str,
    ) -> Tuple[RuntimeRecoveryExecutionRecord, ...]:
        with self._write_lock(project_id):
            return self._read_unlocked(project_id, recover_torn_tail=True)

    def _read_unlocked(
        self,
        project_id: str,
        *,
        recover_torn_tail: bool = False,
    ) -> Tuple[RuntimeRecoveryExecutionRecord, ...]:
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
            raise ValueError(
                "corrupt runtime recovery execution journal encoding"
            ) from exc

        records = []
        recovery_ids: set[str] = set()
        execution_ids: set[str] = set()
        for line_number, line in enumerate(lines, 1):
            try:
                record = RuntimeRecoveryExecutionRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt runtime recovery execution journal line {line_number}"
                ) from exc
            if record.journal_sequence != line_number:
                raise ValueError(
                    "runtime recovery execution journal sequence mismatch"
                )
            if record.project_id != _safe_project_id(project_id):
                raise ValueError("runtime recovery execution project mismatch")
            if record.recovery_id in recovery_ids:
                raise ValueError("duplicate runtime recovery consumption")
            if record.recovery_execution_id in execution_ids:
                raise ValueError("duplicate runtime recovery execution identifier")
            recovery_ids.add(record.recovery_id)
            execution_ids.add(record.recovery_execution_id)
            records.append(record)
        return tuple(records)
