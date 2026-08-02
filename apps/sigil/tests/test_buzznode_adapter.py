from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai.registry import canonical_digest
from sigil.buzznode_adapter import (
    BUZZNODE_ADAPTER_SCHEMA_VERSION,
    BuzznodeAdapterConfig,
    BuzznodeBrowserSessionRef,
    BuzznodeCapabilitySet,
    BuzznodeHealth,
    BuzznodeHeartbeat,
    BuzznodeIdentity,
    BuzznodeLease,
    BuzznodeLeaseState,
    BuzznodeProjection,
    BuzznodeResourceLimits,
    BuzznodeRole,
    BuzznodeValidationError,
    BuzznodeWorkspaceRef,
    BuzznodeWorkState,
    evaluate_buzznode_status,
    lifecycle_projection,
    project_worker_job,
    validate_buzznode_registry_entry,
)
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    ApprovalRequirements,
    EvidenceRequirements,
    GovernedWorkerJob,
    JobBudget,
    JobState,
)


NOW = "2026-08-02T00:05:00Z"
LATER = "2026-08-02T01:05:00Z"
REVISION = "a" * 40


def make_job(
    *,
    state: JobState = JobState.PROPOSED,
    integration_id: str = "buzznode",
) -> GovernedWorkerJob:
    payload = {
        "node_id": "buzznode-001",
        "workspace_id": "workspace-001",
    }

    return GovernedWorkerJob(
        job_id="job-buzznode-006",
        correlation_id="corr-buzznode-006",
        idempotency_key="idem-buzznode-006",
        integration_id=integration_id,
        requested_capability="persistent_worker",
        requesting_actor_identity="hermes-control-plane",
        target_machine="buzznode-001",
        target_profile="governed-worker",
        created_at=NOW,
        deadline_at=LATER,
        input_payload=payload,
        input_digest=f"sha256:{canonical_digest(payload)}",
        budget=JobBudget(
            maximum_cost_usd="5.00",
            maximum_runtime_seconds=3600,
            maximum_attempts=3,
            maximum_input_bytes=100000,
            maximum_output_bytes=100000,
        ),
        evidence_requirements=EvidenceRequirements(
            required=True,
            minimum_references=1,
            required_kinds=("test_result",),
            require_content_digests=True,
            require_provenance=True,
        ),
        approval_requirements=ApprovalRequirements(
            required=False,
            policy_revision="buzznode-stage6",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
        state=state,
    )


def identity() -> BuzznodeIdentity:
    return BuzznodeIdentity(
        node_id="buzznode-001",
        machine_id="machine-001",
        display_name="Buzznode 001",
        role=BuzznodeRole.PERSISTENT_WORKER,
        platform="linux",
        architecture="arm64",
        worker_profile="governed-worker",
    )


def resources() -> BuzznodeResourceLimits:
    return BuzznodeResourceLimits(
        cpu_cores=8,
        memory_megabytes=16384,
        storage_megabytes=262144,
        maximum_concurrent_jobs=2,
        maximum_runtime_seconds=86400,
        maximum_browser_sessions=2,
    )


def capabilities() -> BuzznodeCapabilitySet:
    return BuzznodeCapabilitySet(
        capabilities=(
            "persistent_worker",
            "browser_session_reference",
            "isolated_workspace_reference",
        ),
        browser_available=True,
        persistent_workspace_available=True,
        network_access_declared=True,
    )


def workspace() -> BuzznodeWorkspaceRef:
    return BuzznodeWorkspaceRef(
        workspace_id="workspace-001",
        repository_identity="firecattechllc/hermes-agent",
        revision=REVISION,
        workspace_reference="workspaces/buzznode-001",
        persistent=True,
        isolated=True,
    )


def browser_session() -> BuzznodeBrowserSessionRef:
    return BuzznodeBrowserSessionRef(
        session_id="browser-session-001",
        workspace_id="workspace-001",
        browser_profile="research",
        created_at=NOW,
        expires_at=LATER,
        state_reference="browser-sessions/session-001.json",
    )


def lease(
    *,
    state: BuzznodeLeaseState = BuzznodeLeaseState.ACTIVE,
    job_id: str | None = "job-buzznode-006",
) -> BuzznodeLease:
    return BuzznodeLease(
        lease_id="lease-001",
        node_id="buzznode-001",
        job_id=job_id,
        issued_at=NOW,
        expires_at=LATER,
        state=state,
        generation=1,
    )


def projection(
    *,
    lease_value: BuzznodeLease | None = None,
    sessions: tuple[BuzznodeBrowserSessionRef, ...] | None = None,
) -> BuzznodeProjection:
    return BuzznodeProjection(
        identity=identity(),
        resources=resources(),
        capabilities=capabilities(),
        workspaces=(workspace(),),
        browser_sessions=(
            (browser_session(),) if sessions is None else sessions
        ),
        lease=lease() if lease_value is None else lease_value,
        expected_worker_contract_schema=1,
    )


def heartbeat(
    *,
    node_id: str = "buzznode-001",
    online: bool = True,
    running_jobs: int = 1,
    active_browser_sessions: int = 1,
    worker_contract_schema: int = 1,
) -> BuzznodeHeartbeat:
    return BuzznodeHeartbeat(
        node_id=node_id,
        observed_at=NOW,
        sequence=1,
        online=online,
        running_jobs=running_jobs,
        active_browser_sessions=active_browser_sessions,
        worker_contract_schema=worker_contract_schema,
        sanitized_summary="Buzznode heartbeat evidence.",
    )


def make_registry_entry(
    *,
    integration_id: str = "buzznode",
    category: IntegrationCategory = IntegrationCategory.WORKER,
    lifecycle: LifecycleState = LifecycleState.DISCOVERED,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id=integration_id,
        canonical_project_name="Buzznode",
        category=category,
        repository_url="https://github.com/nousresearch/buzznode",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="nousresearch/buzznode",
        maintainer_identity="nousresearch",
        maturity="under evaluation",
        license_classification="open source",
        license_evidence_source="upstream repository",
        activity_evidence="repository activity inspected",
        activity_observed_at=NOW,
        credential_requirements=(),
        authentication_requirements=(),
        declared_network_access=(),
        declared_egress_destinations=(),
        declared_filesystem_access=(),
        declared_tool_permissions=(),
        declared_shell_process_authority=(),
        declared_browser_authority=(),
        declared_execution_model="descriptive worker-host adapter only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan", "hermes-mac"),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("persistent_worker",),
        integration_overlap=("hermes-control-plane",),
        known_risks=("remote execution", "credential exposure"),
        threat_model_references=("docs/threat-models/buzznode.md",),
        evaluation_evidence_references=("docs/evidence/buzznode.md",),
        rollback_instructions="Remove the disabled Buzznode projection.",
        disable_instructions="Keep the adapter disabled.",
        quarantine_instructions="Reject all Buzznode projections.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 6 contract evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def test_config_is_disabled_and_has_no_authority() -> None:
    config = BuzznodeAdapterConfig()

    assert config.schema_version == BUZZNODE_ADAPTER_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_provision is False
    assert config.can_connect is False
    assert config.can_authenticate is False
    assert config.can_ssh is False
    assert config.can_execute_shell is False
    assert config.can_access_filesystem is False
    assert config.can_launch_browser is False
    assert config.can_dispatch is False
    assert config.authority == AuthorityDenials()


def test_config_rejects_worker_schema_mismatch() -> None:
    with pytest.raises(BuzznodeValidationError, match="incompatible"):
        BuzznodeAdapterConfig(expected_worker_contract_schema=999)


def test_registry_entry_validation_accepts_worker_entry() -> None:
    validate_buzznode_registry_entry(
        BuzznodeAdapterConfig(),
        make_registry_entry(),
    )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            make_registry_entry(integration_id="buzz-relay"),
            "identity mismatch",
        ),
        (
            make_registry_entry(
                category=IntegrationCategory.COLLABORATION
            ),
            "worker integration",
        ),
        (
            make_registry_entry(
                lifecycle=LifecycleState.QUARANTINED
            ),
            "not eligible",
        ),
    ],
)
def test_registry_entry_validation_fails_closed(
    entry: IntegrationRegistryEntry,
    message: str,
) -> None:
    with pytest.raises(BuzznodeValidationError, match=message):
        validate_buzznode_registry_entry(
            BuzznodeAdapterConfig(),
            entry,
        )


def test_projection_is_immutable_and_deterministic() -> None:
    first = projection()
    second = projection()

    assert first == second
    assert first.projection_digest == second.projection_digest
    assert first.projection_digest.startswith("sha256:")
    assert first.can_provision is False
    assert first.can_execute is False
    assert first.can_open_workspace is False


def test_projection_rejects_digest_tampering() -> None:
    value = projection()

    with pytest.raises(BuzznodeValidationError, match="digest mismatch"):
        replace(value, browser_sessions=(), projection_digest=value.projection_digest)


def test_capabilities_reject_shell_and_credentials() -> None:
    with pytest.raises(BuzznodeValidationError, match="credential"):
        replace(capabilities(), credential_mount_available=True)

    with pytest.raises(BuzznodeValidationError, match="shell"):
        replace(capabilities(), shell_available=True)

    with pytest.raises(BuzznodeValidationError, match="filesystem"):
        replace(capabilities(), arbitrary_filesystem_available=True)


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/workspace",
        "/home/operator/workspace",
        "../outside",
        "workspaces/../../outside",
        "http://127.0.0.1:3000/workspace",
    ],
)
def test_workspace_references_reject_private_or_escaping_values(
    bad_reference: str,
) -> None:
    with pytest.raises(BuzznodeValidationError):
        replace(
            workspace(),
            workspace_reference=bad_reference,
        )


def test_workspace_must_be_isolated_and_read_only() -> None:
    with pytest.raises(BuzznodeValidationError, match="isolated"):
        replace(workspace(), isolated=False)

    with pytest.raises(BuzznodeValidationError, match="read-only"):
        replace(workspace(), read_only_reference=False)


def test_browser_session_requires_known_workspace() -> None:
    unknown = replace(
        browser_session(),
        workspace_id="workspace-999",
    )

    with pytest.raises(BuzznodeValidationError, match="unknown workspace"):
        BuzznodeProjection(
            identity=identity(),
            resources=resources(),
            capabilities=capabilities(),
            workspaces=(workspace(),),
            browser_sessions=(unknown,),
            lease=lease(),
            expected_worker_contract_schema=1,
        )


def test_browser_session_limit_fails_closed() -> None:
    second = replace(
        browser_session(),
        session_id="browser-session-002",
    )
    third = replace(
        browser_session(),
        session_id="browser-session-003",
    )

    with pytest.raises(BuzznodeValidationError, match="resource limits"):
        projection(sessions=(browser_session(), second, third))


def test_active_lease_requires_job_identity() -> None:
    with pytest.raises(BuzznodeValidationError, match="requires"):
        lease(state=BuzznodeLeaseState.ACTIVE, job_id=None)


def test_unassigned_lease_rejects_job_identity() -> None:
    with pytest.raises(BuzznodeValidationError, match="cannot reference"):
        lease(
            state=BuzznodeLeaseState.UNASSIGNED,
            job_id="job-buzznode-006",
        )


def test_disabled_status_fails_closed() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(),
        projection(),
        heartbeat=heartbeat(),
        heartbeat_age_seconds=1,
        lease_age_seconds=1,
    )

    assert status.state is BuzznodeHealth.DISABLED
    assert status.enabled is False
    assert status.heartbeat_current is False
    assert status.lease_valid is False


def test_enabled_online_busy_node_is_busy() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=heartbeat(running_jobs=1),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.BUSY
    assert status.heartbeat_current is True
    assert status.worker_contract_compatible is True
    assert status.lease_valid is True


def test_enabled_idle_node_is_ready() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(
            lease_value=lease(
                state=BuzznodeLeaseState.UNASSIGNED,
                job_id=None,
            )
        ),
        heartbeat=heartbeat(running_jobs=0),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.READY
    assert status.lease_valid is True


def test_missing_heartbeat_is_stale() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=None,
        heartbeat_age_seconds=None,
        lease_age_seconds=None,
    )

    assert status.state is BuzznodeHealth.STALE
    assert status.heartbeat_current is False


def test_stale_heartbeat_is_stale() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=heartbeat(),
        heartbeat_age_seconds=121,
        lease_age_seconds=10,
        stale_after_seconds=120,
    )

    assert status.state is BuzznodeHealth.STALE


def test_offline_heartbeat_is_offline() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=heartbeat(online=False),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.OFFLINE


def test_incompatible_worker_schema_fails_closed() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=heartbeat(worker_contract_schema=999),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.INCOMPATIBLE
    assert status.worker_contract_compatible is False


def test_expired_lease_is_degraded() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(
            lease_value=lease(
                state=BuzznodeLeaseState.EXPIRED,
                job_id="job-buzznode-006",
            )
        ),
        heartbeat=heartbeat(),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.DEGRADED
    assert status.lease_valid is False


def test_resource_overage_is_degraded() -> None:
    status = evaluate_buzznode_status(
        BuzznodeAdapterConfig(enabled=True),
        projection(),
        heartbeat=heartbeat(
            running_jobs=3,
            active_browser_sessions=3,
        ),
        heartbeat_age_seconds=10,
        lease_age_seconds=10,
    )

    assert status.state is BuzznodeHealth.DEGRADED


def test_mismatched_heartbeat_node_fails_closed() -> None:
    with pytest.raises(BuzznodeValidationError, match="does not match"):
        evaluate_buzznode_status(
            BuzznodeAdapterConfig(enabled=True),
            projection(),
            heartbeat=heartbeat(node_id="buzznode-999"),
            heartbeat_age_seconds=1,
            lease_age_seconds=1,
        )


def test_future_heartbeat_fails_closed() -> None:
    with pytest.raises(BuzznodeValidationError, match="future"):
        evaluate_buzznode_status(
            BuzznodeAdapterConfig(enabled=True),
            projection(),
            heartbeat=heartbeat(),
            heartbeat_age_seconds=-1,
            lease_age_seconds=1,
        )


@pytest.mark.parametrize(
    ("job_state", "buzznode_state"),
    [
        (JobState.PROPOSED, BuzznodeWorkState.PROPOSED),
        (JobState.ADMITTED, BuzznodeWorkState.ADMITTED),
        (JobState.REJECTED, BuzznodeWorkState.REJECTED),
        (JobState.QUEUED, BuzznodeWorkState.QUEUED),
        (JobState.RUNNING, BuzznodeWorkState.RUNNING),
        (
            JobState.CANCELLATION_REQUESTED,
            BuzznodeWorkState.CANCELLATION_REQUESTED,
        ),
        (JobState.CANCELLED, BuzznodeWorkState.CANCELLED),
        (JobState.SUCCEEDED, BuzznodeWorkState.SUCCEEDED),
        (JobState.FAILED, BuzznodeWorkState.FAILED),
        (
            JobState.COMPLETION_UNKNOWN,
            BuzznodeWorkState.COMPLETION_UNKNOWN,
        ),
    ],
)
def test_worker_lifecycle_projection(
    job_state: JobState,
    buzznode_state: BuzznodeWorkState,
) -> None:
    result = project_worker_job(
        BuzznodeAdapterConfig(),
        make_job(state=job_state),
    )

    assert result.state is buzznode_state
    assert lifecycle_projection()[job_state] is buzznode_state


def test_projection_preserves_job_identity_and_target() -> None:
    job = make_job()
    result = project_worker_job(BuzznodeAdapterConfig(), job)

    assert result.job_id == job.job_id
    assert result.correlation_id == job.correlation_id
    assert result.idempotency_key == job.idempotency_key
    assert result.target_machine == job.target_machine
    assert result.target_profile == job.target_profile
    assert result.worker_contract_digest == job.contract_digest


def test_projection_rejects_wrong_integration() -> None:
    with pytest.raises(BuzznodeValidationError, match="does not match"):
        project_worker_job(
            BuzznodeAdapterConfig(),
            make_job(integration_id="buzz-relay"),
        )
