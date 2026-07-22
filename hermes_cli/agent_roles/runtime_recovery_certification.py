"""Deterministic certification of governed runtime recovery lifecycles."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime_recovery_audit import (
    RuntimeRecoveryAuditEvent,
    RuntimeRecoveryAuditEventType,
    verify_runtime_recovery_audit_chain,
)
from .runtime_recovery_closure_store import RuntimeRecoveryClosureRecord
from .runtime_recovery_execution_store import (
    RuntimeRecoveryExecutionRecord,
    RuntimeRecoveryExecutionState,
)
from .runtime_recovery_reconciliation_store import (
    RuntimeRecoveryReconciliationRecord,
    RuntimeRecoveryReconciliationState,
)
from .runtime_recovery_reporting import RuntimeRecoveryReport
from .runtime_recovery_store import RuntimeRecoveryRecord, RuntimeRecoveryState


RUNTIME_RECOVERY_CERTIFICATION_SCHEMA_VERSION = 1


class RuntimeRecoveryCertificationStatus(str, Enum):
    CERTIFIED = "certified"
    REJECTED = "rejected"
    ATTENTION_REQUIRED = "attention_required"


class RuntimeRecoveryCertificationCheck(BaseModel):
    """One deterministic, structured lifecycle certification conclusion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_name: str = Field(..., min_length=1, max_length=128)
    passed: bool
    attention_required: bool = False
    detail: str = Field(..., min_length=1, max_length=1024)

    @field_validator("check_name", "detail")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("runtime recovery certification check text must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_attention(self) -> "RuntimeRecoveryCertificationCheck":
        if self.attention_required and not self.passed:
            raise ValueError("failed certification check cannot be an attention indicator")
        return self


class RuntimeRecoveryCertificationArtifact(BaseModel):
    """Immutable identity and provenance for one certification input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str = Field(..., min_length=1, max_length=128)
    artifact_id: str = Field(..., min_length=1, max_length=128)
    checksum: str = Field(..., min_length=64, max_length=64)
    recovery_revision: Optional[int] = Field(default=None, ge=1)
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class RuntimeRecoveryCertification(BaseModel):
    """Immutable governance-review certification for a complete lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    certification_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_CERTIFICATION_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    certification_revision: int = Field(..., ge=1)
    status: RuntimeRecoveryCertificationStatus
    certified_at: int = Field(..., ge=0)
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    validation_checks: Tuple[RuntimeRecoveryCertificationCheck, ...]
    artifact_inventory: Tuple[RuntimeRecoveryCertificationArtifact, ...]
    source_checksums: Tuple[Tuple[str, str], ...]
    evidence_refs: Tuple[str, ...]
    attention_flags: Tuple[str, ...]
    lifecycle_checksum: str = Field(..., min_length=64, max_length=64)
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_lifecycle_checksum(
        inventory: Iterable[RuntimeRecoveryCertificationArtifact],
    ) -> str:
        payload = [item.model_dump(mode="json") for item in inventory]
        return _checksum(payload)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("checksum", None)
        payload["status"] = _enum_value(payload["status"])
        payload["validation_checks"] = [
            _dump(item) for item in payload.get("validation_checks", ())
        ]
        payload["artifact_inventory"] = [
            _dump(item) for item in payload.get("artifact_inventory", ())
        ]
        payload["source_checksums"] = [
            list(item) for item in payload.get("source_checksums", ())
        ]
        payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
        payload["attention_flags"] = list(payload.get("attention_flags", ()))
        return _checksum(payload)

    @classmethod
    def certification_id_for(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("certification_id", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_certification_{_checksum(_canonical_certification_payload(payload))[:24]}"

    @model_validator(mode="after")
    def _validate_artifact(self) -> "RuntimeRecoveryCertification":
        if self.schema_version != RUNTIME_RECOVERY_CERTIFICATION_SCHEMA_VERSION:
            raise ValueError("unsupported runtime recovery certification schema version")
        if tuple(sorted(self.source_checksums)) != self.source_checksums:
            raise ValueError("runtime recovery certification source checksums are not sorted")
        if tuple(sorted(set(self.evidence_refs))) != self.evidence_refs:
            raise ValueError("runtime recovery certification evidence refs are not normalised")
        if tuple(sorted(set(self.attention_flags))) != self.attention_flags:
            raise ValueError("runtime recovery certification attention flags are not normalised")
        if self.lifecycle_checksum != self.calculate_lifecycle_checksum(self.artifact_inventory):
            raise ValueError("runtime recovery certification lifecycle checksum mismatch")
        expected = self.calculate_checksum(**self.model_dump(exclude={"checksum"}))
        if self.checksum != expected:
            raise ValueError("runtime recovery certification checksum mismatch")
        expected_id = self.certification_id_for(**self.model_dump(exclude={"checksum"}))
        if self.certification_id != expected_id:
            raise ValueError("runtime recovery certification identifier mismatch")
        return self


class RuntimeRecoveryCertificationVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    checked_count: int = Field(..., ge=0)
    missing_refs: Tuple[str, ...] = ()
    checksum_mismatches: Tuple[str, ...] = ()
    identity_mismatches: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    attention_required: bool = False


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _checksum(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalise(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _report_checksum(report: RuntimeRecoveryReport) -> str:
    payload = report.model_dump(exclude={"report_id", "checksum"}, mode="json")
    return _checksum(payload)


def _artifact_id(kind: str, artifact: Any) -> str:
    names = {
        "runtime_recovery": "recovery_id",
        "runtime_recovery_execution": "recovery_execution_id",
        "runtime_recovery_reconciliation": "reconciliation_id",
        "runtime_recovery_closure": "closure_id",
        "runtime_recovery_report": "report_id",
        "runtime_recovery_audit": "audit_event_id",
    }
    return str(getattr(artifact, names[kind]))


def _inventory_entry(kind: str, artifact: Any) -> RuntimeRecoveryCertificationArtifact:
    recovery_revision = (
        artifact.revision if kind == "runtime_recovery"
        else getattr(artifact, "recovery_revision", None)
    )
    return RuntimeRecoveryCertificationArtifact(
        artifact_type=kind,
        artifact_id=_artifact_id(kind, artifact),
        checksum=artifact.checksum,
        recovery_revision=recovery_revision,
        execution_id=getattr(artifact, "execution_id", None),
    )


class RuntimeRecoveryCertificationBuilder:
    """Build certification conclusions without mutating lifecycle sources."""

    _ORDER = (
        "runtime_recovery",
        "runtime_recovery_execution",
        "runtime_recovery_reconciliation",
        "runtime_recovery_closure",
        "runtime_recovery_report",
    )

    @classmethod
    def from_artifacts(
        cls,
        *,
        recovery: Optional[RuntimeRecoveryRecord],
        recovery_execution: Optional[RuntimeRecoveryExecutionRecord],
        reconciliation: Optional[RuntimeRecoveryReconciliationRecord],
        closure: Optional[RuntimeRecoveryClosureRecord],
        report: Optional[RuntimeRecoveryReport],
        audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
        certified_at: int,
        actor_id: str,
        certification_revision: int = 1,
    ) -> RuntimeRecoveryCertification:
        actor_id = actor_id.strip()
        if not actor_id:
            raise ValueError("runtime recovery certification actor is required")
        if certified_at < 0:
            raise ValueError("runtime recovery certification timestamp must be non-negative")
        if certification_revision < 1:
            raise ValueError("runtime recovery certification revision must be positive")
        audits = tuple(audit_artifacts)
        supplied = {
            "runtime_recovery": recovery,
            "runtime_recovery_execution": recovery_execution,
            "runtime_recovery_reconciliation": reconciliation,
            "runtime_recovery_closure": closure,
            "runtime_recovery_report": report,
        }
        anchor = next((item for item in supplied.values() if item is not None), None)
        if anchor is None and audits:
            anchor = audits[0]
        if anchor is None:
            raise ValueError("at least one runtime recovery certification artifact is required")

        checks: list[RuntimeRecoveryCertificationCheck] = []
        def check(name: str, passed: bool, detail: str, *, attention: bool = False) -> None:
            checks.append(RuntimeRecoveryCertificationCheck(
                check_name=name, passed=passed,
                attention_required=attention and passed, detail=detail,
            ))

        missing = tuple(kind for kind in cls._ORDER if supplied[kind] is None)
        missing_names = missing + (("runtime_recovery_audit",) if not audits else ())
        check("required_artifact_presence", not missing and bool(audits),
              "complete lifecycle artifacts are present" if not missing and audits
              else "missing required artifacts: " + ", ".join(missing_names))

        integrity: dict[str, bool] = {}
        for kind in cls._ORDER:
            artifact = supplied[kind]
            valid = artifact is not None and _source_valid(kind, artifact)
            integrity[kind] = valid
            check(f"{kind}_checksum_integrity", valid,
                  f"{kind} checksum is valid" if valid else f"{kind} checksum is invalid or unavailable")
        audit_integrity = verify_runtime_recovery_audit_chain(audits)
        check("audit_sequence_checksum_chain_integrity", bool(audits) and audit_integrity.valid,
              audit_integrity.reason if audits else "runtime recovery audit sequence is missing")

        project_id = str(getattr(anchor, "project_id", "")).strip()
        recovery_id = str(getattr(anchor, "recovery_id", "")).strip()
        execution_id = str(getattr(anchor, "execution_id", "")).strip()
        recovery_revision = int(getattr(recovery, "revision", getattr(anchor, "recovery_revision", 1)))
        all_artifacts = tuple(item for item in supplied.values() if item is not None) + audits
        check("project_id_consistency", bool(project_id) and all(getattr(item, "project_id", project_id) == project_id for item in all_artifacts), "project identifiers are consistent")
        check("recovery_id_consistency", bool(recovery_id) and all(getattr(item, "recovery_id", recovery_id) == recovery_id for item in all_artifacts), "recovery identifiers are consistent")
        execution_values = tuple(getattr(item, "execution_id", None) for item in all_artifacts if getattr(item, "execution_id", None) is not None)
        if not execution_id and execution_values:
            execution_id = str(execution_values[0])
        check("execution_id_consistency", bool(execution_id) and all(value == execution_id for value in execution_values), "execution identifiers are consistent")
        revision_values = tuple(getattr(item, "recovery_revision", recovery_revision) for item in (recovery_execution, reconciliation) if item is not None)
        check("recovery_revision_consistency", recovery is not None and all(value == recovery_revision for value in revision_values), "recovery revisions are consistent")

        provenance = _provenance_checks(recovery, recovery_execution, reconciliation, closure, report)
        for name, passed, detail in provenance:
            check(name, passed, detail)

        chronology = _chronology_valid(recovery, recovery_execution, reconciliation, closure, report, audits, certified_at)
        check("lifecycle_chronology", chronology, "lifecycle timestamps are monotonic and do not postdate certification")
        terminal = _terminal_consistent(recovery_execution, reconciliation, closure, report)
        check("terminal_execution_state_consistency", terminal, "terminal execution, reconciliation, closure, and report states are consistent")

        audit_sources = _audit_sources_valid(audits, supplied)
        check("audit_source_verification", audit_sources, "audit sources resolve to lifecycle artifact checksums")

        attention_flags = _attention_flags(reconciliation, report, audits)
        check("attention_required_propagation", True,
              "attention indicators propagated" if attention_flags else "no unresolved attention indicators",
              attention=bool(attention_flags))

        inventory = tuple(
            [_inventory_entry(kind, supplied[kind]) for kind in cls._ORDER if supplied[kind] is not None]
            + [_inventory_entry("runtime_recovery_audit", item) for item in audits]
        )
        check("deterministic_artifact_inventory", True, "artifact inventory uses canonical lifecycle and audit sequence order")
        lifecycle_checksum = RuntimeRecoveryCertification.calculate_lifecycle_checksum(inventory)
        check("deterministic_lifecycle_checksum", True, "lifecycle checksum uses canonical artifact inventory")

        source_checksums = tuple(sorted(
            (f"{item.artifact_type}:{item.artifact_id}", item.checksum)
            for item in inventory
        ))
        evidence_refs = _normalise(
            ref for item in all_artifacts for ref in getattr(item, "evidence_refs", ())
        )
        failed = any(not item.passed for item in checks)
        status = (RuntimeRecoveryCertificationStatus.REJECTED if failed else
                  RuntimeRecoveryCertificationStatus.ATTENTION_REQUIRED if attention_flags else
                  RuntimeRecoveryCertificationStatus.CERTIFIED)
        correlation_id = str(getattr(recovery, "correlation_id", getattr(anchor, "correlation_id", ""))).strip()
        causation_id = lifecycle_checksum
        values = dict(
            schema_version=RUNTIME_RECOVERY_CERTIFICATION_SCHEMA_VERSION,
            project_id=project_id or "unknown",
            recovery_id=recovery_id or "unknown",
            recovery_revision=recovery_revision,
            execution_id=execution_id or "unknown",
            certification_revision=certification_revision,
            status=status,
            certified_at=certified_at,
            actor_id=actor_id,
            correlation_id=correlation_id or "unknown",
            causation_id=causation_id,
            validation_checks=tuple(checks),
            artifact_inventory=inventory,
            source_checksums=source_checksums,
            evidence_refs=evidence_refs,
            attention_flags=attention_flags,
            lifecycle_checksum=lifecycle_checksum,
        )
        certification_id = RuntimeRecoveryCertification.certification_id_for(**values)
        checksum = RuntimeRecoveryCertification.calculate_checksum(
            certification_id=certification_id, **values
        )
        return RuntimeRecoveryCertification(
            certification_id=certification_id, **values, checksum=checksum
        )


def _canonical_certification_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(values)
    payload["status"] = _enum_value(payload["status"])
    payload["validation_checks"] = [_dump(item) for item in payload["validation_checks"]]
    payload["artifact_inventory"] = [_dump(item) for item in payload["artifact_inventory"]]
    payload["source_checksums"] = [list(item) for item in payload["source_checksums"]]
    payload["evidence_refs"] = list(payload["evidence_refs"])
    payload["attention_flags"] = list(payload["attention_flags"])
    return payload


def _source_valid(kind: str, artifact: Any) -> bool:
    try:
        if kind == "runtime_recovery_report":
            return artifact.checksum == _report_checksum(artifact) and artifact.report_id == f"runtime_recovery_report_{artifact.checksum[:24]}"
        type(artifact).model_validate(artifact.model_dump())
        return True
    except Exception:
        return False


def _provenance_checks(recovery, receipt, reconciliation, closure, report):
    complete = all(item is not None for item in (recovery, receipt, reconciliation, closure, report))
    def result(name, predicate, detail):
        return (name, bool(complete and predicate), detail)
    return (
        result("source_execution_fingerprint_consistency", receipt is not None and len(receipt.source_execution_fingerprint) == 64, "source execution fingerprint is present and canonical"),
        result("correlation_continuity", len({item.correlation_id for item in (recovery, receipt, reconciliation, closure) if item is not None}) == 1, "correlation identifiers are continuous"),
        result("causation_continuity", receipt is not None and recovery is not None and reconciliation is not None and closure is not None and receipt.causation_id == recovery.checksum and reconciliation.causation_id == receipt.checksum and closure.causation_id == reconciliation.checksum, "causation checksum chain is continuous"),
        result("source_checksum_references", reconciliation is not None and closure is not None and report is not None and reconciliation.source_recovery_checksum == recovery.checksum and reconciliation.source_recovery_execution_checksum == receipt.checksum and closure.source_reconciliation_checksum == reconciliation.checksum and report.source_checksums == (recovery.checksum, receipt.checksum, reconciliation.checksum, closure.checksum), "source checksum references are exact"),
        result("evidence_reference_continuity", reconciliation is not None and closure is not None and report is not None and set(receipt.evidence_refs).issubset(reconciliation.evidence_refs) and set(reconciliation.evidence_refs).issubset(closure.evidence_refs) and set(report.evidence_refs) == set(reconciliation.evidence_refs) | set(closure.evidence_refs), "evidence references are continuous"),
        result("closure_reporting_consistency", report is not None and closure is not None and reconciliation is not None and report.reconciliation_id == reconciliation.reconciliation_id and report.closure_id == closure.closure_id and report.reconciliation_checksum == reconciliation.checksum and report.closure_checksum == closure.checksum and report.is_terminal, "closure and reporting identities are consistent"),
    )


def _chronology_valid(recovery, receipt, reconciliation, closure, report, audits, certified_at):
    if any(item is None for item in (recovery, receipt, reconciliation, closure, report)):
        return False
    times = (recovery.requested_at, recovery.authorized_at, receipt.executed_at, reconciliation.reconciled_at, closure.closed_at, report.reconciled_at, report.closed_at, certified_at)
    if any(value is None for value in times):
        return False
    if not (times[0] <= times[1] <= times[2] <= times[3] <= times[4] <= times[7]):
        return False
    if report.reconciled_at != reconciliation.reconciled_at or report.closed_at != closure.closed_at:
        return False
    return all(receipt.executed_at <= event.occurred_at <= certified_at for event in audits)


def _terminal_consistent(receipt, reconciliation, closure, report):
    return bool(
        receipt is not None and reconciliation is not None and closure is not None and report is not None
        and receipt.state == RuntimeRecoveryExecutionState.EXECUTED
        and receipt.resulting_execution_state == "cancelled"
        and reconciliation.state == RuntimeRecoveryReconciliationState.RECONCILED
        and reconciliation.expected_execution_state == receipt.resulting_execution_state
        and reconciliation.observed_execution_state == receipt.resulting_execution_state
        and report.recovery_execution_state == receipt.state.value
        and report.reconciliation_state == reconciliation.state.value
        and report.closure_state == closure.state.value
    )


def _audit_sources_valid(audits, supplied):
    if not audits or any(item is None for item in supplied.values()):
        return False
    checksums = {item.checksum for item in supplied.values()}
    referenced = set()
    for event in audits:
        if event.project_id != supplied["runtime_recovery"].project_id or event.recovery_id != supplied["runtime_recovery"].recovery_id:
            return False
        if event.source_checksums and not set(event.source_checksums).issubset(checksums):
            return False
        referenced.update(event.source_checksums)
    return checksums.issubset(referenced)


def _attention_flags(reconciliation, report, audits):
    flags = []
    if reconciliation is not None and reconciliation.state != RuntimeRecoveryReconciliationState.RECONCILED:
        flags.append(f"reconciliation:{reconciliation.state.value}")
    if report is not None and report.requires_attention:
        flags.append("report:attention_required")
    attention_events = {
        RuntimeRecoveryAuditEventType.RECOVERY_ACTION_FAILED,
        RuntimeRecoveryAuditEventType.RECONCILIATION_UNRESOLVED,
        RuntimeRecoveryAuditEventType.ATTENTION_REQUIRED,
    }
    flags.extend(f"audit:{event.event_type.value}:{event.audit_event_id}" for event in audits if event.event_type in attention_events)
    return _normalise(flags)


def verify_runtime_recovery_certification(
    certification: RuntimeRecoveryCertification,
    *,
    artifacts: Mapping[str, Any],
    audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
) -> RuntimeRecoveryCertificationVerificationResult:
    missing: list[str] = []
    checksums: list[str] = []
    identities: list[str] = []
    errors: list[str] = []
    checked = 0
    try:
        RuntimeRecoveryCertification.model_validate(certification.model_dump())
        checked += 3
    except Exception as exc:
        errors.append(f"invalid certification artifact: {exc}")

    resolved = dict(artifacts)
    audits = tuple(audit_artifacts)
    audit_by_id = {item.audit_event_id: item for item in audits}
    expected_keys = {item.artifact_id for item in certification.artifact_inventory if item.artifact_type != "runtime_recovery_audit"}
    unexpected = set(resolved) - expected_keys
    if unexpected:
        identities.extend(f"unexpected artifact: {item}" for item in sorted(unexpected))
    for item in certification.artifact_inventory:
        source = audit_by_id.get(item.artifact_id) if item.artifact_type == "runtime_recovery_audit" else resolved.get(item.artifact_id)
        if source is None:
            missing.append(item.artifact_id)
            continue
        checked += 1
        if getattr(source, "checksum", None) != item.checksum or not _source_valid(item.artifact_type, source):
            checksums.append(item.artifact_id)
        if _artifact_id(item.artifact_type, source) != item.artifact_id:
            identities.append(item.artifact_id)
        if getattr(source, "project_id", certification.project_id) != certification.project_id or getattr(source, "recovery_id", certification.recovery_id) != certification.recovery_id:
            identities.append(item.artifact_id)

    kind_map = {item.artifact_type: resolved.get(item.artifact_id) for item in certification.artifact_inventory if item.artifact_type != "runtime_recovery_audit"}
    if all(kind_map.get(kind) is not None for kind in RuntimeRecoveryCertificationBuilder._ORDER):
        try:
            rebuilt = RuntimeRecoveryCertificationBuilder.from_artifacts(
                recovery=kind_map["runtime_recovery"],
                recovery_execution=kind_map["runtime_recovery_execution"],
                reconciliation=kind_map["runtime_recovery_reconciliation"],
                closure=kind_map["runtime_recovery_closure"],
                report=kind_map["runtime_recovery_report"],
                audit_artifacts=audits,
                certified_at=certification.certified_at,
                actor_id=certification.actor_id,
                certification_revision=certification.certification_revision,
            )
            checked += len(rebuilt.validation_checks)
            if rebuilt != certification:
                errors.append("certification is incompatible with deterministic rebuild")
        except Exception as exc:
            errors.append(f"deterministic certification rebuild failed: {exc}")
    valid = not (missing or checksums or identities or errors)
    return RuntimeRecoveryCertificationVerificationResult(
        valid=valid, checked_count=checked,
        missing_refs=_normalise(missing), checksum_mismatches=_normalise(checksums),
        identity_mismatches=_normalise(identities), errors=_normalise(errors),
        attention_required=(certification.status != RuntimeRecoveryCertificationStatus.CERTIFIED or not valid),
    )


def runtime_recovery_certifications_requiring_attention(
    certifications: Iterable[RuntimeRecoveryCertification],
) -> Tuple[RuntimeRecoveryCertification, ...]:
    return tuple(sorted(
        (item for item in certifications if item.status != RuntimeRecoveryCertificationStatus.CERTIFIED or item.attention_flags),
        key=lambda item: (item.certified_at, item.certification_id),
    ))
