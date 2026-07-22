"""Append-only, project-isolated governed runtime recovery journal."""

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


RUNTIME_RECOVERY_JOURNAL = "runtime-recoveries.jsonl"


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid runtime recovery project_id")
    return value


class RuntimeRecoveryAction(str, Enum):
    CANCEL = "cancel"
    RETRY = "retry"
    ESCALATE = "escalate"


class RuntimeRecoveryState(str, Enum):
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    APPROVED = "approved"
    DENIED = "denied"


class RuntimeRecoveryDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


class RuntimeRecoveryRecord(BaseModel):
    """One immutable revision of a governed recovery request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    execution_id: str = Field(..., min_length=1, max_length=128)
    supervision_id: str = Field(..., min_length=1, max_length=128)
    supervision_revision: int = Field(..., ge=1)
    action: RuntimeRecoveryAction
    state: RuntimeRecoveryState
    revision: int = Field(..., ge=1)
    requested_by: str = Field(..., min_length=1, max_length=256)
    requested_at: int = Field(..., ge=0)
    request_reason: str = Field(..., min_length=1, max_length=1024)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    authorization_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    authorized_by: Optional[str] = Field(default=None, min_length=1, max_length=256)
    authorized_at: Optional[int] = Field(default=None, ge=0)
    authorization_reason: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        *,
        journal_sequence: int,
        recovery_id: str,
        project_id: str,
        execution_id: str,
        supervision_id: str,
        supervision_revision: int,
        action: RuntimeRecoveryAction,
        state: RuntimeRecoveryState,
        revision: int,
        requested_by: str,
        requested_at: int,
        request_reason: str,
        correlation_id: str,
        causation_id: str,
        authorization_id: Optional[str],
        authorized_by: Optional[str],
        authorized_at: Optional[int],
        authorization_reason: Optional[str],
    ) -> str:
        payload = {
            "journal_sequence": journal_sequence,
            "recovery_id": recovery_id,
            "project_id": _safe_project_id(project_id),
            "execution_id": execution_id,
            "supervision_id": supervision_id,
            "supervision_revision": supervision_revision,
            "action": action.value,
            "state": state.value,
            "revision": revision,
            "requested_by": requested_by,
            "requested_at": requested_at,
            "request_reason": request_reason,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "authorization_id": authorization_id,
            "authorized_by": authorized_by,
            "authorized_at": authorized_at,
            "authorization_reason": authorization_reason,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeRecoveryRecord":
        awaiting = self.state == RuntimeRecoveryState.AWAITING_AUTHORIZATION
        authority = (
            self.authorization_id,
            self.authorized_by,
            self.authorized_at,
            self.authorization_reason,
        )
        if awaiting and any(item is not None for item in authority):
            raise ValueError("pending runtime recovery cannot contain authorization")
        if not awaiting and any(item is None for item in authority):
            raise ValueError("decided runtime recovery requires complete authorization")
        if self.authorized_at is not None and self.authorized_at < self.requested_at:
            raise ValueError("runtime recovery authorization cannot predate request")

        computed = self.calculate_checksum(
            journal_sequence=self.journal_sequence,
            recovery_id=self.recovery_id,
            project_id=self.project_id,
            execution_id=self.execution_id,
            supervision_id=self.supervision_id,
            supervision_revision=self.supervision_revision,
            action=self.action,
            state=self.state,
            revision=self.revision,
            requested_by=self.requested_by,
            requested_at=self.requested_at,
            request_reason=self.request_reason,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
            authorization_id=self.authorization_id,
            authorized_by=self.authorized_by,
            authorized_at=self.authorized_at,
            authorization_reason=self.authorization_reason,
        )
        if self.checksum != computed:
            raise ValueError("runtime recovery journal checksum mismatch")
        return self


class RuntimeRecoveryStore:
    """Append-only recovery journal with project isolation and torn-tail repair."""

    def __init__(self, root: Path, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("runtime recovery capacity must be between 1 and 100000")
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return self.root / _safe_project_id(project_id) / RUNTIME_RECOVERY_JOURNAL

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
        recovery_id: str,
        project_id: str,
        execution_id: str,
        supervision_id: str,
        supervision_revision: int,
        action: RuntimeRecoveryAction,
        requested_by: str,
        requested_at: int,
        request_reason: str,
        correlation_id: str,
        causation_id: str,
    ) -> RuntimeRecoveryRecord:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            existing = next(
                (item for item in records if item.recovery_id == recovery_id),
                None,
            )
            if existing is not None:
                expected = {
                    "execution_id": execution_id,
                    "supervision_id": supervision_id,
                    "supervision_revision": supervision_revision,
                    "action": action,
                    "requested_by": requested_by,
                    "requested_at": requested_at,
                    "request_reason": request_reason,
                    "correlation_id": correlation_id,
                    "causation_id": causation_id,
                }
                if all(getattr(existing, key) == value for key, value in expected.items()):
                    return existing
                raise ValueError("runtime recovery identifier collision")

            record = self._build(
                journal_sequence=len(records) + 1,
                recovery_id=recovery_id,
                project_id=project_id,
                execution_id=execution_id,
                supervision_id=supervision_id,
                supervision_revision=supervision_revision,
                action=action,
                state=RuntimeRecoveryState.AWAITING_AUTHORIZATION,
                revision=1,
                requested_by=requested_by,
                requested_at=requested_at,
                request_reason=request_reason,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            return self._append_unlocked(project_id, records, record)

    def decide(
        self,
        *,
        project_id: str,
        recovery_id: str,
        expected_revision: int,
        decision: RuntimeRecoveryDecision,
        authorization_id: str,
        authorized_by: str,
        authorized_at: int,
        authorization_reason: str,
    ) -> RuntimeRecoveryRecord:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            current = self._latest(records, recovery_id)
            if current is None:
                raise ValueError("runtime recovery request not found")
            if current.revision != expected_revision:
                raise ValueError("runtime recovery expected_revision is stale")
            if current.state != RuntimeRecoveryState.AWAITING_AUTHORIZATION:
                if (
                    current.authorization_id == authorization_id
                    and current.authorized_by == authorized_by
                    and current.authorized_at == authorized_at
                    and current.authorization_reason == authorization_reason
                    and current.state.value == decision.value
                ):
                    return current
                raise ValueError("runtime recovery request is already decided")

            state = (
                RuntimeRecoveryState.APPROVED
                if decision == RuntimeRecoveryDecision.APPROVED
                else RuntimeRecoveryState.DENIED
            )
            record = self._build(
                journal_sequence=len(records) + 1,
                recovery_id=current.recovery_id,
                project_id=current.project_id,
                execution_id=current.execution_id,
                supervision_id=current.supervision_id,
                supervision_revision=current.supervision_revision,
                action=current.action,
                state=state,
                revision=current.revision + 1,
                requested_by=current.requested_by,
                requested_at=current.requested_at,
                request_reason=current.request_reason,
                correlation_id=current.correlation_id,
                causation_id=current.checksum,
                authorization_id=authorization_id,
                authorized_by=authorized_by,
                authorized_at=authorized_at,
                authorization_reason=authorization_reason,
            )
            return self._append_unlocked(project_id, records, record)

    def get(self, project_id: str, recovery_id: str) -> Optional[RuntimeRecoveryRecord]:
        with self._write_lock(project_id):
            records = self._read_unlocked(project_id, recover_torn_tail=True)
            return self._latest(records, recovery_id)

    def history(
        self,
        project_id: str,
        recovery_id: str,
    ) -> Tuple[RuntimeRecoveryRecord, ...]:
        with self._write_lock(project_id):
            return tuple(
                item
                for item in self._read_unlocked(project_id, recover_torn_tail=True)
                if item.recovery_id == recovery_id
            )

    def list(
        self,
        project_id: str,
        *,
        state: Optional[RuntimeRecoveryState] = None,
    ) -> Tuple[RuntimeRecoveryRecord, ...]:
        with self._write_lock(project_id):
            latest: dict[str, RuntimeRecoveryRecord] = {}
            for item in self._read_unlocked(project_id, recover_torn_tail=True):
                latest[item.recovery_id] = item
            records = tuple(latest.values())
            if state is not None:
                records = tuple(item for item in records if item.state == state)
            return records

    def _build(self, **values) -> RuntimeRecoveryRecord:
        if values["journal_sequence"] > self.capacity:
            raise OverflowError("runtime recovery capacity reached")

        values.setdefault("authorization_id", None)
        values.setdefault("authorized_by", None)
        values.setdefault("authorized_at", None)
        values.setdefault("authorization_reason", None)

        checksum = RuntimeRecoveryRecord.calculate_checksum(**values)
        return RuntimeRecoveryRecord(**values, checksum=checksum)

    def _append_unlocked(
        self,
        project_id: str,
        records: Tuple[RuntimeRecoveryRecord, ...],
        record: RuntimeRecoveryRecord,
    ) -> RuntimeRecoveryRecord:
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
                    raise OSError("runtime recovery journal write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    @staticmethod
    def _latest(
        records: Tuple[RuntimeRecoveryRecord, ...],
        recovery_id: str,
    ) -> Optional[RuntimeRecoveryRecord]:
        matches = tuple(item for item in records if item.recovery_id == recovery_id)
        return matches[-1] if matches else None

    def _read_unlocked(
        self,
        project_id: str,
        *,
        recover_torn_tail: bool = False,
    ) -> Tuple[RuntimeRecoveryRecord, ...]:
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
            raise ValueError("corrupt runtime recovery journal encoding") from exc

        records = []
        for line_number, line in enumerate(lines, 1):
            try:
                record = RuntimeRecoveryRecord.model_validate_json(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupt runtime recovery journal line {line_number}"
                ) from exc
            if record.journal_sequence != line_number:
                raise ValueError("runtime recovery journal sequence mismatch")
            if record.project_id != _safe_project_id(project_id):
                raise ValueError("runtime recovery project mismatch")
            records.append(record)
        return tuple(records)
