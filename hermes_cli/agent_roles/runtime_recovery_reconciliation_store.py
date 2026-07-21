"""Immutable in-memory records for governed recovery reconciliation."""

from __future__ import annotations

import hashlib
import json
import threading
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime_recovery_execution_store import RuntimeRecoveryExecutionState
from .runtime_recovery_store import RuntimeRecoveryAction


RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION = 1
MAX_RECONCILIATION_EVIDENCE_REFS = 100


class RuntimeRecoveryReconciliationState(str, Enum):
    RECONCILED = "reconciled"
    HANDOFF_PENDING = "handoff_pending"
    INCONSISTENT = "inconsistent"


class RuntimeRecoveryReconciliationRecord(BaseModel):
    """One immutable, checksum-linked reconciliation conclusion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reconciliation_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_execution_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    action: RuntimeRecoveryAction
    recovery_execution_state: RuntimeRecoveryExecutionState
    state: RuntimeRecoveryReconciliationState
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    reconciled_at: int = Field(..., ge=0)
    source_recovery_checksum: str = Field(..., min_length=64, max_length=64)
    source_recovery_execution_checksum: str = Field(..., min_length=64, max_length=64)
    expected_execution_revision: Optional[int] = Field(default=None, ge=1)
    expected_execution_state: Optional[str] = Field(default=None, min_length=1, max_length=64)
    observed_execution_revision: Optional[int] = Field(default=None, ge=1)
    observed_execution_state: Optional[str] = Field(default=None, min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = Field(
        default_factory=tuple, max_length=MAX_RECONCILIATION_EVIDENCE_REFS
    )
    revision: int = Field(..., ge=1)
    previous_checksum: Optional[str] = Field(default=None, min_length=64, max_length=64)
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator(
        "reconciliation_id", "project_id", "recovery_execution_id", "recovery_id",
        "execution_id", "actor_id", "correlation_id", "causation_id",
        "authorization_id", "reason", "expected_execution_state",
        "observed_execution_state",
    )
    @classmethod
    def _strip_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("reconciliation text must not be blank")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        result = []
        for raw in values:
            value = raw.strip()
            if not value or len(value) > 512:
                raise ValueError("invalid reconciliation evidence reference")
            if value not in result:
                result.append(value)
        return tuple(result)

    @classmethod
    def calculate_checksum(cls, **values) -> str:
        payload = dict(values)
        payload.pop("checksum", None)
        for key in ("action", "recovery_execution_state", "state"):
            value = payload[key]
            payload[key] = value.value if isinstance(value, Enum) else value
        payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeRecoveryReconciliationRecord":
        if self.schema_version != RUNTIME_RECOVERY_RECONCILIATION_SCHEMA_VERSION:
            raise ValueError("unsupported runtime recovery reconciliation schema version")
        expected = (self.expected_execution_revision, self.expected_execution_state)
        observed = (self.observed_execution_revision, self.observed_execution_state)
        if (expected[0] is None) != (expected[1] is None):
            raise ValueError("expected execution result must be complete")
        if (observed[0] is None) != (observed[1] is None):
            raise ValueError("observed execution result must be complete")
        if self.state == RuntimeRecoveryReconciliationState.RECONCILED:
            if expected[0] is None or expected != observed:
                raise ValueError("reconciled recovery requires matching runtime result")
        if self.state == RuntimeRecoveryReconciliationState.HANDOFF_PENDING:
            if self.recovery_execution_state != RuntimeRecoveryExecutionState.HANDOFF_REQUIRED:
                raise ValueError("handoff pending requires a handoff receipt")
            if any(item is not None for item in expected + observed):
                raise ValueError("handoff pending cannot claim runtime mutation")
        if self.revision == 1 and self.previous_checksum is not None:
            raise ValueError("initial reconciliation cannot have previous checksum")
        if self.revision > 1 and self.previous_checksum is None:
            raise ValueError("later reconciliation requires previous checksum")
        computed = self.calculate_checksum(**self.model_dump(exclude={"checksum"}))
        if self.checksum != computed:
            raise ValueError("runtime recovery reconciliation checksum mismatch")
        return self


class RuntimeRecoveryReconciliationStore:
    """Thread-safe append-only in-memory reconciliation history."""

    def __init__(self, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("reconciliation capacity must be between 1 and 100000")
        self.capacity = capacity
        self._records: list[RuntimeRecoveryReconciliationRecord] = []
        self._lock = threading.RLock()

    def append(self, record: RuntimeRecoveryReconciliationRecord) -> RuntimeRecoveryReconciliationRecord:
        with self._lock:
            record = RuntimeRecoveryReconciliationRecord.model_validate(record.model_dump())
            records = self._validated_records()
            by_id = next((item for item in records if item.reconciliation_id == record.reconciliation_id), None)
            by_receipt = next((item for item in records if item.recovery_execution_id == record.recovery_execution_id), None)
            if by_id is not None or by_receipt is not None:
                existing = by_id or by_receipt
                if existing == record:
                    return existing.model_copy(deep=True)
                raise ValueError("conflicting runtime recovery reconciliation replay")
            if len(records) >= self.capacity:
                raise OverflowError("runtime recovery reconciliation capacity reached")
            if record.revision != 1 or record.previous_checksum is not None:
                raise ValueError("new recovery reconciliation must be initial revision")
            self._records.append(record.model_copy(deep=True))
            return record.model_copy(deep=True)

    def get(self, project_id: str, reconciliation_id: str) -> Optional[RuntimeRecoveryReconciliationRecord]:
        project_id = project_id.strip()
        with self._lock:
            match = next((item for item in self._validated_records() if item.project_id == project_id and item.reconciliation_id == reconciliation_id), None)
            return match.model_copy(deep=True) if match else None

    def find_by_recovery_execution(self, project_id: str, recovery_execution_id: str) -> Optional[RuntimeRecoveryReconciliationRecord]:
        project_id = project_id.strip()
        with self._lock:
            match = next((item for item in self._validated_records() if item.project_id == project_id and item.recovery_execution_id == recovery_execution_id), None)
            return match.model_copy(deep=True) if match else None

    def history(self, project_id: str, recovery_execution_id: str) -> Tuple[RuntimeRecoveryReconciliationRecord, ...]:
        project_id = project_id.strip()
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._validated_records() if item.project_id == project_id and item.recovery_execution_id == recovery_execution_id)

    def list(self, project_id: str) -> Tuple[RuntimeRecoveryReconciliationRecord, ...]:
        project_id = project_id.strip()
        with self._lock:
            return tuple(item.model_copy(deep=True) for item in self._validated_records() if item.project_id == project_id)

    def _validated_records(self) -> Tuple[RuntimeRecoveryReconciliationRecord, ...]:
        records = tuple(RuntimeRecoveryReconciliationRecord.model_validate(item.model_dump()) for item in self._records)
        seen_ids: set[str] = set()
        seen_receipts: set[tuple[str, str]] = set()
        for item in records:
            if item.reconciliation_id in seen_ids:
                raise ValueError("duplicate runtime recovery reconciliation identifier")
            key = (item.project_id, item.recovery_execution_id)
            if key in seen_receipts:
                raise ValueError("multiple reconciliations for recovery execution")
            seen_ids.add(item.reconciliation_id)
            seen_receipts.add(key)
        return records
