from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.paperclip_adapter import (
    PAPERCLIP_ADAPTER_SCHEMA_VERSION,
    PaperclipAdapterConfig,
    PaperclipAgentRef,
    PaperclipCommentRef,
    PaperclipEvidenceRef,
    PaperclipHeartbeat,
    PaperclipHeartbeatState,
    PaperclipIssueState,
    PaperclipOrganizationRef,
    PaperclipProjectionHealth,
    PaperclipValidationError,
    PaperclipWorkspaceRef,
    evaluate_projection_status,
    lifecycle_projection,
    project_worker_job,
    validate_paperclip_registry_entry,
)
from sigil.worker_contract import (
    ApprovalRequirements,
    EvidenceRequirements,
    GovernedWorkerJob,
    JobBudget,
    JobState,
    WORKER_CONTRACT_SCHEMA_VERSION,
)


NOW = "2026-08-01T23:45:00Z"
LATER = "2026-08-01T23:46:00Z"
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64


def make_job(
    *,
    state: JobState = JobState.PROPOSED,
    integration_id: str = "paperclip",
) -> GovernedWorkerJob:
    payload = {
        "organization_id": "firecat",
        "project_id": "sigil",
        "issue_id": "issue-004",
    }

    return GovernedWorkerJob(
        job_id="job-paperclip-004",
        correlation_id="corr-paperclip-004",
        idempotency_key="idem-paperclip-004",
        integration_id=integration_id,
        requested_capability="organization_projection",
        requesting_actor_identity="hermes-control-plane",
        target_machine="hermes-titan",
        target_profile="governed-worker",
        created_at=NOW,
        deadline_at="2026-08-02T00:45:00Z",
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
            policy_revision="paperclip-stage4",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
        state=state,
    )


def organization() -> PaperclipOrganizationRef:
    return PaperclipOrganizationRef(
        organization_id="firecat",
        project_id="sigil",
        organization_name="FireCat Technology",
        project_name="Sigil",
    )


def agent() -> PaperclipAgentRef:
    return PaperclipAgentRef(
        agent_id="hermes-titan-worker",
        employee_id="employee-titan-001",
        display_name="Hermes Titan Worker",
        role_id="governed-engineer",
        worker_profile="governed-worker",
        active=True,
    )


def workspace() -> PaperclipWorkspaceRef:
    return PaperclipWorkspaceRef(
        repository_identity="firecattechllc/hermes-agent",
        revision=REVISION,
        workspace_reference="workspaces/sigil-stage4",
        worktree_reference="worktrees/issue-004",
    )


def evidence() -> PaperclipEvidenceRef:
    return PaperclipEvidenceRef(
        evidence_id="evidence-stage4-tests",
        kind="test_result",
        content_digest=DIGEST,
        provenance="pytest focused Stage 4 suite",
        reference="evidence/stage4-focused-tests.json",
    )


def comment() -> PaperclipCommentRef:
    return PaperclipCommentRef(
        comment_id="comment-001",
        author_identity="hermes-control-plane",
        created_at=NOW,
        transcript_reference="transcripts/issue-004.json",
        content_digest=DIGEST,
    )


def projection(
    *,
    config: PaperclipAdapterConfig | None = None,
    state: JobState = JobState.PROPOSED,
    assigned: bool = True,
):
    return project_worker_job(
        PaperclipAdapterConfig() if config is None else config,
        make_job(state=state),
        organization=organization(),
        issue_id="issue-004",
        issue_title="Implement governed Paperclip adapter",
        assigned_agent=agent() if assigned else None,
        priority=80,
        updated_at=LATER,
        comments=(comment(),),
        workspaces=(workspace(),),
        evidence=(evidence(),),
        recorded_cost_usd="1.25",
        runtime_seconds=120,
        attempt_count=1,
    )


def make_registry_entry(
    *,
    integration_id: str = "paperclip",
    category: IntegrationCategory = IntegrationCategory.ORGANIZATION,
    lifecycle: LifecycleState = LifecycleState.DISCOVERED,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id=integration_id,
        canonical_project_name="Paperclip",
        category=category,
        repository_url="https://github.com/paperclipai/paperclip",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="paperclipai/paperclip",
        maintainer_identity="paperclipai",
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
        declared_execution_model="descriptive adapter only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan", "hermes-mac"),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("organization_projection",),
        integration_overlap=("hermes-control-plane",),
        known_risks=("remote state mutation",),
        threat_model_references=("docs/threat-models/paperclip.md",),
        evaluation_evidence_references=("docs/evidence/paperclip.md",),
        rollback_instructions="Remove the disabled adapter projection.",
        disable_instructions="Keep the adapter disabled.",
        quarantine_instructions="Reject all Paperclip projections.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 4 contract evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def test_adapter_is_disabled_and_has_no_authority() -> None:
    config = PaperclipAdapterConfig()

    assert config.schema_version == PAPERCLIP_ADAPTER_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_connect is False
    assert config.can_authenticate is False
    assert config.can_dispatch is False
    assert config.can_mutate_remote_state is False
    assert config.can_create_workspace is False
    assert config.can_spend is False
    assert config.authority == AuthorityDenials()


def test_adapter_rejects_worker_schema_mismatch() -> None:
    with pytest.raises(PaperclipValidationError, match="incompatible"):
        PaperclipAdapterConfig(expected_worker_contract_schema=999)


def test_registry_entry_validation_accepts_organization_entry() -> None:
    validate_paperclip_registry_entry(
        PaperclipAdapterConfig(),
        make_registry_entry(),
    )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            make_registry_entry(integration_id="buzz"),
            "identity mismatch",
        ),
        (
            make_registry_entry(
                category=IntegrationCategory.COLLABORATION
            ),
            "organization integration",
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
    with pytest.raises(PaperclipValidationError, match=message):
        validate_paperclip_registry_entry(
            PaperclipAdapterConfig(),
            entry,
        )


def test_projection_is_immutable_and_deterministic() -> None:
    first = projection()
    second = projection()

    assert first == second
    assert first.projection_digest == second.projection_digest
    assert first.projection_digest.startswith("sha256:")
    assert first.can_execute is False
    assert first.can_approve is False
    assert first.can_mutate_portfolio is False


def test_projection_rejects_digest_tampering() -> None:
    value = projection()

    with pytest.raises(PaperclipValidationError, match="digest mismatch"):
        replace(value, issue_title="Changed after digest")


@pytest.mark.parametrize(
    ("job_state", "paperclip_state"),
    [
        (JobState.PROPOSED, PaperclipIssueState.BACKLOG),
        (JobState.ADMITTED, PaperclipIssueState.ASSIGNED),
        (JobState.REJECTED, PaperclipIssueState.FAILED),
        (JobState.QUEUED, PaperclipIssueState.QUEUED),
        (JobState.RUNNING, PaperclipIssueState.IN_PROGRESS),
        (
            JobState.CANCELLATION_REQUESTED,
            PaperclipIssueState.CANCELLATION_REQUESTED,
        ),
        (JobState.CANCELLED, PaperclipIssueState.CANCELLED),
        (JobState.SUCCEEDED, PaperclipIssueState.COMPLETED),
        (JobState.FAILED, PaperclipIssueState.FAILED),
        (
            JobState.COMPLETION_UNKNOWN,
            PaperclipIssueState.COMPLETION_UNKNOWN,
        ),
    ],
)
def test_worker_lifecycle_projection(
    job_state: JobState,
    paperclip_state: PaperclipIssueState,
) -> None:
    assert projection(state=job_state).state is paperclip_state
    assert lifecycle_projection()[job_state] is paperclip_state


def test_projection_preserves_job_correlation_and_idempotency() -> None:
    value = projection()
    job = make_job()

    assert value.worker_job_id == job.job_id
    assert value.correlation_id == job.correlation_id
    assert value.idempotency_key == job.idempotency_key
    assert value.worker_contract_digest == job.contract_digest
    assert (
        value.worker_contract_schema
        == WORKER_CONTRACT_SCHEMA_VERSION
    )


def test_projection_rejects_wrong_integration() -> None:
    with pytest.raises(PaperclipValidationError, match="does not match"):
        project_worker_job(
            PaperclipAdapterConfig(),
            make_job(integration_id="buzz"),
            organization=organization(),
            issue_id="issue-004",
            issue_title="Wrong integration",
            assigned_agent=agent(),
            priority=80,
            updated_at=LATER,
        )


def test_projection_rejects_recorded_cost_over_budget() -> None:
    with pytest.raises(PaperclipValidationError, match="exceeds"):
        project_worker_job(
            PaperclipAdapterConfig(),
            make_job(),
            organization=organization(),
            issue_id="issue-004",
            issue_title="Excess cost",
            assigned_agent=agent(),
            priority=80,
            updated_at=LATER,
            recorded_cost_usd="5.01",
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/project",
        "/home/operator/project",
        "../outside",
        "workspaces/../../outside",
        "http://127.0.0.1:3000/workspace",
    ],
)
def test_workspace_references_reject_private_or_escaping_paths(
    bad_reference: str,
) -> None:
    with pytest.raises(PaperclipValidationError):
        PaperclipWorkspaceRef(
            repository_identity="firecattechllc/hermes-agent",
            revision=REVISION,
            workspace_reference=bad_reference,
            worktree_reference=None,
        )


def test_workspace_reference_is_read_only() -> None:
    with pytest.raises(PaperclipValidationError, match="read-only"):
        replace(workspace(), read_only=False)


def test_comments_and_evidence_reject_credentials() -> None:
    with pytest.raises(PaperclipValidationError, match="credential"):
        PaperclipCommentRef(
            comment_id="comment-secret",
            author_identity="operator",
            created_at=NOW,
            transcript_reference="api_key=secret-value",
            content_digest=DIGEST,
        )

    with pytest.raises(PaperclipValidationError, match="credential"):
        PaperclipEvidenceRef(
            evidence_id="secret-evidence",
            kind="test_result",
            content_digest=DIGEST,
            provenance="access_token=secret-value",
            reference="evidence/result.json",
        )


def test_disabled_status_fails_closed_even_with_current_heartbeat() -> None:
    value = projection()
    heartbeat = PaperclipHeartbeat(
        agent_id=agent().agent_id,
        observed_at=LATER,
        sequence=1,
        state=PaperclipHeartbeatState.WORKING,
        current_issue_id=value.issue_id,
        sanitized_summary="Working on the governed adapter.",
    )

    status = evaluate_projection_status(
        PaperclipAdapterConfig(),
        value,
        heartbeat=heartbeat,
        heartbeat_age_seconds=1,
    )

    assert status.state is PaperclipProjectionHealth.DISABLED
    assert status.enabled is False
    assert status.heartbeat_current is False


def test_enabled_projection_with_current_heartbeat_is_ready() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)
    heartbeat = PaperclipHeartbeat(
        agent_id=agent().agent_id,
        observed_at=LATER,
        sequence=1,
        state=PaperclipHeartbeatState.WORKING,
        current_issue_id=value.issue_id,
        sanitized_summary="Working on the governed adapter.",
    )

    status = evaluate_projection_status(
        config,
        value,
        heartbeat=heartbeat,
        heartbeat_age_seconds=10,
    )

    assert status.state is PaperclipProjectionHealth.READY
    assert status.enabled is True
    assert status.worker_contract_compatible is True
    assert status.heartbeat_current is True


def test_enabled_unassigned_projection_needs_no_heartbeat() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config, assigned=False)

    status = evaluate_projection_status(
        config,
        value,
        heartbeat=None,
        heartbeat_age_seconds=None,
    )

    assert status.state is PaperclipProjectionHealth.READY
    assert status.heartbeat_current is False


def test_assigned_projection_without_heartbeat_is_stale() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)

    status = evaluate_projection_status(
        config,
        value,
        heartbeat=None,
        heartbeat_age_seconds=None,
    )

    assert status.state is PaperclipProjectionHealth.STALE
    assert status.heartbeat_current is False


def test_stale_heartbeat_fails_closed() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)
    heartbeat = PaperclipHeartbeat(
        agent_id=agent().agent_id,
        observed_at=NOW,
        sequence=2,
        state=PaperclipHeartbeatState.WORKING,
        current_issue_id=value.issue_id,
        sanitized_summary="Last known activity.",
    )

    status = evaluate_projection_status(
        config,
        value,
        heartbeat=heartbeat,
        heartbeat_age_seconds=121,
        stale_after_seconds=120,
    )

    assert status.state is PaperclipProjectionHealth.STALE
    assert status.heartbeat_current is False


def test_future_heartbeat_fails_closed() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)
    heartbeat = PaperclipHeartbeat(
        agent_id=agent().agent_id,
        observed_at=LATER,
        sequence=1,
        state=PaperclipHeartbeatState.READY,
        current_issue_id=value.issue_id,
        sanitized_summary="Ready.",
    )

    with pytest.raises(PaperclipValidationError, match="future"):
        evaluate_projection_status(
            config,
            value,
            heartbeat=heartbeat,
            heartbeat_age_seconds=-1,
        )


def test_mismatched_agent_heartbeat_fails_closed() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)
    heartbeat = PaperclipHeartbeat(
        agent_id="different-agent",
        observed_at=LATER,
        sequence=1,
        state=PaperclipHeartbeatState.WORKING,
        current_issue_id=value.issue_id,
        sanitized_summary="Wrong agent.",
    )

    with pytest.raises(PaperclipValidationError, match="does not match"):
        evaluate_projection_status(
            config,
            value,
            heartbeat=heartbeat,
            heartbeat_age_seconds=1,
        )


def test_mismatched_issue_heartbeat_fails_closed() -> None:
    config = PaperclipAdapterConfig(enabled=True)
    value = projection(config=config)
    heartbeat = PaperclipHeartbeat(
        agent_id=agent().agent_id,
        observed_at=LATER,
        sequence=1,
        state=PaperclipHeartbeatState.WORKING,
        current_issue_id="different-issue",
        sanitized_summary="Wrong issue.",
    )

    with pytest.raises(PaperclipValidationError, match="issue does not match"):
        evaluate_projection_status(
            config,
            value,
            heartbeat=heartbeat,
            heartbeat_age_seconds=1,
        )


def test_duplicate_comments_fail_closed() -> None:
    duplicate = comment()

    with pytest.raises(PaperclipValidationError, match="duplicate comment"):
        project_worker_job(
            PaperclipAdapterConfig(),
            make_job(),
            organization=organization(),
            issue_id="issue-004",
            issue_title="Duplicate comments",
            assigned_agent=agent(),
            priority=80,
            updated_at=LATER,
            comments=(duplicate, duplicate),
        )


def test_duplicate_evidence_fails_closed() -> None:
    duplicate = evidence()

    with pytest.raises(PaperclipValidationError, match="duplicate evidence"):
        project_worker_job(
            PaperclipAdapterConfig(),
            make_job(),
            organization=organization(),
            issue_id="issue-004",
            issue_title="Duplicate evidence",
            assigned_agent=agent(),
            priority=80,
            updated_at=LATER,
            evidence=(duplicate, duplicate),
        )
