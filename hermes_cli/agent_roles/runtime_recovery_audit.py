"""Deterministic append-only audit history for runtime recovery lifecycles."""

from __future__ import annotations

import hashlib
import json
import threading
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


RUNTIME_RECOVERY_AUDIT_SCHEMA_VERSION = 1
MAX_RUNTIME_RECOVERY_AUDIT_REFS = 100


class RuntimeRecoveryAuditEventType(str, Enum):
    RECOVERY_EXECUTION_CREATED = "recovery_execution_created"
    EXECUTION_STATE_CHANGED = "execution_state_changed"
    RECOVERY_ACTION_COMPLETED = "recovery_action_completed"
    RECOVERY_ACTION_FAILED = "recovery_action_failed"
    RECOVERY_CLOSURE_CREATED = "recovery_closure_created"
    RECONCILIATION_COMPLETED = "reconciliation_completed"
    RECONCILIATION_UNRESOLVED = "reconciliation_unresolved"
    RECOVERY_REPORT_CREATED = "recovery_report_created"
    ATTENTION_REQUIRED = "attention_required"
    AUDIT_VERIFICATION_RESULT = "audit_verification_result"


class RuntimeRecoveryAuditVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    checked_count: int = Field(..., ge=0)
    failure_position: Optional[int] = Field(default=None, ge=1)
    failure_event_id: Optional[str] = None
    reason: str


def _normalise(values) -> Tuple[str, ...]:
    cleaned = {str(value).strip() for value in values if str(value).strip()}
    if len(cleaned) > MAX_RUNTIME_RECOVERY_AUDIT_REFS:
        raise ValueError("too many runtime recovery audit references")
    if any(len(value) > 512 for value in cleaned):
        raise ValueError("invalid runtime recovery audit reference")
    return tuple(sorted(cleaned))


class RuntimeRecoveryAuditEvent(BaseModel):
    """One immutable, checksum-linked recovery lifecycle observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_event_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_AUDIT_SCHEMA_VERSION
    audit_sequence: int = Field(..., ge=1)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    closure_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    report_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    event_type: RuntimeRecoveryAuditEventType
    lifecycle_state: str = Field(..., min_length=1, max_length=128)
    actor_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=1024)
    occurred_at: int = Field(..., ge=0)
    evidence_refs: Tuple[str, ...] = Field(default_factory=tuple)
    source_checksums: Tuple[str, ...] = Field(default_factory=tuple)
    previous_event_checksum: Optional[str] = Field(default=None, min_length=64, max_length=64)
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator("audit_event_id", "project_id", "recovery_id", "lifecycle_state", "actor_id", "reason")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("runtime recovery audit text must not be blank")
        return value

    @field_validator("execution_id", "closure_id", "report_id")
    @classmethod
    def _strip_optional(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None

    @field_validator("evidence_refs", "source_checksums")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)

    @classmethod
    def calculate_checksum(cls, **values) -> str:
        payload = dict(values)
        payload.pop("checksum", None)
        event_type = payload["event_type"]
        payload["event_type"] = event_type.value if isinstance(event_type, Enum) else event_type
        payload["evidence_refs"] = list(_normalise(payload.get("evidence_refs", ())))
        payload["source_checksums"] = list(_normalise(payload.get("source_checksums", ())))
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_event(self) -> "RuntimeRecoveryAuditEvent":
        if self.schema_version != RUNTIME_RECOVERY_AUDIT_SCHEMA_VERSION:
            raise ValueError("unsupported runtime recovery audit schema version")
        if self.audit_sequence == 1 and self.previous_event_checksum is not None:
            raise ValueError("initial audit event cannot have previous checksum")
        if self.audit_sequence > 1 and self.previous_event_checksum is None:
            raise ValueError("later audit event requires previous checksum")
        expected = self.calculate_checksum(**self.model_dump(exclude={"checksum"}))
        if self.checksum != expected:
            raise ValueError("runtime recovery audit checksum mismatch")
        return self


class RuntimeRecoveryAuditBuilder:
    """Build audit events without mutating source recovery artifacts."""

    @staticmethod
    def build(*, audit_sequence: int, project_id: str, recovery_id: str,
              event_type: RuntimeRecoveryAuditEventType, lifecycle_state: str,
              actor_id: str, reason: str, occurred_at: int,
              execution_id: Optional[str] = None, closure_id: Optional[str] = None,
              report_id: Optional[str] = None, evidence_refs=(), source_checksums=(),
              previous_event_checksum: Optional[str] = None) -> RuntimeRecoveryAuditEvent:
        values = dict(
            schema_version=RUNTIME_RECOVERY_AUDIT_SCHEMA_VERSION,
            audit_sequence=audit_sequence, project_id=project_id.strip(),
            recovery_id=recovery_id.strip(), execution_id=execution_id,
            closure_id=closure_id, report_id=report_id, event_type=event_type,
            lifecycle_state=lifecycle_state.strip(), actor_id=actor_id.strip(),
            reason=reason.strip(), occurred_at=occurred_at,
            evidence_refs=_normalise(evidence_refs),
            source_checksums=_normalise(source_checksums),
            previous_event_checksum=previous_event_checksum,
        )
        content_digest = RuntimeRecoveryAuditEvent.calculate_checksum(**values)
        event_id = f"runtime_recovery_audit_{content_digest[:24]}"
        checksum = RuntimeRecoveryAuditEvent.calculate_checksum(
            audit_event_id=event_id,
            **values,
        )
        return RuntimeRecoveryAuditEvent(
            audit_event_id=event_id,
            **values,
            checksum=checksum,
        )

    @classmethod
    def from_artifact(cls, artifact: Any, *, audit_sequence: int,
                      event_type: RuntimeRecoveryAuditEventType,
                      lifecycle_state: Optional[str] = None,
                      actor_id: Optional[str] = None, reason: Optional[str] = None,
                      occurred_at: Optional[int] = None,
                      previous_event_checksum: Optional[str] = None,
                      extra_evidence_refs=(), extra_source_checksums=()):
        def value(*names, default=None):
            for name in names:
                found = getattr(artifact, name, None)
                if found is not None:
                    return found.value if isinstance(found, Enum) else found
            return default
        checksum = value("checksum")
        return cls.build(
            audit_sequence=audit_sequence, project_id=value("project_id"),
            recovery_id=value("recovery_id"), event_type=event_type,
            lifecycle_state=lifecycle_state or str(value("state", "reconciliation_state", default="unknown")),
            actor_id=actor_id or value("actor_id", "authorized_by", "requested_by"),
            reason=reason or value("reason", "authorization_reason", "request_reason"),
            occurred_at=occurred_at if occurred_at is not None else value("closed_at", "reconciled_at", "executed_at", "authorized_at", "requested_at"),
            execution_id=value("execution_id"),
            closure_id=value("closure_id"), report_id=value("report_id"),
            evidence_refs=tuple(value("evidence_refs", default=())) + tuple(extra_evidence_refs),
            source_checksums=tuple(value("source_checksums", default=())) + ((checksum,) if checksum else ()) + tuple(extra_source_checksums),
            previous_event_checksum=previous_event_checksum,
        )


class RuntimeRecoveryAuditStore:
    """Thread-safe, append-only in-memory audit history."""

    def __init__(self, *, capacity: int = 4096) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError("runtime recovery audit capacity must be between 1 and 100000")
        self.capacity = capacity
        self._events: list[RuntimeRecoveryAuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: RuntimeRecoveryAuditEvent) -> RuntimeRecoveryAuditEvent:
        with self._lock:
            event = RuntimeRecoveryAuditEvent.model_validate(event.model_dump())
            existing = next((item for item in self._events if item.audit_event_id == event.audit_event_id), None)
            if existing is not None:
                if existing == event:
                    return existing.model_copy(deep=True)
                raise ValueError("conflicting runtime recovery audit event replay")
            if len(self._events) >= self.capacity:
                raise OverflowError("runtime recovery audit capacity reached")
            related = [item for item in self._events if item.recovery_id == event.recovery_id]
            if related and any(item.project_id != event.project_id for item in related):
                raise ValueError("runtime recovery audit project ownership mismatch")
            execution_owners = [item for item in self._events if event.execution_id and item.execution_id == event.execution_id]
            if any(item.project_id != event.project_id or item.recovery_id != event.recovery_id for item in execution_owners):
                raise ValueError("runtime recovery audit execution ownership mismatch")
            expected_sequence = len(related) + 1
            expected_previous = related[-1].checksum if related else None
            if event.audit_sequence != expected_sequence or event.previous_event_checksum != expected_previous:
                raise ValueError("runtime recovery audit chain mismatch")
            self._events.append(event.model_copy(deep=True))
            return event.model_copy(deep=True)

    def get(self, audit_event_id: str) -> RuntimeRecoveryAuditEvent:
        with self._lock:
            match = next((item for item in self._events if item.audit_event_id == audit_event_id.strip()), None)
            if match is None:
                raise KeyError(f"runtime recovery audit event not found: {audit_event_id}")
            return match.model_copy(deep=True)

    def list(self, *, project_id: Optional[str] = None, recovery_id: Optional[str] = None,
             execution_id: Optional[str] = None,
             event_type: Optional[RuntimeRecoveryAuditEventType] = None,
             requires_attention: Optional[bool] = None) -> Tuple[RuntimeRecoveryAuditEvent, ...]:
        with self._lock:
            events = tuple(item.model_copy(deep=True) for item in self._events)
        if project_id is not None: events = tuple(e for e in events if e.project_id == project_id.strip())
        if recovery_id is not None: events = tuple(e for e in events if e.recovery_id == recovery_id.strip())
        if execution_id is not None: events = tuple(e for e in events if e.execution_id == execution_id.strip())
        if event_type is not None: events = tuple(e for e in events if e.event_type == event_type)
        if requires_attention is not None:
            attention = {RuntimeRecoveryAuditEventType.RECOVERY_ACTION_FAILED, RuntimeRecoveryAuditEventType.RECONCILIATION_UNRESOLVED, RuntimeRecoveryAuditEventType.ATTENTION_REQUIRED}
            events = tuple(e for e in events if (e.event_type in attention) is requires_attention)
        return tuple(sorted(events, key=lambda e: (e.occurred_at, e.audit_event_id)))

    def list_for_recovery(self, recovery_id: str): return self.list(recovery_id=recovery_id)
    def list_for_execution(self, execution_id: str): return self.list(execution_id=execution_id)
    def list_for_project(self, project_id: str): return self.list(project_id=project_id)

    def verify(self, recovery_id: str) -> RuntimeRecoveryAuditVerificationResult:
        with self._lock:
            events = tuple(item.model_copy(deep=True) for item in self._events if item.recovery_id == recovery_id.strip())
        return verify_runtime_recovery_audit_chain(events)


def verify_runtime_recovery_audit_chain(events) -> RuntimeRecoveryAuditVerificationResult:
    previous = None
    checked = 0
    for position, event in enumerate(events, 1):
        try:
            validated = RuntimeRecoveryAuditEvent.model_validate(event.model_dump())
        except Exception as exc:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_position=position, failure_event_id=getattr(event, "audit_event_id", None), reason=f"invalid or modified audit event: {exc}")
        if validated.audit_sequence != position:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_position=position, failure_event_id=validated.audit_event_id, reason="audit event missing or reordered")
        if validated.previous_event_checksum != previous:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_position=position, failure_event_id=validated.audit_event_id, reason="previous audit event checksum mismatch")
        previous = validated.checksum
        checked += 1
    return RuntimeRecoveryAuditVerificationResult(valid=True, checked_count=checked, reason="runtime recovery audit chain is valid")


def verify_runtime_recovery_audit_sources(event: RuntimeRecoveryAuditEvent, *, artifacts: Mapping[str, Any]) -> RuntimeRecoveryAuditVerificationResult:
    """Verify linked artifact ownership and checksums using their own validated models."""
    checked = 0
    for artifact_id, artifact in artifacts.items():
        if artifact is None:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_event_id=event.audit_event_id, reason=f"missing linked artifact: {artifact_id}")
        try:
            validated = type(artifact).model_validate(artifact.model_dump())
        except Exception as exc:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_event_id=event.audit_event_id, reason=f"invalid linked artifact {artifact_id}: {exc}")
        if getattr(validated, "project_id", event.project_id) != event.project_id or getattr(validated, "recovery_id", event.recovery_id) != event.recovery_id:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_event_id=event.audit_event_id, reason=f"linked artifact ownership mismatch: {artifact_id}")
        checksum = getattr(validated, "checksum", None)
        if checksum and checksum not in event.source_checksums:
            return RuntimeRecoveryAuditVerificationResult(valid=False, checked_count=checked, failure_event_id=event.audit_event_id, reason=f"linked artifact checksum mismatch: {artifact_id}")
        checked += 1
    return RuntimeRecoveryAuditVerificationResult(valid=True, checked_count=checked, reason="runtime recovery audit sources are valid")
