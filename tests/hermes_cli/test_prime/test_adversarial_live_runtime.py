"""Cross-component adversarial + end-to-end acceptance tests for the Fleet
Unification live runtime.

Each test below targets exactly one adversarial scenario from the live
runtime's threat model, exercised through the real, wired-together stack
(FleetRuntime + dispatch_gate + desktop_governance + sigil_routing +
operator_approval + evidence) rather than a single module in isolation.
Some of these properties are also covered by the focused unit tests in the
other files in this directory; this file exists as the single, explicitly
labeled place an adversarial review can check every item off.

Evidence tampering and remote-maintenance privilege escalation are
exercised by the pre-existing Stage 2 suites
(``test_evidence.py::test_store_detects_tampered_entry`` and
``test_remote_maintenance_governance.py``'s default-deny/expired/revoked/
missing-scope tests) and are not duplicated here — those already prove the
properties adversarially, at the module a live-runtime failure would
actually surface in.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.model_execution import (
    GovernedModelExecutionService,
    InMemoryModelExecutionStore,
    ModelExecutionErrorClass,
    ModelExecutionRequest,
    ModelExecutionState,
)
from hermes_cli.agent_roles.model_routing import (
    CandidateDisposition,
    CandidateScore,
    RoutingDecision,
    RoutingPolicyOutcome,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import AdmissionOutcome, CertificationStatus
from hermes_cli.prime.desktop_governance import (
    DesktopUseOutcome,
    DesktopUsePolicy,
    DesktopUseRejectionCode,
    DesktopUseRequest,
    evaluate_desktop_use_request,
)
from hermes_cli.prime.dispatch_gate import CertificationSnapshot, PrimeGovernedProviderAdapter
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import (
    FleetNodeRegistrationRequest,
    FleetNodeRole,
    FleetRegistrationOutcome,
    FleetRegistrationRejectionCode,
)
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import DEFAULT_MAX_REPORT_AGE_SECONDS, LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatOutcome, HeartbeatSubmission
from hermes_cli.prime.ollama_node import OllamaGenerateOutcome, OllamaNodeConfig, OllamaNodeConfigurationError
from hermes_cli.prime.operator_approval import (
    ApprovalChannel,
    ApprovalRejectionCode,
    OperatorApproval,
    OperatorApprovalReplayStore,
    OperatorApprovalScope,
    validate_operator_approval,
)
from hermes_cli.prime.sigil_contract import SigilContractOutcome, SigilContractRequest, SigilRejectionCode
from hermes_cli.prime.sigil_routing import SigilRoutingService


def _now() -> int:
    return int(time.time())


def _runtime(tmp_path: Path, project_id: str = "adversarial") -> FleetRuntime:
    return FleetRuntime(
        state_root=tmp_path / "prime",
        project_id=project_id,
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )


def _register_and_heartbeat(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int) -> None:
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id=f"req-{natural_key}", natural_key=natural_key, role=role,
            declared_capabilities=("worker_heartbeat", "local_model_inference", "desktop_use"),
            endpoint=f"http://{natural_key}.tailnet.internal:11434",
            software_version="1.0.0", protocol_version=1, requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key=natural_key, liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )


class FakeUnderlying:
    def __init__(self, outcome: OllamaGenerateOutcome) -> None:
        self._outcome = outcome
        self.calls = []

    def generate(self, *, alias, input_text, timeout_seconds):
        self.calls.append((alias, input_text, timeout_seconds))
        return self._outcome


def _adapter(runtime, natural_key, provider_id, *, outcome):
    return PrimeGovernedProviderAdapter(
        provider_id=provider_id, natural_key=natural_key, fleet_runtime=runtime,
        underlying=FakeUnderlying(outcome),
        certification_provider=lambda: CertificationSnapshot(
            status=CertificationStatus.CERTIFIED, evidence_ref="evidence://cert"
        ),
        input_resolver=lambda ref: "adversarial test input",
        clock=lambda: _now(),
    )


def _route(provider_id: str, model_id: str, *, now: int) -> RoutingDecision:
    candidate = CandidateScore(
        provider_id=provider_id, model_id=model_id, disposition=CandidateDisposition.ELIGIBLE,
        estimated_cost_micros=0, score=100, quality_factor=100, reliability_factor=100,
        latency_factor=100, cost_factor=100, preference_factor=100, trust_factor=100,
    )
    return RoutingDecision(
        decision_id="adversarial_route", request_id="adversarial-req", request_fingerprint="0" * 64,
        selected_provider_id=provider_id, selected_model_id=model_id, candidates=(candidate,),
        estimated_cost_micros=0, budget_limit_micros=0, policy_outcome=RoutingPolicyOutcome.FREE,
        fallback_chain=(), created_at=now,
    )


# ── 1. Spoofed node identity ─────────────────────────────────────────────────

def test_spoofed_node_identity_is_rejected(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    decision = runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="spoof-1", natural_key="titan", role=FleetNodeRole.TITAN,
            endpoint="http://titan.tailnet.internal:11434", software_version="1.0.0",
            protocol_version=1, requested_at=now,
            claimed_identity_id="fid_node_totally_forged_identity_value",
        ),
        now=now,
    )
    assert decision.outcome == FleetRegistrationOutcome.REJECTED
    assert decision.rejection_code == FleetRegistrationRejectionCode.IDENTITY_MISMATCH
    assert runtime.get_node("titan") is None


# ── 2. Stale heartbeat ───────────────────────────────────────────────────────

def test_stale_heartbeat_removes_dispatch_eligibility_end_to_end(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    adapter = _adapter(runtime, "titan", "titan-ollama", outcome=OllamaGenerateOutcome(succeeded=True, output_text="ok"))
    adapter._clock = lambda: now + DEFAULT_MAX_REPORT_AGE_SECONDS + 120

    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    request = ModelExecutionRequest(
        execution_id="e1", idempotency_key="i1", project_id="p", task_id="t", request_id="adversarial-req",
        routing_decision=_route("titan-ollama", "lightweight", now=now), selected_provider_id="titan-ollama",
        selected_model_id="lightweight", input_reference="input://x",
        requested_at=now + DEFAULT_MAX_REPORT_AGE_SECONDS + 120,
    )
    evidence = service.execute(request, timestamp=now + DEFAULT_MAX_REPORT_AGE_SECONDS + 120)
    assert evidence.state == ModelExecutionState.FAILED
    assert evidence.error_classification == ModelExecutionErrorClass.AUTHORIZATION_INVALID


# ── 3. Duplicate registration ────────────────────────────────────────────────

def test_duplicate_registration_end_to_end(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    second = runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="dup-1", natural_key="mac", role=FleetNodeRole.MAC,
            endpoint="http://mac2.tailnet.internal:11434", software_version="1.0.0",
            protocol_version=1, requested_at=now,
        ),
        now=now,
    )
    assert second.outcome == FleetRegistrationOutcome.REJECTED
    assert second.rejection_code == FleetRegistrationRejectionCode.DUPLICATE_REGISTRATION


# ── 4. Revoked node ───────────────────────────────────────────────────────────

def test_revoked_node_cannot_dispatch_heartbeat_or_reregister(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    runtime.revoke_node("titan", now=now, reason="adversarial-test")

    assert runtime.is_dispatchable(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    ) is False
    hb = runtime.ingest_heartbeat(
        HeartbeatSubmission(natural_key="titan", liveness=LivenessState.ALIVE, readiness=ReadinessState.READY, submitted_at=now + 1),
        now=now + 1,
    )
    assert hb.outcome == HeartbeatOutcome.REJECTED
    reregistration = runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="revoked-rereg", natural_key="titan", role=FleetNodeRole.TITAN,
            endpoint="http://titan.tailnet.internal:11434", software_version="1.0.0",
            protocol_version=1, requested_at=now,
        ),
        now=now, allow_reregistration=True,
    )
    assert reregistration.outcome == FleetRegistrationOutcome.REJECTED
    assert reregistration.rejection_code == FleetRegistrationRejectionCode.NODE_REVOKED


# ── 5. Missing model / 6. Empty model name ──────────────────────────────────

def test_missing_model_configuration_is_rejected_before_network(tmp_path) -> None:
    config = OllamaNodeConfig(natural_key="titan", endpoint="http://titan.tailnet.internal:11434", model_aliases={})
    with pytest.raises(OllamaNodeConfigurationError):
        config.resolve_model("primary_reasoning")


def test_empty_model_name_never_reaches_dispatch(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    adapter = _adapter(runtime, "titan", "titan-ollama", outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"))
    result = adapter.execute(model_id="", input_reference="input://x", timeout_seconds=10)
    assert result.error_classification == ModelExecutionErrorClass.INVALID_REQUEST


# ── 7. Unavailable Ollama endpoint ──────────────────────────────────────────

def test_unavailable_ollama_endpoint_is_retryable_not_a_silent_success(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    adapter = _adapter(
        runtime, "titan", "titan-ollama",
        outcome=OllamaGenerateOutcome(succeeded=False, error="connection refused", retryable=True),
    )
    result = adapter.execute(model_id="lightweight", input_reference="input://x", timeout_seconds=10)
    assert result.error_classification == ModelExecutionErrorClass.PROVIDER_UNAVAILABLE
    assert result.error_classification.retryable is True


# ── 8. Unauthorized provider fallback ───────────────────────────────────────

def test_unauthorized_provider_never_reached_via_fallback(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    # "mac" is never registered/heartbeated — it is not an authorized adapter,
    # and is deliberately absent from the adapters tuple below too.
    adapter = _adapter(runtime, "titan", "titan-ollama", outcome=OllamaGenerateOutcome(succeeded=False, error="down", retryable=True))
    service = GovernedModelExecutionService((adapter,), InMemoryModelExecutionStore())
    route = _route("titan-ollama", "lightweight", now=now)
    request = ModelExecutionRequest(
        execution_id="e1", idempotency_key="i1", project_id="p", task_id="t", request_id="adversarial-req",
        routing_decision=route, selected_provider_id="titan-ollama", selected_model_id="lightweight",
        input_reference="input://x", requested_at=now, maximum_attempts=3,
    )
    evidence = service.execute(request, timestamp=now)
    assert evidence.state != ModelExecutionState.SUCCEEDED
    assert "mac" not in evidence.attempted_models[0] if evidence.attempted_models else True


# ── 9. Approval replay ──────────────────────────────────────────────────────

def test_approval_replay_across_desktop_use_evaluations(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    policy = DesktopUsePolicy(allowed_apps=("Finder",))
    node = runtime.get_node("mac")
    request = DesktopUseRequest(request_id="r1", action="click", app="Finder", requested_at=now)
    approval = OperatorApproval.grant(
        scope=OperatorApprovalScope.DESKTOP_USE, action_id=request.action_id,
        subject_identity_id=node.identity_id, operator_identity="attacker-replaying-a-captured-approval",
        channel=ApprovalChannel.TELEGRAM, granted_at=now, evidence_ref="evidence://x",
    )
    gated = request.model_copy(update={"operator_approval": approval})

    first = evaluate_desktop_use_request(
        gated, policy, fleet_runtime=runtime, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now,
    )
    assert first.outcome == DesktopUseOutcome.ADMITTED

    replayed = evaluate_desktop_use_request(
        gated, policy, fleet_runtime=runtime, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now + 1,
    )
    assert replayed.outcome == DesktopUseOutcome.DENIED
    assert replayed.rejection_code == DesktopUseRejectionCode.APPROVAL_INVALID
    assert replayed.approval_detail == "replayed"


# ── 10. Expired approval ────────────────────────────────────────────────────

def test_expired_approval_is_rejected(tmp_path) -> None:
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    now = _now()
    approval = OperatorApproval.grant(
        scope=OperatorApprovalScope.REMOTE_MAINTENANCE, action_id="actn_x",
        subject_identity_id="fid_node_titan", operator_identity="op:1",
        channel=ApprovalChannel.PHONE, granted_at=now, evidence_ref="evidence://x", max_age_seconds=60,
    )
    ok, code = validate_operator_approval(
        approval, expected_scope=OperatorApprovalScope.REMOTE_MAINTENANCE, expected_action_id="actn_x",
        expected_subject_identity_id="fid_node_titan", now=now + 3600, replay_store=replay_store,
    )
    assert ok is False
    assert code == ApprovalRejectionCode.EXPIRED


# ── 11. Desktop-use scope escape ────────────────────────────────────────────

def test_desktop_use_cannot_escape_its_approved_app_scope(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    policy = DesktopUsePolicy(allowed_apps=("Finder",))  # System Settings is NOT in scope
    node = runtime.get_node("mac")

    escalation_request = DesktopUseRequest(
        request_id="r1", action="click", app="System Settings", requested_at=now
    )
    approval = OperatorApproval.grant(
        scope=OperatorApprovalScope.DESKTOP_USE, action_id=escalation_request.action_id,
        subject_identity_id=node.identity_id, operator_identity="op:1",
        channel=ApprovalChannel.CLI, granted_at=now, evidence_ref="evidence://x",
    )
    decision = evaluate_desktop_use_request(
        escalation_request.model_copy(update={"operator_approval": approval}),
        policy, fleet_runtime=runtime, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.APP_NOT_IN_WORKSPACE_SCOPE


def test_desktop_use_action_cannot_be_widened_beyond_its_approval(tmp_path) -> None:
    """An approval granted for `click` cannot be reused to authorize `type`
    (or any other action) against the same app — scope is bound to the
    exact action, not just the node/app pair."""
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    replay_store = OperatorApprovalReplayStore(state_root=tmp_path / "prime")
    policy = DesktopUsePolicy(allowed_apps=("Finder",))
    node = runtime.get_node("mac")

    click_request = DesktopUseRequest(request_id="r1", action="click", app="Finder", requested_at=now)
    approval = OperatorApproval.grant(
        scope=OperatorApprovalScope.DESKTOP_USE, action_id=click_request.action_id,
        subject_identity_id=node.identity_id, operator_identity="op:1",
        channel=ApprovalChannel.CLI, granted_at=now, evidence_ref="evidence://x",
    )
    widened_request = DesktopUseRequest(request_id="r2", action="type", app="Finder", requested_at=now)
    decision = evaluate_desktop_use_request(
        widened_request.model_copy(update={"operator_approval": approval}),
        policy, fleet_runtime=runtime, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert", replay_store=replay_store, now=now,
    )
    assert decision.outcome == DesktopUseOutcome.DENIED
    assert decision.rejection_code == DesktopUseRejectionCode.APPROVAL_INVALID
    assert decision.approval_detail == "action_mismatch"


# ── 12. Direct Sigil bypass ─────────────────────────────────────────────────

def test_sigil_cannot_bypass_prime_to_reach_an_unadmitted_provider(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    mac_node = runtime.get_node("mac")

    # No caller/service admission was ever evaluated — this simulates Sigil
    # attempting to invoke the contract without going through Prime at all.
    service = SigilRoutingService(adapters={
        "mac": _adapter(runtime, "mac", "mac-ollama", outcome=OllamaGenerateOutcome(succeeded=True, output_text="bypass"))
    })
    request = SigilContractRequest(
        request_id="bypass-1", correlation_id="c1", caller_identity_id="fid_node_unregistered_caller",
        service_identity_id=mac_node.identity_id, operation="advisory_valuation", requested_at=now,
    )
    response = service.route(
        request, caller_admission=None, service_admission=None, caller_health=None, service_health=None, now=now,
    )
    assert response.outcome == SigilContractOutcome.REJECTED
    assert response.rejection_code == SigilRejectionCode.CALLER_NOT_ADMITTED
    assert response.execution_authority_granted is False
    assert response.broker_submission_granted is False


def test_sigil_contract_cannot_be_constructed_with_unsafe_invariants() -> None:
    """Structural bypass attempt: try to construct a request that claims
    non-paper-only / broker-submission / execution authority. Must fail at
    construction, not merely be denied later."""
    now = _now()
    for unsafe_kwargs in (
        {"paper_only": False},
        {"broker_submission_denied": False},
        {"execution_authority_denied": False},
        {"advisory": False},
        {"production_mutation_denied": False},
    ):
        with pytest.raises(ValidationError):
            SigilContractRequest(
                request_id="r", correlation_id="c", caller_identity_id="fid_a",
                service_identity_id="fid_b", operation="advisory_valuation", requested_at=now,
                **unsafe_kwargs,
            )
