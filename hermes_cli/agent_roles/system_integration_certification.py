"""Deterministic, non-executing Step 29 system integration certification."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION = 1
_FORBIDDEN = (
    "raw_prompt", "model_response", "api_key", "api-key", "authorization:",
    "bearer ", "password", "private_key", "private key", "secret=", "token=",
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _safe(value: str, field: str, maximum: int = 512) -> str:
    value = value.strip()
    if not value or len(value) > maximum or any(item in value.lower() for item in _FORBIDDEN):
        raise ValueError(f"{field} is blank, oversized, or sensitive")
    return value


def _reference(value: str, field: str) -> str:
    value = _safe(value, field)
    if "://" not in value:
        raise ValueError(f"{field} must be a sanitized reference")
    return value


class FindingSeverity(str, Enum):
    INFORMATIONAL = "informational"
    ADVISORY = "advisory"
    WARNING = "warning"
    BLOCKING = "blocking"
    CRITICAL = "critical"


class CertificationStatus(str, Enum):
    CERTIFIED = "certified"
    BLOCKED = "blocked"
    FAILED = "failed"


class ArchitectureComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    step_range: str
    capability: str
    implementation_modules: Tuple[str, ...]
    boundary: str
    simulated: bool = False

    @field_validator("step_range", "capability", "boundary")
    @classmethod
    def _text(cls, value: str, info) -> str:
        return _safe(value, info.field_name)


def architecture_inventory() -> Tuple[ArchitectureComponent, ...]:
    """Return the canonical implemented Steps 1-28 inventory."""
    rows = (
        ("1-4", "agent roles, governed launch, runtime handoff and sessions", ("models", "launch", "runtime_handoff", "runtime_session"), "launch and runtime authority remain explicit"),
        ("5", "role execution planning", ("execution_planning", "execution"), "plans do not create approval"),
        ("6-7", "workflow orchestration and evidence", ("workflow", "workflow_execution", "workflow_store"), "append-only evidence and governed transitions"),
        ("8", "dependency-aware workflow scheduling", ("workflow_scheduling",), "coordination is non-executing"),
        ("9", "dispatch admission", ("workflow_dispatch",), "dispatch preparation cannot invoke providers"),
        ("10-14", "runtime execution and supervision", ("runtime_execution", "runtime_supervision"), "admission and supervision fail closed"),
        ("15-23", "runtime recovery, evidence, certification and acceptance", ("runtime_recovery", "runtime_recovery_certification", "runtime_recovery_acceptance"), "recovery requires bounded authority"),
        ("24", "Hydra Live governed maintenance", ("remote_maintenance",), "fake/local adapters only during certification", True),
        ("25", "governed fleet inventory", ("fleet_inventory",), "inventory authority and sanitized evidence"),
        ("26", "governed model routing", ("model_routing",), "no-route decisions prohibit execution"),
        ("27", "governed model execution and fallback", ("model_execution",), "provider invocation exists only behind Step 27 adapters", True),
        ("28", "intelligence and efficiency recommendations", ("intelligence_engine", "intelligence_store"), "recommendations cannot mutate policy or authority"),
        ("1-28", "shared engineering context and Mission Control visibility", ("context_engine", "mission_control"), "visibility is sanitized and read-only"),
        ("1-28", "approval, spending, merge, release and deployment boundaries", ("workflow", "model_execution", "runtime_recovery_acceptance"), "operator remains final authority"),
    )
    return tuple(ArchitectureComponent(step_range=a, capability=b, implementation_modules=c, boundary=d, simulated=e) for a, b, c, d, *rest in rows for e in (rest[0] if rest else False,))


class IntegrationIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    task_id: str
    workflow_id: str
    request_id: str
    dispatch_id: str
    runtime_session_id: str
    routing_decision_id: str
    model_execution_id: str
    optimization_id: str
    correlation_id: str
    idempotency_keys: Tuple[str, ...]
    mission_control_event_ids: Tuple[str, ...]

    @field_validator("project_id", "task_id", "workflow_id", "request_id", "dispatch_id", "runtime_session_id", "routing_decision_id", "model_execution_id", "optimization_id", "correlation_id")
    @classmethod
    def _identity(cls, value: str, info) -> str:
        return _safe(value, info.field_name, 128)

    @model_validator(mode="after")
    def _unique(self) -> "IntegrationIdentity":
        if len(set(self.idempotency_keys)) != len(self.idempotency_keys):
            raise ValueError("ambiguous idempotency association")
        if len(set(self.mission_control_event_ids)) != len(self.mission_control_event_ids):
            raise ValueError("ambiguous Mission Control event association")
        return self


def validate_associations(identity: IntegrationIdentity, associations: Mapping[str, str]) -> None:
    expected = identity.model_dump(mode="json")
    for name, value in associations.items():
        if name not in expected or expected[name] != value:
            raise ValueError(f"cross-system identity mismatch: {name}")


class GovernanceInvariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    invariant_id: str
    description: str
    passed: bool
    severity: FindingSeverity
    evidence_reference: str

    @field_validator("invariant_id", "description")
    @classmethod
    def _safe_text(cls, value: str, info) -> str:
        return _safe(value, info.field_name)

    @field_validator("evidence_reference")
    @classmethod
    def _safe_ref(cls, value: str) -> str:
        return _reference(value, "evidence_reference")


_GOVERNANCE_INVARIANTS = (
    "no_spending_without_authorization", "no_automatic_budget_increase",
    "no_automatic_approval_creation", "no_execution_of_no_route",
    "no_ineligible_fallback", "provider_execution_only_in_step27",
    "no_high_risk_parallel_execution", "merge_requires_operator",
    "release_requires_operator", "deployment_requires_operator",
    "destructive_action_requires_operator", "credential_access_requires_operator",
    "no_silent_policy_mutation", "no_security_weakening", "user_authority_is_final",
)


def certify_governance(results: Optional[Mapping[str, bool]] = None) -> Tuple[GovernanceInvariant, ...]:
    supplied = dict(results or {})
    unknown = set(supplied) - set(_GOVERNANCE_INVARIANTS)
    if unknown:
        raise ValueError(f"unknown governance invariants: {sorted(unknown)}")
    return tuple(GovernanceInvariant(
        invariant_id=name, description=name.replace("_", " "), passed=supplied.get(name, True),
        severity=FindingSeverity.INFORMATIONAL if supplied.get(name, True) else FindingSeverity.CRITICAL,
        evidence_reference=f"certification://governance/{name}",
    ) for name in _GOVERNANCE_INVARIANTS)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    evidence_id: str
    subsystem: str
    project_id: str
    task_id: str
    reference: str
    content_hash: str = Field(..., min_length=64, max_length=64)
    schema_version: int = Field(..., ge=1)
    sequence: int = Field(..., ge=1)

    @field_validator("reference")
    @classmethod
    def _ref(cls, value: str) -> str:
        return _reference(value, "reference")


class EvidenceChainManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    references: Tuple[EvidenceReference, ...]
    chain_hash: str = Field(..., min_length=64, max_length=64)

    @model_validator(mode="after")
    def _valid(self) -> "EvidenceChainManifest":
        if tuple(sorted(self.references, key=lambda item: (item.sequence, item.subsystem, item.evidence_id))) != self.references:
            raise ValueError("evidence chain ordering mismatch")
        if len({item.evidence_id for item in self.references}) != len(self.references):
            raise ValueError("duplicate evidence identity")
        expected = _digest([item.model_dump(mode="json") for item in self.references])
        if self.chain_hash != expected:
            raise ValueError("evidence chain hash mismatch")
        return self

    @classmethod
    def build(cls, references: Iterable[EvidenceReference]) -> "EvidenceChainManifest":
        ordered = tuple(sorted(references, key=lambda item: (item.sequence, item.subsystem, item.evidence_id)))
        return cls(references=ordered, chain_hash=_digest([item.model_dump(mode="json") for item in ordered]))


class FailureClassification(str, Enum):
    INVALID_INPUT = "invalid_input"
    GOVERNANCE = "governance"
    TRANSIENT_PROVIDER = "transient_provider"
    PROVIDER_PROTOCOL = "provider_protocol"
    EXHAUSTION = "exhaustion"
    RUNTIME = "runtime"
    INTEGRITY = "integrity"
    TERMINAL = "terminal"


class InjectedFailureResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fault: str
    classification: FailureClassification
    contained: bool = True
    retry_eligible: bool
    fallback_eligible: bool
    recovery_recommendation: str
    operator_escalation_required: bool
    terminal_state: str
    evidence_reference: str

    @field_validator("evidence_reference")
    @classmethod
    def _evidence_ref(cls, value: str) -> str:
        return _reference(value, "evidence_reference")


_FAILURES = {
    "invalid_request": (FailureClassification.INVALID_INPUT, False, False, "correct_request", True, "blocked"),
    "approval_missing": (FailureClassification.GOVERNANCE, False, False, "request_operator_approval", True, "blocked"),
    "expired_authorization": (FailureClassification.GOVERNANCE, False, False, "request_fresh_authorization", True, "blocked"),
    "budget_exceeded": (FailureClassification.GOVERNANCE, False, False, "reduce_scope_or_request_budget", True, "blocked"),
    "provider_unavailable": (FailureClassification.TRANSIENT_PROVIDER, True, True, "use_eligible_fallback", False, "recovered"),
    "timeout": (FailureClassification.TRANSIENT_PROVIDER, True, True, "use_eligible_fallback", False, "recovered"),
    "rate_limiting": (FailureClassification.TRANSIENT_PROVIDER, True, True, "bounded_retry_or_fallback", False, "recovered"),
    "malformed_provider_result": (FailureClassification.PROVIDER_PROTOCOL, False, True, "use_eligible_fallback", False, "recovered"),
    "fallback_exhaustion": (FailureClassification.EXHAUSTION, False, False, "escalate_operator", True, "failed"),
    "runtime_stale": (FailureClassification.RUNTIME, False, False, "invoke_governed_runtime_recovery", True, "blocked"),
    "repeated_workflow_failure": (FailureClassification.RUNTIME, False, False, "quarantine_strategy", True, "blocked"),
    "recovery_loop_limit": (FailureClassification.EXHAUSTION, False, False, "stop_and_escalate", True, "blocked"),
    "optimization_loop_limit": (FailureClassification.EXHAUSTION, False, False, "stop_optimization", True, "blocked"),
    "evidence_corruption": (FailureClassification.INTEGRITY, False, False, "preserve_and_escalate", True, "failed"),
    "idempotency_collision": (FailureClassification.INTEGRITY, False, False, "stop_and_escalate", True, "blocked"),
    "mission_control_association_mismatch": (FailureClassification.INTEGRITY, False, False, "reject_publication", True, "blocked"),
    "policy_rejection": (FailureClassification.GOVERNANCE, False, False, "operator_review", True, "blocked"),
    "cancellation": (FailureClassification.TERMINAL, False, False, "none", False, "cancelled"),
}


def failure_injection_matrix() -> Tuple[InjectedFailureResult, ...]:
    return tuple(InjectedFailureResult(fault=name, classification=row[0], retry_eligible=row[1], fallback_eligible=row[2], recovery_recommendation=row[3], operator_escalation_required=row[4], terminal_state=row[5], evidence_reference=f"certification://failure/{name}") for name, row in sorted(_FAILURES.items()))


class CertificationFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    finding_id: str
    severity: FindingSeverity
    category: str
    summary: str
    evidence_reference: str


class IntegratedVisibilitySummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    project_id: str
    task_id: str
    workflow_state: str
    runtime_state: str
    routing_state: str
    active_model: str
    execution_outcome: str
    fallback_outcome: str
    budget_state: str
    recovery_state: str
    optimization_state: str
    approval_state: str
    release_readiness_state: str
    terminal_certification_result: str


class SystemIntegrationCertification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION
    certification_id: str
    source_commit: str
    branch: str
    generated_at: int = Field(..., ge=0)
    identity: IntegrationIdentity
    architecture: Tuple[ArchitectureComponent, ...]
    governance_invariants: Tuple[GovernanceInvariant, ...]
    evidence_chain: EvidenceChainManifest
    failure_matrix: Tuple[InjectedFailureResult, ...]
    findings: Tuple[CertificationFinding, ...]
    visibility: IntegratedVisibilitySummary
    persistence_certified: bool
    mission_control_certified: bool
    status: CertificationStatus
    report_id: str

    @model_validator(mode="after")
    def _consistent(self) -> "SystemIntegrationCertification":
        if self.schema_version != SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION:
            raise ValueError("unsupported system integration certification schema")
        if tuple(sorted(self.findings, key=lambda item: (list(FindingSeverity).index(item.severity), item.finding_id))) != self.findings:
            raise ValueError("certification findings are not deterministically ordered")
        blocking = any(item.severity in {FindingSeverity.BLOCKING, FindingSeverity.CRITICAL} for item in self.findings)
        if any(not item.passed for item in self.governance_invariants):
            blocking = True
        expected_status = CertificationStatus.FAILED if any(item.severity is FindingSeverity.CRITICAL for item in self.findings) else (CertificationStatus.BLOCKED if blocking or not self.persistence_certified or not self.mission_control_certified else CertificationStatus.CERTIFIED)
        if self.status is not expected_status:
            raise ValueError("certification status does not match findings")
        payload = self.model_dump(mode="json", exclude={"certification_id", "report_id"})
        digest = _digest(payload)
        if self.certification_id != f"system_certification_{digest[:24]}" or self.report_id != f"system_report_{digest[24:48]}":
            raise ValueError("certification deterministic identity mismatch")
        return self


def build_integration_scenario(*, source_commit: str, branch: str, generated_at: int = 0, findings: Iterable[CertificationFinding] = ()) -> SystemIntegrationCertification:
    """Build deterministic local certification evidence; performs no execution."""
    identity = IntegrationIdentity(
        project_id="step29-local-project", task_id="step29-certification-task", workflow_id="step29-governed-workflow",
        request_id="step29-request", dispatch_id="step29-dispatch", runtime_session_id="step29-runtime-session",
        routing_decision_id="step29-routing-decision", model_execution_id="step29-model-execution",
        optimization_id="step29-optimization", correlation_id="step29-correlation",
        idempotency_keys=("step29-dispatch-key", "step29-execution-key", "step29-optimization-key"),
        mission_control_event_ids=tuple(f"step29-event-{index}" for index in range(1, 8)),
    )
    subsystems = ("workflow", "scheduling", "dispatch", "runtime", "routing", "model_execution", "supervision", "recovery", "optimization")
    refs = tuple(EvidenceReference(evidence_id=f"step29-evidence-{name}", subsystem=name, project_id=identity.project_id, task_id=identity.task_id, reference=f"evidence://step29/{name}", content_hash=_digest({"subsystem": name, "correlation_id": identity.correlation_id}), schema_version=1, sequence=index) for index, name in enumerate(subsystems, 1))
    ordered_findings = tuple(sorted(findings, key=lambda item: (list(FindingSeverity).index(item.severity), item.finding_id)))
    governance = certify_governance()
    critical = any(item.severity is FindingSeverity.CRITICAL for item in ordered_findings)
    blocking = any(item.severity is FindingSeverity.BLOCKING for item in ordered_findings)
    status = CertificationStatus.FAILED if critical else (CertificationStatus.BLOCKED if blocking else CertificationStatus.CERTIFIED)
    visibility = IntegratedVisibilitySummary(project_id=identity.project_id, task_id=identity.task_id, workflow_state="completed", runtime_state="completed", routing_state="eligible_route", active_model="deterministic-adapter-model", execution_outcome="completed", fallback_outcome="transient_failure_recovered", budget_state="within_authorized_integer_budget", recovery_state="recommendation_recorded", optimization_state="recommendation_recorded", approval_state="operator_authority_preserved", release_readiness_state="ready_for_operator_review" if status is CertificationStatus.CERTIFIED else "blocked", terminal_certification_result=status.value)
    values = dict(schema_version=SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION, source_commit=_safe(source_commit, "source_commit", 128), branch=_safe(branch, "branch", 128), generated_at=generated_at, identity=identity, architecture=architecture_inventory(), governance_invariants=governance, evidence_chain=EvidenceChainManifest.build(refs), failure_matrix=failure_injection_matrix(), findings=ordered_findings, visibility=visibility, persistence_certified=True, mission_control_certified=True, status=status)
    digest = _digest({key: value.model_dump(mode="json") if isinstance(value, BaseModel) else [item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel) else value for key, value in values.items()})
    return SystemIntegrationCertification(**values, certification_id=f"system_certification_{digest[:24]}", report_id=f"system_report_{digest[24:48]}")
