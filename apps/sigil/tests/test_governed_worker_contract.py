from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    GovernedIntegrationRegistry,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    ApprovalRequirements,
    DurableWorkerContractStore,
    EvidenceRequirements,
    GovernedWorkerJob,
    GovernedWorkerResult,
    JobBudget,
    JobState,
    JobStateTransition,
    ResultState,
    WorkerContractStorageError,
    WorkerContractValidationError,
    WorkerUsage,
    evaluate_job_admission,
    validate_job_transition,
)

NOW = "2026-08-01T23:00:00Z"
LATER = "2026-08-02T00:00:00Z"
PIN = "a" * 40


def registry_entry(**changes: object) -> IntegrationRegistryEntry:
    values: dict[str, object] = {
        "integration_id": "synthetic-worker",
        "canonical_project_name": "Synthetic Worker",
        "category": IntegrationCategory.WORKER,
        "repository_url": "https://github.com/example/synthetic-worker",
        "pinned_identity": PIN,
        "release_label": "v1.0.0",
        "upstream_repository_identity": "example/synthetic-worker",
        "maintainer_identity": "example-org",
        "maturity": "certified_fixture",
        "license_classification": "permissive",
        "license_evidence_source": "LICENSE at pinned commit",
        "activity_evidence": "synthetic activity evidence",
        "activity_observed_at": NOW,
        "credential_requirements": (),
        "authentication_requirements": (),
        "declared_network_access": (),
        "declared_egress_destinations": (),
        "declared_filesystem_access": ("sandbox_relative",),
        "declared_tool_permissions": ("read_public_content",),
        "declared_shell_process_authority": (),
        "declared_browser_authority": (),
        "declared_execution_model": "provider_neutral_worker",
        "declared_external_data_transmission": (),
        "install_mechanism": "not_installed",
        "dependency_summary": ("fixture@sha256:" + "b" * 64,),
        "supported_machines": ("titan",),
        "approved_machines": ("titan",),
        "supported_profiles": ("sandbox",),
        "approved_profiles": ("sandbox",),
        "capabilities": ("public_read",),
        "integration_overlap": (),
        "known_risks": ("untrusted_content",),
        "threat_model_references": ("HERMES_ECOSYSTEM_THREAT_MODEL.md",),
        "evaluation_evidence_references": ("fixture-evidence",),
        "rollback_instructions": "Restore prior reviewed revision.",
        "disable_instructions": "Disable worker admission.",
        "quarantine_instructions": "Quarantine worker identity.",
        "lifecycle_state": LifecycleState.CERTIFIED,
        "lifecycle_reason": "Synthetic certified fixture.",
        "created_at": NOW,
        "observed_at": NOW,
        "reviewed_at": NOW,
        "certified_at": NOW,
    }
    values.update(changes)
    return IntegrationRegistryEntry(**values)


def job(**changes: object) -> GovernedWorkerJob:
    payload = {"query": "public information"}
    values: dict[str, object] = {
        "job_id": "job-001",
        "correlation_id": "corr-001",
        "idempotency_key": "idem-001",
        "integration_id": "synthetic-worker",
        "requested_capability": "public_read",
        "requesting_actor_identity": "requester",
        "target_machine": "titan",
        "target_profile": "sandbox",
        "created_at": NOW,
        "deadline_at": LATER,
        "input_payload": payload,
        "input_digest": "sha256:" + canonical_digest(payload),
        "budget": JobBudget("1.25", 300, 2, 10000, 20000),
        "evidence_requirements": EvidenceRequirements(
            True,
            1,
            ("source",),
            True,
            True,
        ),
        "approval_requirements": ApprovalRequirements(
            True,
            "policy-v1",
            ("public_read",),
            1,
        ),
    }
    values.update(changes)
    return GovernedWorkerJob(**values)


def result(**changes: object) -> GovernedWorkerResult:
    output = {"summary": "bounded result"}
    values: dict[str, object] = {
        "job_id": "job-001",
        "contract_digest": job().contract_digest,
        "result_state": ResultState.SUCCEEDED,
        "completed_at": LATER,
        "worker_identity": "worker-titan-001",
        "output_payload": output,
        "output_digest": "sha256:" + canonical_digest(output),
        "evidence_references": ("evidence-001",),
        "audit_references": ("audit-001",),
        "usage": WorkerUsage(1, 30, 128, 256, "0.10"),
    }
    values.update(changes)
    return GovernedWorkerResult(**values)


def test_job_contract_is_immutable_deterministic_and_authority_denied() -> None:
    first = job()
    second = job()
    assert first.contract_digest == second.contract_digest
    assert first.authority.broker_submission is False
    assert first.authority.execution_authorized is False
    assert first.authority.credential_access is False
    assert first.authority.arbitrary_shell is False


def test_input_digest_mismatch_fails_closed() -> None:
    with pytest.raises(WorkerContractValidationError, match="input payload digest"):
        job(input_digest="sha256:" + "0" * 64)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("input_payload", {"api_key": "sk-secretvalue"}, "credential material"),
        ("input_payload", {"path": "/Users/private/data"}, "private host paths"),
        ("input_payload", {"endpoint": "http://192.168.1.5:9000"}, "private endpoints"),
    ],
)
def test_sensitive_input_material_is_rejected(
    field: str,
    value: object,
    message: str,
) -> None:
    digest = "sha256:" + canonical_digest(value)
    with pytest.raises(WorkerContractValidationError, match=message):
        job(**{field: value, "input_digest": digest})


def test_budget_bounds_fail_closed() -> None:
    with pytest.raises(WorkerContractValidationError, match="attempts"):
        JobBudget("1.00", 30, 11, 100, 100)
    with pytest.raises(WorkerContractValidationError, match="runtime"):
        JobBudget("1.00", 0, 1, 100, 100)


def test_job_state_transition_table_is_explicit() -> None:
    validate_job_transition(JobState.PROPOSED, JobState.ADMITTED)
    validate_job_transition(JobState.RUNNING, JobState.COMPLETION_UNKNOWN)
    with pytest.raises(WorkerContractValidationError, match="denied"):
        validate_job_transition(JobState.PROPOSED, JobState.RUNNING)
    with pytest.raises(WorkerContractValidationError, match="denied"):
        validate_job_transition(JobState.SUCCEEDED, JobState.RUNNING)


def test_certified_registry_entry_can_be_admitted_without_activation() -> None:
    registry = GovernedIntegrationRegistry((registry_entry(),))
    decision = evaluate_job_admission(
        job(),
        registry,
        deciding_actor_identity="hermes-admission",
        decided_at=NOW,
    )
    assert decision.admitted is True
    assert decision.approved_capability == "public_read"
    assert decision.integration_entry_digest == registry.entries[0].content_digest
    assert decision.authority.activation_authorized is False
    assert decision.authority.installation_authorized is False


@pytest.mark.parametrize(
    ("entry_changes", "job_changes", "code"),
    [
        ({}, {"integration_id": "unknown-worker"}, "unknown_integration"),
        (
            {"lifecycle_state": LifecycleState.PILOT},
            {},
            "integration_pilot",
        ),
        ({}, {"requested_capability": "write_public_content"}, "capability_not_declared"),
        ({}, {"target_machine": "prime"}, "machine_not_approved"),
        ({}, {"target_profile": "production"}, "profile_not_approved"),
    ],
)
def test_admission_denials_are_specific_and_fail_closed(
    entry_changes: dict[str, object],
    job_changes: dict[str, object],
    code: str,
) -> None:
    registry = GovernedIntegrationRegistry((registry_entry(**entry_changes),))
    decision = evaluate_job_admission(
        job(**job_changes),
        registry,
        deciding_actor_identity="hermes-admission",
        decided_at=NOW,
    )
    assert decision.admitted is False
    assert decision.rejection_code == code
    assert decision.integration_entry_digest is None
    assert decision.approved_capability is None


def test_requester_cannot_self_admit() -> None:
    with pytest.raises(WorkerContractValidationError, match="self-admit"):
        evaluate_job_admission(
            job(),
            GovernedIntegrationRegistry((registry_entry(),)),
            deciding_actor_identity="requester",
            decided_at=NOW,
        )


def test_result_must_match_admitted_job_and_respect_budget_and_evidence() -> None:
    value = job()
    admission = evaluate_job_admission(
        value,
        GovernedIntegrationRegistry((registry_entry(),)),
        deciding_actor_identity="hermes-admission",
        decided_at=NOW,
    )
    valid = result(contract_digest=value.contract_digest)
    valid.validate_for(value, admission)

    with pytest.raises(WorkerContractValidationError, match="runtime exceeded"):
        replace(
            valid,
            usage=WorkerUsage(1, 301, 128, 256, "0.10"),
        ).validate_for(value, admission)

    with pytest.raises(WorkerContractValidationError, match="missing required evidence"):
        replace(valid, evidence_references=()).validate_for(value, admission)


def test_failed_result_requires_normalized_error_and_rejects_secrets() -> None:
    with pytest.raises(WorkerContractValidationError, match="error code"):
        result(result_state=ResultState.FAILED)

    with pytest.raises(WorkerContractValidationError, match="credential material"):
        result(
            result_state=ResultState.FAILED,
            error_code="provider_failure",
            error_message="api_key=sk-secretvalue",
        )



def transition(**changes: object) -> JobStateTransition:
    values: dict[str, object] = {
        "job_id": "job-001",
        "contract_digest": job().contract_digest,
        "previous_state": JobState.PROPOSED,
        "requested_state": JobState.ADMITTED,
        "actor_identity": "hermes-admission",
        "occurred_at": NOW,
        "reason": "Job passed governed admission.",
        "evidence_references": ("admission-001",),
    }
    values.update(changes)
    return JobStateTransition(**values)


def test_job_snapshot_round_trip_and_integrity_failure(tmp_path: Path) -> None:
    store = DurableWorkerContractStore(tmp_path.resolve())
    value = job()

    assert store.save_job(value) == value.contract_digest

    loaded = store.load_job(value.job_id)

    assert loaded["job_id"] == value.job_id
    assert loaded["contract_digest"] == value.contract_digest
    assert loaded["state"] == JobState.PROPOSED.value

    path = store.jobs_directory / f"{value.job_id}.json"
    payload = json.loads(path.read_text())
    payload["requested_capability"] = "mutated"
    path.write_text(json.dumps(payload))

    with pytest.raises(WorkerContractStorageError, match="integrity"):
        store.load_job(value.job_id)


def test_job_transition_evidence_is_append_only_and_hash_linked(
    tmp_path: Path,
) -> None:
    store = DurableWorkerContractStore(tmp_path.resolve())

    first = store.append_transition(transition())

    second = store.append_transition(
        transition(
            previous_state=JobState.ADMITTED,
            requested_state=JobState.QUEUED,
            occurred_at=LATER,
            reason="Job entered the governed queue.",
        )
    )

    records = store.read_evidence()

    assert records == (first, second)
    assert second["previous_record_hash"] == first["entry_hash"]
    assert records[0]["broker_submission"] is False
    assert records[0]["execution_authorized"] is False
    assert records[0]["credential_access"] is False
    assert records[0]["activation_authorized"] is False


def test_corrupt_transition_evidence_fails_closed(tmp_path: Path) -> None:
    store = DurableWorkerContractStore(tmp_path.resolve())

    store.append_transition(transition())
    store.evidence_path.write_text("{corrupt")

    with pytest.raises(WorkerContractStorageError, match="invalid"):
        store.read_evidence()


def test_transition_rejects_sensitive_material() -> None:
    with pytest.raises(
        WorkerContractValidationError,
        match="credential material",
    ):
        transition(reason="api_key=sk-secretvalue")
