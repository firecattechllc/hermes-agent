"""Deterministic final authorization of certified runtime recovery lifecycles.

This module records a governance decision only.  It never executes a recovery,
changes a lifecycle artifact, or substitutes for a required human approval.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .runtime_recovery_audit import RuntimeRecoveryAuditEvent
from .runtime_recovery_certification import (
    RuntimeRecoveryCertification,
    RuntimeRecoveryCertificationStatus,
    RuntimeRecoveryCertificationVerificationResult,
    verify_runtime_recovery_certification,
)


RUNTIME_RECOVERY_AUTHORIZATION_SCHEMA_VERSION = 1


class RuntimeRecoveryAuthorizationStatus(str, Enum):
    AUTHORIZED = "authorized"
    DENIED = "denied"
    ATTENTION_REQUIRED = "attention_required"
    PENDING_APPROVAL = "pending_approval"


class RuntimeRecoveryAuthorizationDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"
    REQUIRE_ATTENTION = "require_attention"
    AWAIT_APPROVAL = "await_approval"


class RuntimeRecoveryAuthorizationCheckSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


def _checksum(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalise(values: Iterable[Any]) -> Tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


class RuntimeRecoveryAuthorizationPolicy(BaseModel):
    """Small, closed policy surface for final lifecycle acceptance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(default="runtime-recovery-final-acceptance-v1", min_length=1, max_length=128)
    require_certified_status: bool = True
    require_certification_verification: bool = True
    require_no_critical_attention: bool = True
    require_resolved_evidence: bool = True
    require_intact_audit_chain: bool = True
    require_identity_continuity: bool = True
    require_human_approval: bool = False
    approval_required_actions: Tuple[str, ...] = ()
    denied_actions: Tuple[str, ...] = ()
    denied_terminal_states: Tuple[str, ...] = ()
    allowed_approver_ids: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _preserve_fail_closed_guards(self) -> "RuntimeRecoveryAuthorizationPolicy":
        required = (
            self.require_certified_status,
            self.require_certification_verification,
            self.require_no_critical_attention,
            self.require_resolved_evidence,
            self.require_intact_audit_chain,
            self.require_identity_continuity,
        )
        if not all(required):
            raise ValueError("runtime recovery authorization safety requirements cannot be disabled")
        return self

    @field_validator(
        "approval_required_actions", "denied_actions", "denied_terminal_states",
        "allowed_approver_ids",
    )
    @classmethod
    def _normalise_sets(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)

    @property
    def fingerprint(self) -> str:
        return _checksum(self.model_dump(mode="json"))


class RuntimeRecoveryHumanApproval(BaseModel):
    """Explicit human approval bound to one exact certification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    certification_id: str = Field(..., min_length=1, max_length=128)
    certification_checksum: str = Field(..., min_length=64, max_length=64)
    actor_id: str = Field(..., min_length=1, max_length=256)
    approved_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = ()
    checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator("evidence_refs")
    @classmethod
    def _normalise_evidence(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("checksum", None)
        payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
        return _checksum(payload)

    @classmethod
    def approval_id_for(cls, **values: Any) -> str:
        payload = dict(values)
        payload.pop("approval_id", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_human_approval_{_checksum(payload)[:24]}"

    @classmethod
    def build(cls, **values: Any) -> "RuntimeRecoveryHumanApproval":
        values["evidence_refs"] = _normalise(values.get("evidence_refs", ()))
        approval_id = cls.approval_id_for(**values)
        checksum = cls.calculate_checksum(approval_id=approval_id, **values)
        return cls(approval_id=approval_id, **values, checksum=checksum)

    @model_validator(mode="after")
    def _validate_approval(self) -> "RuntimeRecoveryHumanApproval":
        if self.checksum != self.calculate_checksum(**self.model_dump(exclude={"checksum"})):
            raise ValueError("runtime recovery human approval checksum mismatch")
        if self.approval_id != self.approval_id_for(**self.model_dump(exclude={"checksum"})):
            raise ValueError("runtime recovery human approval identifier mismatch")
        return self


class RuntimeRecoveryAuthorizationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    check_code: str = Field(..., min_length=1, max_length=128)
    passed: bool
    severity: RuntimeRecoveryAuthorizationCheckSeverity
    reason: str = Field(..., min_length=1, max_length=1024)
    evidence_refs: Tuple[str, ...] = ()
    source_checksum_refs: Tuple[str, ...] = ()

    @field_validator("evidence_refs", "source_checksum_refs")
    @classmethod
    def _normalise_refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _normalise(values)


class RuntimeRecoveryAuthorizationArtifact(BaseModel):
    """Immutable final-acceptance decision for one certified lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authorization_id: str = Field(..., min_length=1, max_length=128)
    schema_version: int = RUNTIME_RECOVERY_AUTHORIZATION_SCHEMA_VERSION
    project_id: str = Field(..., min_length=1, max_length=128)
    recovery_id: str = Field(..., min_length=1, max_length=128)
    recovery_revision: int = Field(..., ge=1)
    execution_id: str = Field(..., min_length=1, max_length=128)
    certification_id: str = Field(..., min_length=1, max_length=128)
    certification_checksum: str = Field(..., min_length=64, max_length=64)
    lifecycle_checksum: str = Field(..., min_length=64, max_length=64)
    authorization_revision: int = Field(..., ge=1)
    status: RuntimeRecoveryAuthorizationStatus
    decision: RuntimeRecoveryAuthorizationDecision
    authorized_at: int = Field(..., ge=0)
    actor_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=1024)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    policy_id: str = Field(..., min_length=1, max_length=128)
    policy_fingerprint: str = Field(..., min_length=64, max_length=64)
    policy_checks: Tuple[RuntimeRecoveryAuthorizationCheck, ...]
    evidence_refs: Tuple[str, ...]
    source_checksums: Tuple[Tuple[str, str], ...]
    attention_flags: Tuple[str, ...]
    requires_human_approval: bool
    human_approval_ref: Optional[str] = Field(default=None, min_length=1, max_length=128)
    checksum: str = Field(..., min_length=64, max_length=64)

    @classmethod
    def calculate_checksum(cls, **values: Any) -> str:
        payload = _canonical_authorization_payload(values)
        payload.pop("checksum", None)
        return _checksum(payload)

    @classmethod
    def authorization_id_for(cls, **values: Any) -> str:
        payload = _canonical_authorization_payload(values)
        payload.pop("authorization_id", None)
        payload.pop("checksum", None)
        return f"runtime_recovery_authorization_{_checksum(payload)[:24]}"

    @model_validator(mode="after")
    def _validate_artifact(self) -> "RuntimeRecoveryAuthorizationArtifact":
        if self.schema_version != RUNTIME_RECOVERY_AUTHORIZATION_SCHEMA_VERSION:
            raise ValueError("unsupported runtime recovery authorization schema version")
        if tuple(sorted(check.check_code for check in self.policy_checks)) != tuple(
            check.check_code for check in self.policy_checks
        ):
            raise ValueError("runtime recovery authorization checks are not sorted")
        if tuple(sorted(self.source_checksums)) != self.source_checksums:
            raise ValueError("runtime recovery authorization source checksums are not sorted")
        if _normalise(self.evidence_refs) != self.evidence_refs or _normalise(self.attention_flags) != self.attention_flags:
            raise ValueError("runtime recovery authorization references are not normalised")
        if self.checksum != self.calculate_checksum(**self.model_dump(exclude={"checksum"})):
            raise ValueError("runtime recovery authorization checksum mismatch")
        if self.authorization_id != self.authorization_id_for(**self.model_dump(exclude={"checksum"})):
            raise ValueError("runtime recovery authorization identifier mismatch")
        return self


class RuntimeRecoveryAuthorizationVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    valid: bool
    checked_count: int = Field(..., ge=0)
    missing_refs: Tuple[str, ...] = ()
    checksum_mismatches: Tuple[str, ...] = ()
    identity_mismatches: Tuple[str, ...] = ()
    policy_failures: Tuple[str, ...] = ()
    approval_failures: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    attention_required: bool = False


def _canonical_authorization_payload(values: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(values)
    payload["status"] = _enum_value(payload["status"])
    payload["decision"] = _enum_value(payload["decision"])
    payload["policy_checks"] = [
        item.model_dump(mode="json") if isinstance(item, BaseModel) else item
        for item in payload.get("policy_checks", ())
    ]
    payload["evidence_refs"] = list(payload.get("evidence_refs", ()))
    payload["source_checksums"] = [list(item) for item in payload.get("source_checksums", ())]
    payload["attention_flags"] = list(payload.get("attention_flags", ()))
    return payload


def _approval_failures(
    approval: Optional[RuntimeRecoveryHumanApproval],
    certification: RuntimeRecoveryCertification,
    policy: RuntimeRecoveryAuthorizationPolicy,
    authorized_at: int,
) -> Tuple[str, ...]:
    if approval is None:
        return ("explicit human approval is required",)
    failures: list[str] = []
    try:
        RuntimeRecoveryHumanApproval.model_validate(approval.model_dump())
    except Exception as exc:
        failures.append(f"invalid human approval artifact: {exc}")
    expected = (
        ("project", approval.project_id, certification.project_id),
        ("recovery", approval.recovery_id, certification.recovery_id),
        ("recovery revision", approval.recovery_revision, certification.recovery_revision),
        ("execution", approval.execution_id, certification.execution_id),
        ("certification", approval.certification_id, certification.certification_id),
        ("certification checksum", approval.certification_checksum, certification.checksum),
    )
    failures.extend(f"human approval {name} mismatch" for name, actual, wanted in expected if actual != wanted)
    if approval.approved_at < certification.certified_at or approval.approved_at > authorized_at:
        failures.append("human approval timestamp is outside the authorization window")
    if policy.allowed_approver_ids and approval.actor_id not in policy.allowed_approver_ids:
        failures.append("human approval actor is not permitted by policy")
    if not set(approval.evidence_refs).issubset(certification.evidence_refs):
        failures.append("human approval evidence does not resolve to certification evidence")
    return _normalise(failures)


def _artifact_values(artifacts: Mapping[str, Any]) -> tuple[str, str]:
    action = ""
    terminal_state = ""
    for artifact in artifacts.values():
        value = getattr(artifact, "action", None)
        if value is not None and not action:
            action = str(_enum_value(value))
        value = getattr(artifact, "resulting_execution_state", None)
        if value is not None:
            terminal_state = str(_enum_value(value))
    return action, terminal_state


class RuntimeRecoveryAuthorizationBuilder:
    """Evaluate fixed governance rules and emit an immutable decision artifact."""

    @classmethod
    def from_certification(
        cls,
        *,
        certification: RuntimeRecoveryCertification,
        artifacts: Mapping[str, Any],
        audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
        policy: RuntimeRecoveryAuthorizationPolicy,
        actor_id: str,
        authorized_at: int,
        reason: str,
        correlation_id: str,
        causation_id: str,
        approval: Optional[RuntimeRecoveryHumanApproval] = None,
        certification_verification: Optional[RuntimeRecoveryCertificationVerificationResult] = None,
        authorization_revision: int = 1,
    ) -> RuntimeRecoveryAuthorizationArtifact:
        actor_id, reason = actor_id.strip(), reason.strip()
        correlation_id, causation_id = correlation_id.strip(), causation_id.strip()
        if not all((actor_id, reason, correlation_id, causation_id)):
            raise ValueError("runtime recovery authorization identity and reason inputs are required")
        if authorized_at < certification.certified_at:
            raise ValueError("runtime recovery authorization cannot predate certification")
        if authorization_revision < 1:
            raise ValueError("runtime recovery authorization revision must be positive")
        audits = tuple(audit_artifacts)
        recomputed_verification = verify_runtime_recovery_certification(
            certification, artifacts=artifacts, audit_artifacts=audits
        )
        verification = recomputed_verification
        if (
            certification_verification is not None
            and certification_verification != recomputed_verification
        ):
            verification = recomputed_verification.model_copy(update={
                "valid": False,
                "errors": _normalise((
                    *recomputed_verification.errors,
                    "supplied certification verification result does not match recomputation",
                )),
                "attention_required": True,
            })
        action, terminal_state = _artifact_values(artifacts)
        requires_approval = policy.require_human_approval or action in policy.approval_required_actions
        approval_errors = _approval_failures(approval, certification, policy, authorized_at) if requires_approval else ()

        check_values = (
            ("audit_chain_intact", verification.valid and not any("audit" in error for error in verification.errors), RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, "certification audit chain is intact"),
            ("certification_status", certification.status != RuntimeRecoveryCertificationStatus.REJECTED, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, f"certification status is {certification.status.value}"),
            ("certification_verification", verification.valid, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, "certification verification succeeded" if verification.valid else "certification verification failed"),
            ("evidence_resolved", not verification.missing_refs and not verification.checksum_mismatches, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, "certification evidence and source checksums resolve"),
            ("human_approval", not requires_approval or not approval_errors, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, "human approval is valid" if requires_approval and not approval_errors else "human approval is required" if requires_approval else "human approval is not required"),
            ("identity_continuity", not verification.identity_mismatches, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, "certification identities are continuous"),
            ("no_critical_attention", not certification.attention_flags, RuntimeRecoveryAuthorizationCheckSeverity.WARNING, "no unresolved critical attention flags"),
            ("policy_action_allowed", action not in policy.denied_actions, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, f"recovery action {action or 'unknown'} is permitted"),
            ("policy_terminal_state_allowed", terminal_state not in policy.denied_terminal_states, RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL, f"terminal state {terminal_state or 'unknown'} is permitted"),
            ("policy_fingerprint", bool(policy.fingerprint), RuntimeRecoveryAuthorizationCheckSeverity.INFO, "policy fingerprint is canonical"),
        )
        source_refs = tuple(value for _, value in certification.source_checksums)
        checks = tuple(sorted((RuntimeRecoveryAuthorizationCheck(
            check_code=code, passed=passed, severity=severity, reason=detail,
            evidence_refs=certification.evidence_refs if code == "evidence_resolved" else (),
            source_checksum_refs=source_refs if code in {"audit_chain_intact", "evidence_resolved"} else (),
        ) for code, passed, severity, detail in check_values), key=lambda item: item.check_code))

        hard_failed = any(not item.passed and item.severity == RuntimeRecoveryAuthorizationCheckSeverity.CRITICAL for item in checks if item.check_code != "human_approval")
        attention = bool(certification.attention_flags)
        if hard_failed:
            status, decision = RuntimeRecoveryAuthorizationStatus.DENIED, RuntimeRecoveryAuthorizationDecision.DENY
        elif attention:
            status, decision = RuntimeRecoveryAuthorizationStatus.ATTENTION_REQUIRED, RuntimeRecoveryAuthorizationDecision.REQUIRE_ATTENTION
        elif requires_approval and approval_errors:
            if approval is None:
                status, decision = RuntimeRecoveryAuthorizationStatus.PENDING_APPROVAL, RuntimeRecoveryAuthorizationDecision.AWAIT_APPROVAL
            else:
                status, decision = RuntimeRecoveryAuthorizationStatus.DENIED, RuntimeRecoveryAuthorizationDecision.DENY
        else:
            status, decision = RuntimeRecoveryAuthorizationStatus.AUTHORIZED, RuntimeRecoveryAuthorizationDecision.APPROVE

        flags = _normalise((*certification.attention_flags, *approval_errors))
        values = dict(
            schema_version=RUNTIME_RECOVERY_AUTHORIZATION_SCHEMA_VERSION,
            project_id=certification.project_id, recovery_id=certification.recovery_id,
            recovery_revision=certification.recovery_revision, execution_id=certification.execution_id,
            certification_id=certification.certification_id,
            certification_checksum=certification.checksum,
            lifecycle_checksum=certification.lifecycle_checksum,
            authorization_revision=authorization_revision, status=status, decision=decision,
            authorized_at=authorized_at, actor_id=actor_id, reason=reason,
            correlation_id=correlation_id, causation_id=causation_id,
            policy_id=policy.policy_id, policy_fingerprint=policy.fingerprint,
            policy_checks=checks, evidence_refs=_normalise(certification.evidence_refs),
            source_checksums=tuple(sorted(certification.source_checksums)),
            attention_flags=flags, requires_human_approval=requires_approval,
            human_approval_ref=approval.approval_id if approval and not approval_errors else None,
        )
        authorization_id = RuntimeRecoveryAuthorizationArtifact.authorization_id_for(**values)
        checksum = RuntimeRecoveryAuthorizationArtifact.calculate_checksum(authorization_id=authorization_id, **values)
        return RuntimeRecoveryAuthorizationArtifact(authorization_id=authorization_id, **values, checksum=checksum)


def verify_runtime_recovery_authorization(
    authorization: RuntimeRecoveryAuthorizationArtifact,
    *,
    certification: RuntimeRecoveryCertification,
    artifacts: Mapping[str, Any],
    audit_artifacts: Iterable[RuntimeRecoveryAuditEvent],
    policy: RuntimeRecoveryAuthorizationPolicy,
    approval: Optional[RuntimeRecoveryHumanApproval] = None,
) -> RuntimeRecoveryAuthorizationVerificationResult:
    missing: list[str] = []
    checksums: list[str] = []
    identities: list[str] = []
    policy_failures: list[str] = []
    approval_failures: list[str] = []
    errors: list[str] = []
    checked = 0
    try:
        RuntimeRecoveryAuthorizationArtifact.model_validate(authorization.model_dump())
        checked += 2
    except Exception as exc:
        errors.append(f"invalid authorization artifact: {exc}")
    if authorization.certification_checksum != certification.checksum:
        checksums.append("certification")
    if authorization.lifecycle_checksum != certification.lifecycle_checksum:
        checksums.append("lifecycle")
    expected_identities = (
        ("project_id", authorization.project_id, certification.project_id),
        ("recovery_id", authorization.recovery_id, certification.recovery_id),
        ("recovery_revision", authorization.recovery_revision, certification.recovery_revision),
        ("execution_id", authorization.execution_id, certification.execution_id),
        ("certification_id", authorization.certification_id, certification.certification_id),
    )
    identities.extend(name for name, actual, wanted in expected_identities if actual != wanted)
    if authorization.policy_id != policy.policy_id or authorization.policy_fingerprint != policy.fingerprint:
        policy_failures.append("policy fingerprint mismatch")
    audits = tuple(audit_artifacts)
    cert_verification = verify_runtime_recovery_certification(
        certification, artifacts=artifacts, audit_artifacts=audits
    )
    missing.extend(cert_verification.missing_refs)
    checksums.extend(cert_verification.checksum_mismatches)
    identities.extend(cert_verification.identity_mismatches)
    if not cert_verification.valid:
        errors.append("certification verification failed")
    try:
        rebuilt = RuntimeRecoveryAuthorizationBuilder.from_certification(
            certification=certification, artifacts=artifacts, audit_artifacts=audits,
            policy=policy, actor_id=authorization.actor_id, authorized_at=authorization.authorized_at,
            reason=authorization.reason, correlation_id=authorization.correlation_id,
            causation_id=authorization.causation_id, approval=approval,
            certification_verification=cert_verification,
            authorization_revision=authorization.authorization_revision,
        )
        checked += len(rebuilt.policy_checks) + 5
        if rebuilt.policy_checks != authorization.policy_checks:
            policy_failures.append("policy check results mismatch")
        if rebuilt.status != authorization.status or rebuilt.decision != authorization.decision:
            policy_failures.append("authorization decision or status mismatch")
        if rebuilt != authorization:
            errors.append("authorization is incompatible with deterministic rebuild")
    except Exception as exc:
        errors.append(f"deterministic authorization rebuild failed: {exc}")
    if authorization.requires_human_approval:
        approval_failures.extend(_approval_failures(approval, certification, policy, authorization.authorized_at))
        if approval is None and authorization.status != RuntimeRecoveryAuthorizationStatus.PENDING_APPROVAL:
            approval_failures.append("missing approval must remain pending")
        if approval is not None and authorization.human_approval_ref != approval.approval_id:
            approval_failures.append("human approval reference mismatch")
    expected_pending_approval = (
        approval is None
        and authorization.requires_human_approval
        and authorization.status == RuntimeRecoveryAuthorizationStatus.PENDING_APPROVAL
        and approval_failures == ["explicit human approval is required"]
    )
    blocking_approval_failure = bool(approval_failures) and not expected_pending_approval
    valid = not (
        missing or checksums or identities or policy_failures
        or blocking_approval_failure or errors
    )
    return RuntimeRecoveryAuthorizationVerificationResult(
        valid=valid, checked_count=checked, missing_refs=_normalise(missing),
        checksum_mismatches=_normalise(checksums), identity_mismatches=_normalise(identities),
        policy_failures=_normalise(policy_failures), approval_failures=_normalise(approval_failures),
        errors=_normalise(errors),
        attention_required=(authorization.status != RuntimeRecoveryAuthorizationStatus.AUTHORIZED or not valid or bool(authorization.attention_flags)),
    )


def runtime_recovery_authorizations_requiring_attention(
    authorizations: Iterable[RuntimeRecoveryAuthorizationArtifact],
    *,
    verification_results: Optional[Mapping[str, RuntimeRecoveryAuthorizationVerificationResult]] = None,
) -> Tuple[RuntimeRecoveryAuthorizationArtifact, ...]:
    results = verification_results or {}
    return tuple(sorted((item for item in authorizations if (
        item.status != RuntimeRecoveryAuthorizationStatus.AUTHORIZED
        or bool(item.attention_flags)
        or (item.authorization_id in results and not results[item.authorization_id].valid)
    )), key=lambda item: (item.authorized_at, item.authorization_id)))
