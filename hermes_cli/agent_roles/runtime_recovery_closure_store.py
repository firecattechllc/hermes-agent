"""Immutable in-memory records for governed recovery closure."""

from __future__ import annotations

import hashlib
import json
import threading
from enum import Enum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime_recovery_reconciliation_store import RuntimeRecoveryReconciliationState
from .runtime_recovery_store import RuntimeRecoveryAction


RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION = 1
MAX_RECOVERY_CLOSURE_EVIDENCE_REFS = 100


class RuntimeRecoveryClosureState(str, Enum):
    """Terminal state of a governed runtime recovery lifecycle."""

    CLOSED = "closed"


class RuntimeRecoveryClosureRecord(BaseModel):
    """One immutable terminal runtime recovery closure conclusion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    closure_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)

    reconciliation_id: str = Field(..., min_length=1, max_length=128)
    recovery_execution_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    execution_id: str = Field(..., min_length=1, max_length=128)

    action: RuntimeRecoveryAction
    reconciliation_state: RuntimeRecoveryReconciliationState
    state: RuntimeRecoveryClosureState

    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)

    closed_at: int = Field(..., ge=0)

    source_reconciliation_checksum: str = Field(
        ..., min_length=64, max_length=64
    )

    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=MAX_RECOVERY_CLOSURE_EVIDENCE_REFS,
    )

    revision: int = Field(..., ge=1)
    previous_checksum: Optional[str] = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator(
        "closure_id",
        "project_id",
        "reconciliation_id",
        "recovery_execution_id",
        "recovery_id",
        "execution_id",
        "actor_id",
        "correlation_id",
        "causation_id",
        "reason",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("runtime recovery closure text must not be blank")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        result: list[str] = []

        for raw in values:
            value = raw.strip()

            if not value or len(value) > 512:
                raise ValueError(
                    "invalid runtime recovery closure evidence reference"
                )

            if value not in result:
                result.append(value)

        return tuple(result)

    @classmethod
    def calculate_checksum(cls, **values) -> str:
        payload = dict(values)
        payload.pop("checksum", None)

        for key in (
            "action",
            "reconciliation_state",
            "state",
        ):
            value = payload[key]
            payload[key] = value.value if isinstance(value, Enum) else value

        payload["evidence_refs"] = list(payload.get("evidence_refs", ()))

        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeRecoveryClosureRecord":
        if self.schema_version != RUNTIME_RECOVERY_CLOSURE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported runtime recovery closure schema version"
            )

        if self.state != RuntimeRecoveryClosureState.CLOSED:
            raise ValueError(
                "runtime recovery closure must be terminally closed"
            )

        if self.reconciliation_state != RuntimeRecoveryReconciliationState.RECONCILED:
            raise ValueError(
                "runtime recovery closure requires a reconciled recovery"
            )

        if self.action != RuntimeRecoveryAction.CANCEL:
            raise ValueError(
                "runtime recovery closure requires a completed cancellation"
            )

        if self.causation_id != self.source_reconciliation_checksum:
            raise ValueError(
                "runtime recovery closure causation must match reconciliation checksum"
            )

        if self.revision == 1 and self.previous_checksum is not None:
            raise ValueError(
                "initial runtime recovery closure cannot have previous checksum"
            )

        if self.revision > 1 and self.previous_checksum is None:
            raise ValueError(
                "later runtime recovery closure requires previous checksum"
            )

        computed = self.calculate_checksum(
            **self.model_dump(exclude={"checksum"})
        )

        if self.checksum != computed:
            raise ValueError(
                "runtime recovery closure checksum mismatch"
            )

        return self


class RuntimeRecoveryClosureStore:
    """Thread-safe append-only in-memory runtime recovery closure history."""

    def __init__(self, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError(
                "runtime recovery closure capacity must be between 1 and 100000"
            )

        self.capacity = capacity
        self._records: list[RuntimeRecoveryClosureRecord] = []
        self._lock = threading.RLock()

    def append(
        self,
        record: RuntimeRecoveryClosureRecord,
    ) -> RuntimeRecoveryClosureRecord:
        with self._lock:
            record = RuntimeRecoveryClosureRecord.model_validate(
                record.model_dump()
            )
            records = self._validated_records()

            by_id = next(
                (
                    item
                    for item in records
                    if item.closure_id == record.closure_id
                ),
                None,
            )

            by_reconciliation = next(
                (
                    item
                    for item in records
                    if (
                        item.project_id == record.project_id
                        and item.reconciliation_id == record.reconciliation_id
                    )
                ),
                None,
            )

            if by_id is not None or by_reconciliation is not None:
                existing = by_id or by_reconciliation

                if existing == record:
                    return existing.model_copy(deep=True)

                raise ValueError(
                    "conflicting runtime recovery closure replay"
                )

            if len(records) >= self.capacity:
                raise OverflowError(
                    "runtime recovery closure capacity reached"
                )

            if record.revision != 1 or record.previous_checksum is not None:
                raise ValueError(
                    "new runtime recovery closure must be initial revision"
                )

            self._records.append(record.model_copy(deep=True))
            return record.model_copy(deep=True)

    def get(
        self,
        project_id: str,
        closure_id: str,
    ) -> Optional[RuntimeRecoveryClosureRecord]:
        project_id = project_id.strip()
        closure_id = closure_id.strip()

        with self._lock:
            match = next(
                (
                    item
                    for item in self._validated_records()
                    if (
                        item.project_id == project_id
                        and item.closure_id == closure_id
                    )
                ),
                None,
            )

            return match.model_copy(deep=True) if match else None

    def find_by_reconciliation(
        self,
        project_id: str,
        reconciliation_id: str,
    ) -> Optional[RuntimeRecoveryClosureRecord]:
        project_id = project_id.strip()
        reconciliation_id = reconciliation_id.strip()

        with self._lock:
            match = next(
                (
                    item
                    for item in self._validated_records()
                    if (
                        item.project_id == project_id
                        and item.reconciliation_id == reconciliation_id
                    )
                ),
                None,
            )

            return match.model_copy(deep=True) if match else None

    def history(
        self,
        project_id: str,
        reconciliation_id: str,
    ) -> Tuple[RuntimeRecoveryClosureRecord, ...]:
        project_id = project_id.strip()
        reconciliation_id = reconciliation_id.strip()

        with self._lock:
            return tuple(
                item.model_copy(deep=True)
                for item in self._validated_records()
                if (
                    item.project_id == project_id
                    and item.reconciliation_id == reconciliation_id
                )
            )

    def list(
        self,
        project_id: str,
    ) -> Tuple[RuntimeRecoveryClosureRecord, ...]:
        project_id = project_id.strip()

        with self._lock:
            return tuple(
                item.model_copy(deep=True)
                for item in self._validated_records()
                if item.project_id == project_id
            )

    def _validated_records(
        self,
    ) -> Tuple[RuntimeRecoveryClosureRecord, ...]:
        records = tuple(
            RuntimeRecoveryClosureRecord.model_validate(
                item.model_dump()
            )
            for item in self._records
        )

        seen_ids: set[str] = set()
        seen_reconciliations: set[tuple[str, str]] = set()

        for item in records:
            if item.closure_id in seen_ids:
                raise ValueError(
                    "duplicate runtime recovery closure identifier"
                )

            reconciliation_key = (
                item.project_id,
                item.reconciliation_id,
            )

            if reconciliation_key in seen_reconciliations:
                raise ValueError(
                    "multiple closures for runtime recovery reconciliation"
                )

            seen_ids.add(item.closure_id)
            seen_reconciliations.add(reconciliation_key)

        return records
