import json

import pytest

from hermes_cli.agent_roles import (
    SYSTEM_INTEGRATION_EVENT_TYPES,
    CertificationFinding,
    FailureClassification,
    FindingSeverity,
    IntegrationIdentity,
    ReleaseDisposition,
    ReleaseReadinessStore,
    SystemIntegrationCertificationStore,
    SystemIntegrationCertificationVisibilityAdapter,
    SystemIntegrationCertificationVisibilityService,
    architecture_inventory,
    build_integration_scenario,
    build_release_readiness,
    certify_governance,
    failure_injection_matrix,
    validate_associations,
)
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def certification(findings=()):
    return build_integration_scenario(source_commit="14272ebb1", branch="step29-system-integration-certification", generated_at=29, findings=findings)


def finding(severity):
    return CertificationFinding(finding_id=f"finding-{severity.value}", severity=severity, category="integration", summary=f"{severity.value} finding", evidence_reference="evidence://step29/finding")


def test_architecture_inventory_and_end_to_end_scenario_are_deterministic():
    inventory = architecture_inventory()
    assert inventory == architecture_inventory()
    assert {item.step_range for item in inventory} >= {"1-4", "24", "25", "26", "27", "28"}
    first = certification(); second = certification()
    assert first == second
    assert first.status.value == "certified"
    assert first.visibility.fallback_outcome == "transient_failure_recovered"
    assert tuple(item.sequence for item in first.evidence_chain.references) == tuple(range(1, 10))
    assert all("://" in item.reference for item in first.evidence_chain.references)


def test_identity_associations_fail_closed():
    identity = certification().identity
    validate_associations(identity, {"project_id": identity.project_id, "correlation_id": identity.correlation_id})
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_associations(identity, {"project_id": "wrong"})
    with pytest.raises(ValueError, match="ambiguous idempotency"):
        IntegrationIdentity(**{**identity.model_dump(), "idempotency_keys": ("same", "same")})


def test_governance_invariants_are_machine_readable_and_failures_are_critical():
    invariants = certify_governance()
    assert len(invariants) == 15 and all(item.passed for item in invariants)
    failed = certify_governance({"no_spending_without_authorization": False})
    assert next(item for item in failed if not item.passed).severity is FindingSeverity.CRITICAL
    names = {item.invariant_id for item in invariants}
    assert {"merge_requires_operator", "release_requires_operator", "deployment_requires_operator", "credential_access_requires_operator", "destructive_action_requires_operator"} <= names


def test_failure_matrix_covers_faults_and_governance_does_not_retry():
    matrix = failure_injection_matrix(); by_fault = {item.fault: item for item in matrix}
    assert len(matrix) == 18
    assert by_fault["provider_unavailable"].fallback_eligible
    assert by_fault["timeout"].retry_eligible
    assert not by_fault["policy_rejection"].retry_eligible
    assert by_fault["recovery_loop_limit"].operator_escalation_required
    assert by_fault["cancellation"].terminal_state == "cancelled"
    assert by_fault["evidence_corruption"].classification is FailureClassification.INTEGRITY
    assert all(item.contained and item.evidence_reference.startswith("certification://") for item in matrix)


def test_certification_store_restart_replay_collision_and_corruption(tmp_path):
    artifact = certification(); store = SystemIntegrationCertificationStore(tmp_path)
    assert store.save(artifact, idempotency_key="cert-key") == artifact
    assert SystemIntegrationCertificationStore(tmp_path).list() == (artifact,)
    assert store.save(artifact, idempotency_key="cert-key") == artifact
    other = build_integration_scenario(source_commit="different", branch="step29-system-integration-certification", generated_at=29)
    with pytest.raises(ValueError, match="collision"):
        store.save(other, idempotency_key="cert-key")
    path = store.journal_path; original = path.read_bytes()
    payload = json.loads(original); payload["checksum"] = "0" * 64
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ValueError, match="checksum"):
        store.list()
    path.write_bytes(original[:-1])
    with pytest.raises(ValueError, match="truncated"):
        store.list()


def test_invalid_schema_is_rejected(tmp_path):
    artifact = certification(); store = SystemIntegrationCertificationStore(tmp_path)
    store.save(artifact, idempotency_key="cert-key")
    payload = json.loads(store.journal_path.read_text()); payload["artifact"]["schema_version"] = 999
    store.journal_path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(ValueError, match="unsupported system integration"):
        store.list()


@pytest.mark.parametrize(("severity", "expected"), [(None, ReleaseDisposition.READY_FOR_OPERATOR_REVIEW), (FindingSeverity.ADVISORY, ReleaseDisposition.CONDITIONALLY_READY), (FindingSeverity.BLOCKING, ReleaseDisposition.BLOCKED), (FindingSeverity.CRITICAL, ReleaseDisposition.FAILED)])
def test_release_dispositions_and_operator_gate(tmp_path, severity, expected):
    findings = () if severity is None else (finding(severity),)
    result = build_release_readiness(certification(findings), repository_clean=True, required_suites=("focused",), passed_suites=("focused",), generated_at=29)
    assert result.disposition is expected
    assert result.operator_approval_required
    assert {"merge", "release", "deployment"} <= set(result.manifest.operator_approvals_still_required)
    assert result.rollback.known_good_source_commit == "14272ebb1"
    store = ReleaseReadinessStore(tmp_path); store.save(result, idempotency_key="readiness-key")
    assert ReleaseReadinessStore(tmp_path).list() == (result,)


def test_release_manifest_and_visibility_are_deterministic_and_sanitized():
    artifact = certification()
    readiness = build_release_readiness(artifact, repository_clean=True, required_suites=("mission-control", "focused"), passed_suites=("focused", "mission-control"), generated_at=29)
    again = build_release_readiness(artifact, repository_clean=True, required_suites=("focused", "mission-control"), passed_suites=("mission-control", "focused"), generated_at=29)
    assert readiness == again
    events = SystemIntegrationCertificationVisibilityAdapter().certification_events(artifact)
    events += SystemIntegrationCertificationVisibilityAdapter().readiness_events(readiness, project_id=artifact.identity.project_id, task_id=artifact.identity.task_id, correlation_id=artifact.identity.correlation_id)
    assert len({event.event_id for event in events}) == len(events)
    assert all(event.event_type in SYSTEM_INTEGRATION_EVENT_TYPES for event in events)
    assert all(event.project_id == artifact.identity.project_id for event in events)
    assert "deterministic-adapter-model" not in json.dumps([event.model_dump(mode="json") for event in events])


def test_all_step29_event_types_registered_deliberately():
    for event_type in SYSTEM_INTEGRATION_EVENT_TYPES:
        TelemetryEvent(event_id=f"event-{event_type}", event_type=event_type, project_id="project", task_id="task", timestamp=0, severity="info", correlation_id="correlation", causation_id="cause", payload={"source_idempotency_key": event_type})


def test_mission_control_publication_is_event_specific_and_idempotent(tmp_path):
    artifact = certification()
    events = SystemIntegrationCertificationVisibilityAdapter().certification_events(artifact)
    service = SystemIntegrationCertificationVisibilityService(MissionControlService(MissionControlStore(root=tmp_path)))
    assert service.publish(events) == events
    assert service.publish(events) == events
    stored = service._mission_control.get_events(artifact.identity.project_id)
    assert len(stored) == len(events)
    assert len({event.payload["source_idempotency_key"] for event in stored}) == len(events)
