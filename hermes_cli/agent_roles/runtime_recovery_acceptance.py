"""Deterministic acceptance and safe handoff for governed runtime recovery.

The records in this module are governance evidence only.  They never execute a
recovery action or mutate any source lifecycle artifact.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime_recovery_audit import RuntimeRecoveryAuditEvent
from .runtime_recovery_authorization import (
    RuntimeRecoveryAuthorizationArtifact,
    RuntimeRecoveryAuthorizationPolicy,
    RuntimeRecoveryAuthorizationStatus,
    RuntimeRecoveryAuthorizationVerificationResult,
    RuntimeRecoveryHumanApproval,
    verify_runtime_recovery_authorization,
)
from .runtime_recovery_certification import RuntimeRecoveryCertification


RUNTIME_RECOVERY_ACCEPTANCE_SCHEMA_VERSION = 1


class RuntimeRecoveryAcceptanceStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PENDING_ACCEPTANCE = "pending_acceptance"
    ATTENTION_REQUIRED = "attention_required"


class RuntimeRecoveryAcceptanceDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    AWAIT_ACCEPTANCE = "await_acceptance"
    REQUIRE_ATTENTION = "require_attention"


class RuntimeRecoveryAcceptanceCheckSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RuntimeRecoverySafeNextAction(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    READY_FOR_MANUAL_HANDOFF = "ready_for_manual_handoff"
    BLOCKED = "blocked"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    ATTENTION_REQUIRED = "attention_required"


def _checksum(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalise(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


class RuntimeRecoveryAcceptancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(default="runtime-recovery-acceptance-v1", min_length=1, max_length=128)
    require_authorized_status: bool = True
    require_authorization_verification: bool = True
    require_certification_continuity: bool = True
    require_identity_continuity: bool = True
    require_resolved_sources: bool = True
    require_complete_handoff: bool = True
    require_no_critical_attention: bool = True
    require_human_acceptance: bool = False
    allowed_acceptor_ids: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _fail_closed(self) -> "RuntimeRecoveryAcceptancePolicy":
        if not all((
            self.require_authorized_status,
            self.require_authorization_verification,
            self.require_certification_continuity,
            self.require_identity_continuity,
            self.require_resolved_sources,
            self.require_complete_handoff,
            self.require_no_critical_attention,
        )):
            raise ValueError("runtime recovery acceptance safety requirements cannot be disabled")
        return self

    @field_validator("allowed_acceptor_ids")
    @classmethod
    def _normalise_ids(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)

    @property
    def fingerprint(self) -> str:
        return _checksum(self.model_dump(mode="json"))


class RuntimeRecoveryHumanAcceptance(BaseModel):
    """Explicit final human decision bound to one authorization and certification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acceptance_ref_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    certification_id: str = Field(..., min_length=1, max_length=128)
    certification_checksum: str = Field(..., min_length=64, max_length=64)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    authorization_checksum: str = Field(..., min_length=64, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=256)
    accepted: bool
    accepted_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = ()
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator("evidence_refs")
    @classmethod
    def _refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("checksum", None)
        payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
        return _checksum(payload)

    @classmethod
    def acceptance_ref_id_for(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("acceptance_ref_id", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_human_acceptance_{_checksum(payload)[:24]}"

    @classmethod
    def build(cls, **values: Any) -> "RuntimeRecoveryHumanAcceptance":
        values["evidence_refs"] = _normalise(values.get("evidence_refs", ()))
        reference_id = cls.acceptance_ref_id_for(**values)
        checksum = cls.calculate_checksum(acceptance_ref_id=reference_id, **values)
        return cls(acceptance_ref_id=reference_id, **values, checksum=checksum)

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeRecoveryHumanAcceptance":
        values = self.model_dump(exclude={"checksum"})
        if self.checksum != self.calculate_checksum(**values):
            raise ValueError("runtime recovery human acceptance checksum mismatch")
        if self.acceptance_ref_id != self.acceptance_ref_id_for(**values):
            raise ValueError("runtime recovery human acceptance identifier mismatch")
        return self


class RuntimeRecoveryAcceptanceCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_code: str = Field(..., min_length=1, max_length=128)
    passed: bool
    severity: RuntimeRecoveryAcceptanceCheckSeverity
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = ()
    source_checksum_refs: Tuple[str, ...] = ()

    @field_validator("evidence_refs", "source_checksum_refs")
    @classmethod
    def _refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)


class RuntimeRecoveryHandoffArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str = Field(..., min_length=1, max_length=128)
    artifact_id: str = Field(..., min_length=1, max_length=256)
    checksum: str = Field(..., min_length=64, max_length=64)


class RuntimeRecoveryHandoffPackage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str = Field(..., min_length=1, max_length=128)
    acceptance_id: str = Field(..., min_length=1, max_length=128)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    certification_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    destination_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    artifact_inventory: Tuple[RuntimeRecoveryHandoffArtifact, ...]
    source_checksums: Tuple[Tuple[str, str], ...]
    lifecycle_checksum: str = Field(..., min_length=64, max_length=64)
    acceptance_core_checksum: str = Field(..., min_length=64, max_length=64)
    evidence_refs: Tuple[str, ...]
    generated_at: int = Field(..., ge=0)
    attention_flags: Tuple[str, ...]
    safe_next_action: RuntimeRecoverySafeNextAction
    checksum: str = Field(..., min_length=64, max_length=64)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = _canonical_handoff_payload(values)
        payload.pop("checksum", None)
        return _checksum(payload)

    @classmethod
    def handoff_id_for(cls, **values: Any) -> str:
        payload = _canonical_handoff_payload(values)
        payload.pop("handoff_id", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_handoff_{_checksum(payload)[:24]}"

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeRecoveryHandoffPackage":
        if tuple(sorted(self.artifact_inventory, key=lambda item: (item.artifact_type, item.artifact_id))) != self.artifact_inventory:
            raise ValueError("runtime recovery handoff inventory is not sorted")
        if tuple(sorted(self.source_checksums)) != self.source_checksums:
            raise ValueError("runtime recovery handoff source checksums are not sorted")
        if _normalise(self.evidence_refs) != self.evidence_refs or _normalise(self.attention_flags) != self.attention_flags:
            raise ValueError("runtime recovery handoff references are not normalised")
        values = self.model_dump(exclude={"checksum"})
        if self.checksum != self.calculate_checksum(**values):
            raise ValueError("runtime recovery handoff checksum mismatch")
        if self.handoff_id != self.handoff_id_for(**values):
            raise ValueError("runtime recovery handoff identifier mismatch")
        return self


class RuntimeRecoveryAcceptance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acceptance_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_ACCEPTANCE_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    certification_id: str = Field(..., min_length=1, max_length=128)
    authorization_id: str = Field(..., min_length=1, max_length=128)
    authorization_checksum: str = Field(..., min_length=64, max_length=64)
    lifecycle_checksum: str = Field(..., min_length=64, max_length=64)
    acceptance_revision: int = Field(..., ge=1)
    status: RuntimeRecoveryAcceptanceStatus
    decision: RuntimeRecoveryAcceptanceDecision
    accepted_at: int = Field(..., ge=0)
    actor_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=1024)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    policy_id: str = Field(..., min_length=1, max_length=128)
    policy_fingerprint: str = Field(..., min_length=64, max_length=64)
    acceptance_checks: Tuple[RuntimeRecoveryAcceptanceCheck, ...]
    source_checksums: Tuple[Tuple[str, str], ...]
    evidence_refs: Tuple[str, ...]
    attention_flags: Tuple[str, ...]
    requires_human_acceptance: bool
    human_acceptance_ref: Optional[str] = Field(default=None, min_length=1, max_length=128)
    handoff_package: RuntimeRecoveryHandoffPackage
    checksum: str = Field(..., min_length=64, max_length=64)

    @classmethod
    def calculate_core_checksum(cls, **values: Any) -> str:
        payload = _canonical_acceptance_payload(values)
        payload.pop("acceptance_id", None)
        payload.pop("handoff_package", None)
        payload.pop("checksum", None)
        return _checksum(payload)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = _canonical_acceptance_payload(values)
        payload.pop("checksum", None)
        return _checksum(payload)

    @classmethod
    def acceptance_id_for(cls, **values: Any) -> str:
        payload = _canonical_acceptance_payload(values)
        payload.pop("acceptance_id", None)
        payload.pop("handoff_package", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_acceptance_{_checksum(payload)[:24]}"

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeRecoveryAcceptance":
        if self.schema_version != RUNTIME_RECOVERY_ACCEPTANCE_SCHEMA_VERSION:
            raise ValueError("unsupported runtime recovery acceptance schema version")
        if tuple(sorted(self.acceptance_checks, key=lambda item: item.check_code)) != self.acceptance_checks:
            raise ValueError("runtime recovery acceptance checks are not sorted")
        if tuple(sorted(self.source_checksums)) != self.source_checksums:
            raise ValueError("runtime recovery acceptance source checksums are not sorted")
        if _normalise(self.evidence_refs) != self.evidence_refs or _normalise(self.attention_flags) != self.attention_flags:
            raise ValueError("runtime recovery acceptance references are not normalised")
        values = self.model_dump(exclude={"checksum"})
        if self.checksum != self.calculate_checksum(**values):
            raise ValueError("runtime recovery acceptance checksum mismatch")
        if self.acceptance_id != self.acceptance_id_for(**values):
            raise ValueError("runtime recovery acceptance identifier mismatch")
        return self


class RuntimeRecoveryAcceptanceVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    checked_count: int = Field(..., ge=0)
    missing_refs: Tuple[str, ...] = ()
    checksum_mismatches: Tuple[str, ...] = ()
    identity_mismatches: Tuple[str, ...] = ()
    policy_failures: Tuple[str, ...] = ()
    acceptance_failures: Tuple[str, ...] = ()
    handoff_failures: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    attention_required: bool = False


def _canonical_handoff_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(values)
    payload["artifact_inventory"] = [
        item.model_dump(mode="json") if isinstance(item, BaseModel) else item
        for item in payload.get("artifact_inventory", ())
    ]
    payload["source_checksums"] = [list(item) for item in payload.get("source_checksums", ())]
    payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
    payload["attention_flags"] = list(payload.get("attention_flags", ()))
    payload["safe_next_action"] = _enum_value(payload["safe_next_action"])
    return payload


def _canonical_acceptance_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(values)
    payload["status"] = _enum_value(payload["status"])
    payload["decision"] = _enum_value(payload["decision"])
    payload["acceptance_checks"] = [
        item.model_dump(mode="json") if isinstance(item, BaseModel) else item
        for item in payload.get("acceptance_checks", ())
    ]
    payload["source_checksums"] = [list(item) for item in payload.get("source_checksums", ())]
    payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
    payload["attention_flags"] = list(payload.get("attention_flags", ()))
    handoff = payload.get("handoff_package")
    if isinstance(handoff, BaseModel):
        payload["handoff_package"] = handoff.model_dump(mode="json")
    return payload


def _human_failures(
    human: Optional[RuntimeRecoveryHumanAcceptance],
    authorization: RuntimeRecoveryAuthorizationArtifact,
    certification: RuntimeRecoveryCertification,
    policy: RuntimeRecoveryAcceptancePolicy,
    accepted_at: int,
) -> Tuple[str, ...]:
    if human is None:
        return ("explicit final human acceptance is required",)
    failures: list[str] = []
    try:
        RuntimeRecoveryHumanAcceptance.model_validate(human.model_dump())
    except Exception as exc:
        failures.append(f"invalid human acceptance artifact: {exc}")
    expected = (
        ("project", human.project_id, authorization.project_id),
        ("recovery", human.recovery_id, authorization.recovery_id),
        ("recovery revision", human.recovery_revision, authorization.recovery_revision),
        ("execution", human.execution_id, authorization.execution_id),
        ("certification", human.certification_id, certification.certification_id),
        ("certification checksum", human.certification_checksum, certification.checksum),
        ("authorization", human.authorization_id, authorization.authorization_id),
        ("authorization checksum", human.authorization_checksum, authorization.checksum),
    )
    failures.extend(f"human acceptance {name} mismatch" for name, actual, wanted in expected if actual != wanted)
    if human.accepted_at < authorization.authorized_at or human.accepted_at > accepted_at:
        failures.append("human acceptance timestamp is outside the acceptance window")
    if policy.allowed_acceptor_ids and human.actor_id not in policy.allowed_acceptor_ids:
        failures.append("human acceptance actor is not permitted by policy")
    if not human.accepted:
        failures.append("human acceptance decision rejects the recovery lifecycle")
    resolvable = set(authorization.evidence_refs) | set(certification.evidence_refs)
    if not set(human.evidence_refs).issubset(resolvable):
        failures.append("human acceptance evidence does not resolve")
    return _normalise(failures)


def _inventory(
    authorization: RuntimeRecoveryAuthorizationArtifact,
    certification: RuntimeRecoveryCertification,
) -> Tuple[RuntimeRecoveryHandoffArtifact, ...]:
    values = [
        RuntimeRecoveryHandoffArtifact(artifact_type="runtime_recovery_authorization", artifact_id=authorization.authorization_id, checksum=authorization.checksum),
        RuntimeRecoveryHandoffArtifact(artifact_type="runtime_recovery_certification", artifact_id=certification.certification_id, checksum=certification.checksum),
    ]
    values.extend(RuntimeRecoveryHandoffArtifact(
        artifact_type=item.artifact_type, artifact_id=item.artifact_id, checksum=item.checksum,
    ) for item in certification.artifact_inventory)
    return tuple(sorted(values, key=lambda item: (item.artifact_type, item.artifact_id)))


def _next_action(status: RuntimeRecoveryAcceptanceStatus, destination_id: Optional[str]) -> RuntimeRecoverySafeNextAction:
    if status == RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE:
        return RuntimeRecoverySafeNextAction.AWAITING_ACCEPTANCE
    if status == RuntimeRecoveryAcceptanceStatus.ATTENTION_REQUIRED:
        return RuntimeRecoverySafeNextAction.ATTENTION_REQUIRED
    if status == RuntimeRecoveryAcceptanceStatus.REJECTED:
        return RuntimeRecoverySafeNextAction.BLOCKED
    return RuntimeRecoverySafeNextAction.READY_FOR_MANUAL_HANDOFF if destination_id else RuntimeRecoverySafeNextAction.READY_FOR_REVIEW


class RuntimeRecoveryAcceptanceBuilder:
    """Consume Step 22 authorization and produce final evidence and handoff."""

    @classmethod
    def from_authorization(
        cls,
        *,
        authorization: RuntimeRecoveryAuthorizationArtifact,
        certification: RuntimeRecoveryCertification,
        artifacts: Mapping[str, Any],
        audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
        authorization_policy: RuntimeRecoveryAuthorizationPolicy,
        policy: RuntimeRecoveryAcceptancePolicy,
        actor_id: str,
        accepted_at: int,
        reason: str,
        correlation_id: str,
        causation_id: str,
        human_acceptance: Optional[RuntimeRecoveryHumanAcceptance] = None,
        authorization_approval: Optional[RuntimeRecoveryHumanApproval] = None,
        destination_id: Optional[str] = None,
        authorization_verification: Optional[RuntimeRecoveryAuthorizationVerificationResult] = None,
        acceptance_revision: int = 1,
    ) -> RuntimeRecoveryAcceptance:
        actor_id, reason = actor_id.strip(), reason.strip()
        correlation_id, causation_id = correlation_id.strip(), causation_id.strip()
        destination_id = destination_id.strip() if destination_id is not None else None
        if not all((actor_id, reason, correlation_id, causation_id)):
            raise ValueError("runtime recovery acceptance identity and reason inputs are required")
        if destination_id == "":
            raise ValueError("runtime recovery handoff destination cannot be blank")
        if accepted_at < authorization.authorized_at:
            raise ValueError("runtime recovery acceptance cannot predate authorization")
        if acceptance_revision < 1:
            raise ValueError("runtime recovery acceptance revision must be positive")
        audits = tuple(audit_artifacts)
        recomputed = verify_runtime_recovery_authorization(
            authorization, certification=certification, artifacts=artifacts,
            audit_artifacts=audits, policy=authorization_policy,
            approval=authorization_approval,
        )
        verification = recomputed
        if authorization_verification is not None and authorization_verification != recomputed:
            verification = recomputed.model_copy(update={
                "valid": False,
                "errors": _normalise((*recomputed.errors, "supplied authorization verification result does not match recomputation")),
                "attention_required": True,
            })
        requires_human = policy.require_human_acceptance
        human_errors = _human_failures(human_acceptance, authorization, certification, policy, accepted_at) if requires_human else ()
        identity_ok = not verification.identity_mismatches and all((
            authorization.certification_id == certification.certification_id,
            authorization.certification_checksum == certification.checksum,
            authorization.lifecycle_checksum == certification.lifecycle_checksum,
        ))
        source_ok = not verification.missing_refs and not verification.checksum_mismatches
        checks_data = (
            ("authorization_checksum", authorization.checksum == RuntimeRecoveryAuthorizationArtifact.calculate_checksum(**authorization.model_dump(exclude={"checksum"})), "authorization checksum is valid"),
            ("authorization_status", authorization.status == RuntimeRecoveryAuthorizationStatus.AUTHORIZED, f"authorization status is {authorization.status.value}"),
            ("authorization_verification", verification.valid, "authorization verification succeeded" if verification.valid else "authorization verification failed"),
            ("certification_continuity", authorization.certification_checksum == certification.checksum and authorization.certification_id == certification.certification_id, "certification continuity is intact"),
            ("evidence_resolution", source_ok, "source artifacts and evidence resolve"),
            ("human_acceptance", not requires_human or not human_errors, "human acceptance is valid" if requires_human and not human_errors else "human acceptance is required" if requires_human else "human acceptance is not required"),
            ("identity_continuity", identity_ok, "lifecycle identities are continuous"),
            ("lifecycle_continuity", authorization.lifecycle_checksum == certification.lifecycle_checksum, "lifecycle checksum is continuous"),
            ("no_critical_attention", not authorization.attention_flags, "no unresolved critical attention flags"),
            ("policy_fingerprint", bool(policy.fingerprint), "acceptance policy fingerprint is canonical"),
        )
        source_refs = tuple(value for _, value in authorization.source_checksums)
        checks = tuple(sorted((RuntimeRecoveryAcceptanceCheck(
            check_code=code, passed=passed,
            severity=RuntimeRecoveryAcceptanceCheckSeverity.INFO if code == "policy_fingerprint" else RuntimeRecoveryAcceptanceCheckSeverity.CRITICAL,
            reason=detail,
            evidence_refs=authorization.evidence_refs if code == "evidence_resolution" else (),
            source_checksum_refs=source_refs if code in {"evidence_resolution", "lifecycle_continuity"} else (),
        ) for code, passed, detail in checks_data), key=lambda item: item.check_code))
        hard_failed = any(not item.passed and item.check_code != "human_acceptance" for item in checks)
        if authorization.status == RuntimeRecoveryAuthorizationStatus.PENDING_APPROVAL:
            status, decision = RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE, RuntimeRecoveryAcceptanceDecision.AWAIT_ACCEPTANCE
        elif authorization.status == RuntimeRecoveryAuthorizationStatus.ATTENTION_REQUIRED:
            status, decision = RuntimeRecoveryAcceptanceStatus.ATTENTION_REQUIRED, RuntimeRecoveryAcceptanceDecision.REQUIRE_ATTENTION
        elif authorization.status == RuntimeRecoveryAuthorizationStatus.DENIED or hard_failed:
            status, decision = RuntimeRecoveryAcceptanceStatus.REJECTED, RuntimeRecoveryAcceptanceDecision.REJECT
        elif requires_human and human_errors:
            if human_acceptance is None:
                status, decision = RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE, RuntimeRecoveryAcceptanceDecision.AWAIT_ACCEPTANCE
            else:
                status, decision = RuntimeRecoveryAcceptanceStatus.REJECTED, RuntimeRecoveryAcceptanceDecision.REJECT
        else:
            status, decision = RuntimeRecoveryAcceptanceStatus.ACCEPTED, RuntimeRecoveryAcceptanceDecision.ACCEPT
        flags = _normalise((*authorization.attention_flags, *human_errors))
        source_checksums = tuple(sorted((
            *authorization.source_checksums,
            (f"runtime_recovery_authorization:{authorization.authorization_id}", authorization.checksum),
            (f"runtime_recovery_certification:{certification.certification_id}", certification.checksum),
        )))
        values = dict(
            schema_version=RUNTIME_RECOVERY_ACCEPTANCE_SCHEMA_VERSION,
            project_id=authorization.project_id, recovery_id=authorization.recovery_id,
            recovery_revision=authorization.recovery_revision, execution_id=authorization.execution_id,
            certification_id=authorization.certification_id, authorization_id=authorization.authorization_id,
            authorization_checksum=authorization.checksum, lifecycle_checksum=authorization.lifecycle_checksum,
            acceptance_revision=acceptance_revision, status=status, decision=decision,
            accepted_at=accepted_at, actor_id=actor_id, reason=reason,
            correlation_id=correlation_id, causation_id=causation_id,
            policy_id=policy.policy_id, policy_fingerprint=policy.fingerprint,
            acceptance_checks=checks, source_checksums=source_checksums,
            evidence_refs=_normalise(authorization.evidence_refs), attention_flags=flags,
            requires_human_acceptance=requires_human,
            human_acceptance_ref=human_acceptance.acceptance_ref_id if human_acceptance and not human_errors else None,
        )
        acceptance_id = RuntimeRecoveryAcceptance.acceptance_id_for(**values)
        core_checksum = RuntimeRecoveryAcceptance.calculate_core_checksum(acceptance_id=acceptance_id, **values)
        handoff_values = dict(
            acceptance_id=acceptance_id, authorization_id=authorization.authorization_id,
            certification_id=certification.certification_id, project_id=authorization.project_id,
            recovery_id=authorization.recovery_id, recovery_revision=authorization.recovery_revision,
            execution_id=authorization.execution_id, destination_id=destination_id,
            artifact_inventory=_inventory(authorization, certification), source_checksums=source_checksums,
            lifecycle_checksum=authorization.lifecycle_checksum, acceptance_core_checksum=core_checksum,
            evidence_refs=_normalise(authorization.evidence_refs), generated_at=accepted_at,
            attention_flags=flags, safe_next_action=_next_action(status, destination_id),
        )
        handoff_id = RuntimeRecoveryHandoffPackage.handoff_id_for(**handoff_values)
        handoff_checksum = RuntimeRecoveryHandoffPackage.calculate_checksum(handoff_id=handoff_id, **handoff_values)
        handoff = RuntimeRecoveryHandoffPackage(handoff_id=handoff_id, **handoff_values, checksum=handoff_checksum)
        checksum = RuntimeRecoveryAcceptance.calculate_checksum(acceptance_id=acceptance_id, **values, handoff_package=handoff)
        return RuntimeRecoveryAcceptance(acceptance_id=acceptance_id, **values, handoff_package=handoff, checksum=checksum)


def verify_runtime_recovery_acceptance(
    acceptance: RuntimeRecoveryAcceptance,
    *,
    authorization: RuntimeRecoveryAuthorizationArtifact,
    certification: RuntimeRecoveryCertification,
    artifacts: Mapping[str, Any],
    audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
    authorization_policy: RuntimeRecoveryAuthorizationPolicy,
    policy: RuntimeRecoveryAcceptancePolicy,
    human_acceptance: Optional[RuntimeRecoveryHumanAcceptance] = None,
    authorization_approval: Optional[RuntimeRecoveryHumanApproval] = None,
) -> RuntimeRecoveryAcceptanceVerificationResult:
    missing: list[str] = []
    checksums: list[str] = []
    identities: list[str] = []
    policy_failures: list[str] = []
    acceptance_failures: list[str] = []
    handoff_failures: list[str] = []
    errors: list[str] = []
    checked = 0
    try:
        RuntimeRecoveryAcceptance.model_validate(acceptance.model_dump())
        checked += 2
    except Exception as exc:
        errors.append(f"invalid acceptance artifact: {exc}")
    if acceptance.authorization_checksum != authorization.checksum:
        checksums.append("authorization")
    if acceptance.lifecycle_checksum != certification.lifecycle_checksum:
        checksums.append("lifecycle")
    expected = (
        ("project_id", acceptance.project_id, authorization.project_id),
        ("recovery_id", acceptance.recovery_id, authorization.recovery_id),
        ("recovery_revision", acceptance.recovery_revision, authorization.recovery_revision),
        ("execution_id", acceptance.execution_id, authorization.execution_id),
        ("certification_id", acceptance.certification_id, certification.certification_id),
        ("authorization_id", acceptance.authorization_id, authorization.authorization_id),
    )
    identities.extend(name for name, actual, wanted in expected if actual != wanted)
    if acceptance.policy_id != policy.policy_id or acceptance.policy_fingerprint != policy.fingerprint:
        policy_failures.append("policy fingerprint mismatch")
    audits = tuple(audit_artifacts)
    auth_verification = verify_runtime_recovery_authorization(
        authorization, certification=certification, artifacts=artifacts,
        audit_artifacts=audits, policy=authorization_policy,
        approval=authorization_approval,
    )
    missing.extend(auth_verification.missing_refs)
    checksums.extend(auth_verification.checksum_mismatches)
    identities.extend(auth_verification.identity_mismatches)
    if not auth_verification.valid:
        acceptance_failures.append("authorization verification failed")
    try:
        rebuilt = RuntimeRecoveryAcceptanceBuilder.from_authorization(
            authorization=authorization, certification=certification, artifacts=artifacts,
            audit_artifacts=audits, authorization_policy=authorization_policy, policy=policy,
            actor_id=acceptance.actor_id, accepted_at=acceptance.accepted_at,
            reason=acceptance.reason, correlation_id=acceptance.correlation_id,
            causation_id=acceptance.causation_id, human_acceptance=human_acceptance,
            authorization_approval=authorization_approval,
            destination_id=acceptance.handoff_package.destination_id,
            authorization_verification=auth_verification,
            acceptance_revision=acceptance.acceptance_revision,
        )
        checked += len(rebuilt.acceptance_checks) + len(rebuilt.handoff_package.artifact_inventory) + 8
        if rebuilt.acceptance_checks != acceptance.acceptance_checks:
            policy_failures.append("acceptance check results mismatch")
        if rebuilt.status != acceptance.status or rebuilt.decision != acceptance.decision:
            acceptance_failures.append("acceptance decision or status mismatch")
        if rebuilt.handoff_package.artifact_inventory != acceptance.handoff_package.artifact_inventory:
            handoff_failures.append("handoff inventory incomplete or inconsistent")
        if rebuilt.handoff_package.safe_next_action != acceptance.handoff_package.safe_next_action:
            handoff_failures.append("handoff safe next action mismatch")
        if rebuilt.handoff_package != acceptance.handoff_package:
            handoff_failures.append("handoff package is incompatible with deterministic rebuild")
        if rebuilt != acceptance:
            errors.append("acceptance is incompatible with deterministic rebuild")
    except Exception as exc:
        errors.append(f"deterministic acceptance rebuild failed: {exc}")
    if acceptance.requires_human_acceptance:
        failures = _human_failures(human_acceptance, authorization, certification, policy, acceptance.accepted_at)
        expected_pending = human_acceptance is None and acceptance.status == RuntimeRecoveryAcceptanceStatus.PENDING_ACCEPTANCE
        if failures and not expected_pending:
            acceptance_failures.extend(failures)
        if human_acceptance is not None and acceptance.human_acceptance_ref != human_acceptance.acceptance_ref_id:
            acceptance_failures.append("human acceptance reference mismatch")
    inventory_checksums = {(item.artifact_type, item.artifact_id): item.checksum for item in acceptance.handoff_package.artifact_inventory}
    expected_inventory = {(item.artifact_type, item.artifact_id): item.checksum for item in _inventory(authorization, certification)}
    if inventory_checksums != expected_inventory:
        handoff_failures.append("handoff inventory incomplete or inconsistent")
    core = RuntimeRecoveryAcceptance.calculate_core_checksum(**acceptance.model_dump(exclude={"checksum", "handoff_package"}))
    if acceptance.handoff_package.acceptance_core_checksum != core:
        handoff_failures.append("handoff acceptance checksum reference mismatch")
    valid = not (missing or checksums or identities or policy_failures or acceptance_failures or handoff_failures or errors)
    return RuntimeRecoveryAcceptanceVerificationResult(
        valid=valid, checked_count=checked, missing_refs=_normalise(missing),
        checksum_mismatches=_normalise(checksums), identity_mismatches=_normalise(identities),
        policy_failures=_normalise(policy_failures), acceptance_failures=_normalise(acceptance_failures),
        handoff_failures=_normalise(handoff_failures), errors=_normalise(errors),
        attention_required=(acceptance.status != RuntimeRecoveryAcceptanceStatus.ACCEPTED or not valid or bool(acceptance.attention_flags) or acceptance.handoff_package.safe_next_action in {RuntimeRecoverySafeNextAction.BLOCKED, RuntimeRecoverySafeNextAction.ATTENTION_REQUIRED, RuntimeRecoverySafeNextAction.AWAITING_ACCEPTANCE}),
    )


def runtime_recovery_acceptances_requiring_attention(
    acceptances: Iterable[RuntimeRecoveryAcceptance],
    *,
    verification_results: Optional[Mapping[str, RuntimeRecoveryAcceptanceVerificationResult]] = None,
) -> Tuple[RuntimeRecoveryAcceptance, ...]:
    results = verification_results or {}
    blocked = {RuntimeRecoverySafeNextAction.BLOCKED, RuntimeRecoverySafeNextAction.ATTENTION_REQUIRED, RuntimeRecoverySafeNextAction.AWAITING_ACCEPTANCE}
    return tuple(sorted((item for item in acceptances if (
        item.status != RuntimeRecoveryAcceptanceStatus.ACCEPTED
        or bool(item.attention_flags)
        or item.handoff_package.safe_next_action in blocked
        or (item.acceptance_id in results and not results[item.acceptance_id].valid)
    )), key=lambda item: (item.accepted_at, item.acceptance_id)))
