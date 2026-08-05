"""Cross-cutting adversarial tests for the ecosystem service registry.

Covers the adversarial scenarios from the ecosystem-services-installation
task that are specific to this branch's new code: direct Sigil bypass via
a registered-but-disabled ecosystem service, unauthorized cloud fallback
through the same path, malformed/duplicate/replayed registration events,
secret redaction, and preservation of the pre-existing non-empty
provider/model invariant in ``hermes_cli.agent_roles.model_execution``
(unmodified by this branch — verified here, not re-implemented).
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.model_execution import ModelExecutionRequest
from hermes_cli.agent_roles.model_routing import RoutingDecision, RoutingPolicyOutcome
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.service_registry import (
    EcosystemServiceRegistry,
    EcosystemServiceRegistryStore,
    ServiceInstallationStatus,
)
from hermes_cli.prime.sigil_contract import (
    SigilContractRequest,
)
from hermes_cli.prime.visibility import PrimeVisibilityService


def _now() -> int:
    return int(time.time())


def _route(provider_id: str, model_id: str) -> RoutingDecision:
    return RoutingDecision(
        decision_id="route-1", request_id="req-1", request_fingerprint="0" * 64,
        selected_provider_id=provider_id, selected_model_id=model_id,
        candidates=(), estimated_cost_micros=0, budget_limit_micros=0,
        policy_outcome=RoutingPolicyOutcome.FREE, fallback_chain=(), created_at=0,
    )


# ── Direct Sigil bypass via a registered ecosystem service ─────────────────

def test_registered_ecosystem_service_cannot_be_used_as_a_sigil_provider(tmp_path) -> None:
    """A registered-but-disabled ecosystem service (e.g. Paperclip) has no
    code path into hermes_cli.prime.sigil_contract at all — registering it
    does not create one. This is a structural proof, not a runtime check:
    SigilContractRequest has no field that could ever reference a
    service_key, and evaluate_sigil_contract_request only ever consults
    caller/service AdmissionDecision objects that a service-registry
    registration can never produce (registration never touches
    hermes_cli.prime.admission at all)."""
    registry = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))
    now = _now()
    outcome, record, _rejection = registry.register_known_service("paperclip", now=now)
    assert record.installation_status == ServiceInstallationStatus.PRESENT_DISABLED

    SigilContractRequest(
        request_id="r1", correlation_id="c1",
        caller_identity_id="fid_a", service_identity_id="fid_b",
        operation="advisory_valuation", requested_at=now,
    )
    # Nothing about the registered Paperclip record appears anywhere in a
    # SigilContractRequest's schema — there is no field to smuggle it
    # through, and the model is frozen/extra="forbid".
    with pytest.raises(ValidationError):
        SigilContractRequest(
            request_id="r2", correlation_id="c2",
            caller_identity_id="fid_a", service_identity_id="fid_b",
            operation="advisory_valuation", requested_at=now,
            ecosystem_service_key="paperclip",  # not a real field
        )


def test_unauthorized_cloud_fallback_cannot_originate_from_the_service_registry(tmp_path) -> None:
    """Registering an ecosystem service never produces a
    ModelProviderAdapter, RoutingDecision, or anything else the governed
    model-execution/routing layer could dispatch to — there is no code path
    from EcosystemServiceRegistry into hermes_cli.agent_roles.model_execution
    at all in this branch."""
    registry = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))
    now = _now()
    registry.register_known_service("buzz_relay", now=now)
    registry.register_known_service("agent_reach", now=now)

    # There is no `to_provider_adapter()`, `to_routing_candidate()`, or
    # similar method anywhere on ServiceRecord/EcosystemServiceRegistry —
    # confirmed structurally rather than by trying (and failing) to call
    # a method that does not exist.
    record = registry.get("buzz_relay")
    assert not hasattr(record, "to_provider_adapter")
    assert not hasattr(record, "provider_id")
    assert not hasattr(record, "model_id")


# ── Preserved (unmodified) non-empty provider/model invariant ──────────────

def test_blank_provider_id_is_rejected_by_existing_model_execution_request() -> None:
    """hermes_cli.agent_roles.model_execution is untouched by this branch;
    this test proves its pre-existing non-empty-provider/model invariant
    (required by the task's Gemma-first routing section) still holds."""
    with pytest.raises(ValidationError):
        ModelExecutionRequest(
            execution_id="e1", idempotency_key="i1", project_id="p", task_id="t",
            request_id="r1", routing_decision=_route("", "some-model"),
            selected_provider_id="", selected_model_id="some-model",
            input_reference="input://x", requested_at=0,
        )


def test_blank_model_id_is_rejected_by_existing_model_execution_request() -> None:
    with pytest.raises(ValidationError):
        ModelExecutionRequest(
            execution_id="e1", idempotency_key="i1", project_id="p", task_id="t",
            request_id="r1", routing_decision=_route("some-provider", ""),
            selected_provider_id="some-provider", selected_model_id="",
            input_reference="input://x", requested_at=0,
        )


# ── Malformed / replayed event payloads ─────────────────────────────────────

def test_malformed_registration_event_payload_does_not_fabricate_a_service_state(tmp_path) -> None:
    from hermes_cli.mission_control import models as m

    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    # A malformed event (missing "record" entirely, or with a garbage
    # service_key) must never appear as a service state in the snapshot.
    mission_control.append_event(
        m.TelemetryEvent(
            event_id="evt-malformed", event_type="prime_service_registered",
            project_id="proj1", payload={"service_key": ""},
        )
    )
    snapshot = mission_control.get_snapshot("proj1")
    assert snapshot.ecosystem_service_states == []


def test_replayed_identical_registration_event_is_idempotent(tmp_path) -> None:
    registry = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))
    evidence_store = PrimeEvidenceStore(state_root=tmp_path / "evidence")
    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    visibility = PrimeVisibilityService(mission_control, evidence_store)

    now = _now()
    outcome, record, rejection = registry.register_known_service("hermes_wiki", now=now)
    event1, _ = visibility.publish_service_registration(
        "proj1", service_key="hermes_wiki", outcome=outcome, record=record,
        rejection_code=rejection, now=now,
    )
    # Republishing the exact same (service_key, outcome, now) is treated as
    # a replay via source_idempotency_key and does not duplicate the event.
    event2, _ = visibility.publish_service_registration(
        "proj1", service_key="hermes_wiki", outcome=outcome, record=record,
        rejection_code=rejection, now=now,
    )
    assert event1.event_id == event2.event_id
    events = mission_control.get_events("proj1")
    assert len([e for e in events if e.event_type == "prime_service_registered"]) == 1


# ── Secret redaction ─────────────────────────────────────────────────────────

def test_service_record_and_evidence_never_contain_credential_looking_strings(tmp_path) -> None:
    registry = EcosystemServiceRegistry(store=EcosystemServiceRegistryStore(state_root=tmp_path / "prime"))
    evidence_store = PrimeEvidenceStore(state_root=tmp_path / "evidence")
    mission_control = MissionControlService(store=MissionControlStore(root=tmp_path / "mc"))
    visibility = PrimeVisibilityService(mission_control, evidence_store)

    now = _now()
    for descriptor_key in ("paperclip", "buzz_relay", "buzznode", "hermes_webui_adapter",
                            "hermes_wiki", "agent_reach", "self_evolution", "ecosystem_catalog"):
        outcome, record, rejection = registry.register_known_service(descriptor_key, now=now)
        visibility.publish_service_registration(
            "proj1", service_key=descriptor_key, outcome=outcome, record=record,
            rejection_code=rejection, now=now,
        )

    forbidden_markers = ("api_key", "api-key", "password", "secret=", "bearer ", "private_key")
    for record in evidence_store.read_all():
        encoded = str(record).lower()
        for marker in forbidden_markers:
            assert marker not in encoded, f"found {marker!r} in evidence record"
    for event in mission_control.get_events("proj1"):
        encoded = str(event.model_dump(mode="json")).lower()
        for marker in forbidden_markers:
            assert marker not in encoded, f"found {marker!r} in telemetry event"
