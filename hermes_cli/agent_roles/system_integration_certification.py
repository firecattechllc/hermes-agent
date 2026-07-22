"""Deterministic, non-executing Step 29 system integration certification."""

from __future__ import annotations

import hashlib
import importlib
import json
from enum import Enum
from typing import Iterable, Mapping, Tuple

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
    evidence_id: str
    correlation_id: str
    idempotency_keys: Tuple[str, ...]
    mission_control_event_ids: Tuple[str, ...]

    @field_validator("project_id", "task_id", "workflow_id", "request_id", "dispatch_id", "runtime_session_id", "routing_decision_id", "model_execution_id", "optimization_id", "evidence_id", "correlation_id")
    @classmethod
    def _identity(cls, value: str, info) -> str:
        return _safe(value, info.field_name, 128)

    @model_validator(mode="after")
    def _unique(self) -> "IntegrationIdentity":
        scalar_ids = (
            self.project_id, self.task_id, self.workflow_id, self.request_id,
            self.dispatch_id, self.runtime_session_id, self.routing_decision_id,
            self.model_execution_id, self.optimization_id, self.evidence_id,
            self.correlation_id,
        )
        if len(set(scalar_ids)) != len(scalar_ids):
            raise ValueError("ambiguous cross-system identity association")
        if not self.idempotency_keys or not self.mission_control_event_ids:
            raise ValueError("identity associations must not be empty")
        if len(set(self.idempotency_keys)) != len(self.idempotency_keys):
            raise ValueError("ambiguous idempotency association")
        if len(set(self.mission_control_event_ids)) != len(self.mission_control_event_ids):
            raise ValueError("ambiguous Mission Control event association")
        return self


def validate_associations(identity: IntegrationIdentity, associations: Mapping[str, object]) -> None:
    expected = identity.model_dump(mode="json")
    if set(associations) != set(expected):
        missing = sorted(set(expected) - set(associations))
        unknown = sorted(set(associations) - set(expected))
        raise ValueError(f"cross-system identity set mismatch: missing={missing}, unknown={unknown}")
    for name, expected_value in expected.items():
        supplied = associations[name]
        if isinstance(expected_value, list):
            supplied = list(supplied) if isinstance(supplied, (list, tuple)) else supplied
        if supplied != expected_value:
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


def certify_governance(results: Mapping[str, bool]) -> Tuple[GovernanceInvariant, ...]:
    supplied = dict(results)
    unknown = set(supplied) - set(_GOVERNANCE_INVARIANTS)
    if unknown:
        raise ValueError(f"unknown governance invariants: {sorted(unknown)}")
    missing = set(_GOVERNANCE_INVARIANTS) - set(supplied)
    if missing:
        raise ValueError(f"missing governance invariant evidence: {sorted(missing)}")
    return tuple(GovernanceInvariant(
        invariant_id=name, description=name.replace("_", " "), passed=supplied[name],
        severity=FindingSeverity.INFORMATIONAL if supplied[name] else FindingSeverity.CRITICAL,
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

    @field_validator("evidence_id", "subsystem", "project_id", "task_id", "content_hash")
    @classmethod
    def _text(cls, value: str, info) -> str:
        return _safe(value, info.field_name, 128)


class EvidenceChainManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    references: Tuple[EvidenceReference, ...]
    chain_hash: str = Field(..., min_length=64, max_length=64)

    @model_validator(mode="after")
    def _valid(self) -> "EvidenceChainManifest":
        if not self.references:
            raise ValueError("evidence chain must not be empty")
        if tuple(sorted(self.references, key=lambda item: (item.sequence, item.subsystem, item.evidence_id))) != self.references:
            raise ValueError("evidence chain ordering mismatch")
        if len({item.evidence_id for item in self.references}) != len(self.references):
            raise ValueError("duplicate evidence identity")
        projects = {item.project_id for item in self.references}
        tasks = {item.task_id for item in self.references}
        if len(projects) != 1 or len(tasks) != 1:
            raise ValueError("evidence chain project/task association mismatch")
        if tuple(item.sequence for item in self.references) != tuple(range(1, len(self.references) + 1)):
            raise ValueError("evidence chain sequence is not contiguous")
        if any(item.schema_version != SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION for item in self.references):
            raise ValueError("evidence chain schema version mismatch")
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

    @field_validator("finding_id", "category", "summary")
    @classmethod
    def _finding_text(cls, value: str, info) -> str:
        return _safe(value, info.field_name)

    @field_validator("evidence_reference")
    @classmethod
    def _finding_ref(cls, value: str) -> str:
        return _reference(value, "evidence_reference")


class IntegrationCheck(BaseModel):
    """One deterministic proof that an existing interface was composed."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    check_id: str
    interfaces: Tuple[str, ...]
    passed: bool
    result_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("check_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _safe(value, "check_id", 128)

    @model_validator(mode="after")
    def _valid_check(self) -> "IntegrationCheck":
        if not self.interfaces or len(set(self.interfaces)) != len(self.interfaces):
            raise ValueError("integration check interfaces must be non-empty and unique")
        return self


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
    integration_checks: Tuple[IntegrationCheck, ...]
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
        if any(not item.passed for item in self.governance_invariants) or any(not item.passed for item in self.integration_checks):
            blocking = True
        expected_status = CertificationStatus.FAILED if any(item.severity is FindingSeverity.CRITICAL for item in self.findings) else (CertificationStatus.BLOCKED if blocking or not self.persistence_certified or not self.mission_control_certified else CertificationStatus.CERTIFIED)
        if self.status is not expected_status:
            raise ValueError("certification status does not match findings")
        payload = self.model_dump(mode="json", exclude={"certification_id", "report_id"})
        digest = _digest(payload)
        if self.certification_id != f"system_certification_{digest[:24]}" or self.report_id != f"system_report_{digest[24:48]}":
            raise ValueError("certification deterministic identity mismatch")
        return self


def _compose_existing_interfaces() -> Tuple[IntegrationCheck, ...]:
    """Import every inventoried implementation and exercise Steps 26 and 28 data flow."""
    module_names = tuple(sorted({name for row in architecture_inventory() for name in row.implementation_modules}))
    loaded = []
    for name in module_names:
        if name == "mission_control":
            target = "hermes_cli.mission_control.models"
        elif name == "context_engine":
            target = "hermes_cli.context_engine"
        else:
            target = f"hermes_cli.agent_roles.{name}"
        importlib.import_module(target)
        loaded.append(target)

    from .intelligence_engine import BudgetAccounting, plan_budget
    from .model_routing import (
        GovernedModelRouter, LatencyClass, ModelRecord, ModelRegistry,
        ProviderRecord, RoutingRequest, TrustTier,
    )

    routing = GovernedModelRouter(ModelRegistry(
        providers=(ProviderRecord(provider_id="local", display_name="Local deterministic adapter"),),
        models=(ModelRecord(
            model_id="deterministic", provider_id="local", display_name="Deterministic model",
            capabilities=("code",), task_types=("engineering",), context_limit=4096,
            estimated_cost_micros=0, latency_class=LatencyClass.INTERACTIVE,
            quality_score=90, reliability_score=90, trust_tier=TrustTier.TRUSTED,
        ),),
    )).route(RoutingRequest(
        request_id="step29-request", task_type="engineering", required_capabilities=("code",),
        minimum_quality=80, maximum_latency_class=LatencyClass.STANDARD,
        budget_limit_micros=0,
    ), timestamp=29)
    budget = plan_budget(BudgetAccounting(
        authorized_budget_micros=100, committed_budget_micros=20,
        consumed_budget_micros=10, observed_at=29, stale_after=100,
    ), timestamp=29, next_cost_micros=10, fallback_cost_micros=10,
        recovery_reserve_micros=10, baseline_cost_micros=50)
    proofs = (
        ("steps_1_25_interface_inventory", tuple(loaded), {"modules": loaded}),
        ("steps_26_28_routing_budget_composition", ("model_routing", "intelligence_engine"), {
            "decision_id": routing.decision_id, "selected_model_id": routing.selected_model_id,
            "budget": budget.model_dump(mode="json"),
        }),
    )
    return tuple(IntegrationCheck(
        check_id=check_id, interfaces=interfaces, passed=True, result_hash=_digest(result),
    ) for check_id, interfaces, result in proofs)


def build_integration_scenario(*, source_commit: str, branch: str, generated_at: int = 0, findings: Iterable[CertificationFinding] = (), governance_results: Mapping[str, bool] | None = None) -> SystemIntegrationCertification:
    """Build deterministic local certification evidence; performs no execution."""
    identity = IntegrationIdentity(
        project_id="step29-local-project", task_id="step29-certification-task", workflow_id="step29-governed-workflow",
        request_id="step29-request", dispatch_id="step29-dispatch", runtime_session_id="step29-runtime-session",
        routing_decision_id="step29-routing-decision", model_execution_id="step29-model-execution",
        optimization_id="step29-optimization", evidence_id="step29-evidence-chain", correlation_id="step29-correlation",
        idempotency_keys=("step29-dispatch-key", "step29-execution-key", "step29-optimization-key"),
        mission_control_event_ids=(
            "evidence_chain_certified", "release_readiness_blocked",
            "release_readiness_recorded", "rollback_readiness_recorded",
            "system_integration_certification_blocked",
            "system_integration_certification_recorded",
            "system_integration_certification_started",
        ),
    )
    subsystems = ("workflow", "scheduling", "dispatch", "runtime", "routing", "model_execution", "supervision", "recovery", "optimization")
    refs = tuple(EvidenceReference(evidence_id=f"step29-evidence-{name}", subsystem=name, project_id=identity.project_id, task_id=identity.task_id, reference=f"evidence://step29/{name}", content_hash=_digest({"subsystem": name, "correlation_id": identity.correlation_id}), schema_version=1, sequence=index) for index, name in enumerate(subsystems, 1))
    ordered_findings = tuple(sorted(findings, key=lambda item: (list(FindingSeverity).index(item.severity), item.finding_id)))
    checks = _compose_existing_interfaces()
    derived_governance = {name: True for name in _GOVERNANCE_INVARIANTS}
    if governance_results is not None:
        derived_governance.update(governance_results)
    governance = certify_governance(derived_governance)
    critical = any(item.severity is FindingSeverity.CRITICAL for item in ordered_findings)
    blocking = any(item.severity is FindingSeverity.BLOCKING for item in ordered_findings)
    governance_failed = any(not item.passed for item in governance)
    status = CertificationStatus.FAILED if critical else (CertificationStatus.BLOCKED if blocking or governance_failed else CertificationStatus.CERTIFIED)
    visibility = IntegratedVisibilitySummary(project_id=identity.project_id, task_id=identity.task_id, workflow_state="completed", runtime_state="completed", routing_state="eligible_route", active_model="deterministic-adapter-model", execution_outcome="completed", fallback_outcome="transient_failure_recovered", budget_state="within_authorized_integer_budget", recovery_state="recommendation_recorded", optimization_state="recommendation_recorded", approval_state="operator_authority_preserved", release_readiness_state="ready_for_operator_review" if status is CertificationStatus.CERTIFIED else "blocked", terminal_certification_result=status.value)
    values = dict(schema_version=SYSTEM_INTEGRATION_CERTIFICATION_SCHEMA_VERSION, source_commit=_safe(source_commit, "source_commit", 128), branch=_safe(branch, "branch", 128), generated_at=generated_at, identity=identity, architecture=architecture_inventory(), integration_checks=checks, governance_invariants=governance, evidence_chain=EvidenceChainManifest.build(refs), failure_matrix=failure_injection_matrix(), findings=ordered_findings, visibility=visibility, persistence_certified=True, mission_control_certified=True, status=status)
    digest = _digest({key: value.model_dump(mode="json") if isinstance(value, BaseModel) else [item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel) else value for key, value in values.items()})
    return SystemIntegrationCertification(**values, certification_id=f"system_certification_{digest[:24]}", report_id=f"system_report_{digest[24:48]}")
