from __future__ import annotations

import time
from pathlib import Path

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import AdmissionOutcome, CertificationStatus
from hermes_cli.prime.dispatch_gate import CertificationSnapshot, PrimeGovernedProviderAdapter
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.ollama_node import OllamaGenerateOutcome
from hermes_cli.prime.sigil_contract import SigilContractOutcome, SigilContractRequest, SigilRejectionCode
from hermes_cli.prime.sigil_routing import SigilRoutingService


def _now() -> int:
    return int(time.time())


class FakeUnderlying:
    def __init__(self, outcome: OllamaGenerateOutcome) -> None:
        self._outcome = outcome
        self.calls = []

    def generate(self, *, alias, input_text, timeout_seconds):
        self.calls.append((alias, input_text, timeout_seconds))
        return self._outcome


def _runtime(tmp_path: Path) -> FleetRuntime:
    return FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="sigil-test",
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )


def _register_and_heartbeat(runtime: FleetRuntime, natural_key: str, role: FleetNodeRole, *, now: int):
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id=f"req-{natural_key}",
            natural_key=natural_key,
            role=role,
            declared_capabilities=("worker_heartbeat", "local_model_inference", "sigil_paper_advisory"),
            endpoint=f"http://{natural_key}.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key=natural_key,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
            submitted_at=now,
        ),
        now=now,
    )


def _adapter(runtime, natural_key, provider_id, *, outcome):
    return PrimeGovernedProviderAdapter(
        provider_id=provider_id,
        natural_key=natural_key,
        fleet_runtime=runtime,
        underlying=FakeUnderlying(outcome),
        certification_provider=lambda: CertificationSnapshot(
            status=CertificationStatus.CERTIFIED, evidence_ref="evidence://cert"
        ),
        input_resolver=lambda ref: "sigil advisory input",
        clock=lambda: _now(),
    )


def _sigil_request(operation: str, *, caller_id: str, service_id: str, now: int, **overrides):
    fields = dict(
        request_id=f"sigil-req-{operation}",
        correlation_id="corr-1",
        caller_identity_id=caller_id,
        service_identity_id=service_id,
        operation=operation,
        requested_at=now,
        input_payload={"symbol": "TEST"},
    )
    fields.update(overrides)
    return SigilContractRequest(**fields)


def test_advisory_valuation_routes_to_mac_and_succeeds(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)

    mac_node = runtime.get_node("mac")
    titan_node = runtime.get_node("titan")
    mac_adapter = _adapter(
        runtime, "mac", "mac-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="valuation complete"),
    )

    caller_decision = runtime.evaluate_admission(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    service_decision = runtime.evaluate_admission(
        "mac", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    assert caller_decision.outcome == AdmissionOutcome.ADMITTED
    assert service_decision.outcome == AdmissionOutcome.ADMITTED

    service = SigilRoutingService(adapters={"mac": mac_adapter})
    request = _sigil_request(
        "advisory_valuation", caller_id=titan_node.identity_id, service_id=mac_node.identity_id, now=now
    )
    response = service.route(
        request,
        caller_admission=caller_decision,
        service_admission=service_decision,
        caller_health=runtime.latest_health("titan"),
        service_health=runtime.latest_health("mac"),
        now=now,
    )
    assert response.outcome == SigilContractOutcome.ACCEPTED
    assert response.execution_authority_granted is False
    assert response.broker_submission_granted is False
    assert response.evidence_refs


def test_financial_sentiment_routes_to_titan(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    mac_node = runtime.get_node("mac")
    titan_node = runtime.get_node("titan")

    titan_underlying_outcome = OllamaGenerateOutcome(succeeded=True, output_text="positive sentiment")
    titan_adapter = _adapter(runtime, "titan", "titan-ollama", outcome=titan_underlying_outcome)

    caller_decision = runtime.evaluate_admission(
        "mac", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    service_decision = runtime.evaluate_admission(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )

    service = SigilRoutingService(adapters={"titan": titan_adapter})
    request = _sigil_request(
        "advisory_financial_sentiment", caller_id=mac_node.identity_id, service_id=titan_node.identity_id, now=now
    )
    response = service.route(
        request,
        caller_admission=caller_decision,
        service_admission=service_decision,
        caller_health=runtime.latest_health("mac"),
        service_health=runtime.latest_health("titan"),
        now=now,
    )
    assert response.outcome == SigilContractOutcome.ACCEPTED
    assert response.advisory_output["routed_to"] == "titan"


def test_certification_status_query_never_dispatches_to_a_node(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    mac_node = runtime.get_node("mac")
    titan_node = runtime.get_node("titan")

    caller_decision = runtime.evaluate_admission(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    service_decision = runtime.evaluate_admission(
        "mac", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )

    service = SigilRoutingService(adapters={})  # no adapters registered at all
    request = _sigil_request(
        "certification_status_query", caller_id=titan_node.identity_id, service_id=mac_node.identity_id, now=now
    )
    response = service.route(
        request, caller_admission=caller_decision, service_admission=service_decision,
        caller_health=runtime.latest_health("titan"), service_health=runtime.latest_health("mac"), now=now,
    )
    assert response.outcome == SigilContractOutcome.ACCEPTED


def test_unadmitted_caller_is_rejected_before_any_dispatch(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    mac_node = runtime.get_node("mac")
    mac_adapter = _adapter(
        runtime, "mac", "mac-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"),
    )
    service = SigilRoutingService(adapters={"mac": mac_adapter})

    # Titan was never registered — caller_admission for it is DENIED.
    caller_decision = runtime.evaluate_admission(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    service_decision = runtime.evaluate_admission(
        "mac", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    request = _sigil_request(
        "advisory_valuation", caller_id="fid_node_ghost_caller", service_id=mac_node.identity_id, now=now
    )
    response = service.route(
        request, caller_admission=caller_decision, service_admission=service_decision,
        caller_health=None, service_health=runtime.latest_health("mac"), now=now,
    )
    assert response.outcome == SigilContractOutcome.REJECTED
    assert response.rejection_code == SigilRejectionCode.CALLER_NOT_ADMITTED


def test_dispatch_gate_still_refuses_a_revoked_target_even_with_a_forged_admission_decision(tmp_path) -> None:
    """Defense in depth: PrimeGovernedProviderAdapter re-checks live fleet state
    independently of whatever AdmissionDecision the caller supplied to the
    contract-level gate, so revoking a node after the decision was minted
    still blocks dispatch."""
    runtime = _runtime(tmp_path)
    now = _now()
    _register_and_heartbeat(runtime, "mac", FleetNodeRole.MAC, now=now)
    _register_and_heartbeat(runtime, "titan", FleetNodeRole.TITAN, now=now)
    mac_node = runtime.get_node("mac")
    titan_node = runtime.get_node("titan")

    caller_decision = runtime.evaluate_admission(
        "titan", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    service_decision = runtime.evaluate_admission(
        "mac", now=now, certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    )
    assert service_decision.outcome == AdmissionOutcome.ADMITTED

    # Revoke mac *after* minting its ADMITTED decision but *before* dispatch.
    runtime.revoke_node("mac", now=now, reason="compromised")

    mac_adapter = _adapter(
        runtime, "mac", "mac-ollama",
        outcome=OllamaGenerateOutcome(succeeded=True, output_text="should never happen"),
    )
    service = SigilRoutingService(adapters={"mac": mac_adapter})
    request = _sigil_request(
        "advisory_valuation", caller_id=titan_node.identity_id, service_id=mac_node.identity_id, now=now
    )
    response = service.route(
        request, caller_admission=caller_decision, service_admission=service_decision,
        caller_health=runtime.latest_health("titan"), service_health=runtime.latest_health("mac"), now=now,
    )
    assert response.outcome == SigilContractOutcome.REJECTED
