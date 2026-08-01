from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai.registry import canonical_digest
from sigil.buzz_relay_adapter import (
    BUZZ_RELAY_ADAPTER_SCHEMA_VERSION,
    BuzzActorKind,
    BuzzActorRef,
    BuzzApprovalRef,
    BuzzDeliveryState,
    BuzzEventKind,
    BuzzEvidenceRef,
    BuzzGitRef,
    BuzzRelayConfig,
    BuzzRelayEvent,
    BuzzRelayValidationError,
    BuzzSpaceRef,
    BuzzThreadRef,
    BuzzWorkState,
    evaluate_relay_event,
    initial_replay_window,
    lifecycle_projection,
    project_worker_job,
    validate_buzz_registry_entry,
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


NOW = "2026-08-01T23:58:00Z"
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64
ZERO_DIGEST = "sha256:" + "0" * 64
SIGNATURE = "ed25519:" + "A" * 64


def make_job(
    *,
    state: JobState = JobState.PROPOSED,
    integration_id: str = "buzz-relay",
) -> GovernedWorkerJob:
    payload = {
        "workspace_id": "firecat",
        "project_id": "sigil",
        "channel_id": "engineering",
    }

    return GovernedWorkerJob(
        job_id="job-buzz-005",
        correlation_id="corr-buzz-005",
        idempotency_key="idem-buzz-005",
        integration_id=integration_id,
        requested_capability="collaboration_projection",
        requesting_actor_identity="hermes-control-plane",
        target_machine="hermes-titan",
        target_profile="governed-worker",
        created_at=NOW,
        deadline_at="2026-08-02T00:58:00Z",
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
            policy_revision="buzz-stage5",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
        state=state,
    )


def space() -> BuzzSpaceRef:
    return BuzzSpaceRef(
        workspace_id="firecat",
        project_id="sigil",
        channel_id="engineering",
        workspace_name="FireCat",
        project_name="Sigil",
        channel_name="Engineering",
    )


def actor() -> BuzzActorRef:
    return BuzzActorRef(
        actor_id="hermes-titan",
        display_name="Hermes Titan",
        kind=BuzzActorKind.AGENT,
        organization_identity="firecat-technology",
    )


def thread() -> BuzzThreadRef:
    return BuzzThreadRef(
        message_id="message-005",
        thread_id="thread-005",
        parent_message_id=None,
    )


def evidence() -> BuzzEvidenceRef:
    return BuzzEvidenceRef(
        evidence_id="evidence-stage5",
        kind="test_result",
        content_digest=DIGEST,
        provenance="pytest focused Stage 5 suite",
        reference="evidence/stage5-tests.json",
    )


def make_event(
    *,
    event_id: str = "event-005",
    sequence: int = 0,
    idempotency_key: str = "idem-event-005",
    previous_event_digest: str = ZERO_DIGEST,
    kind: BuzzEventKind = BuzzEventKind.MESSAGE,
    approval: BuzzApprovalRef | None = None,
    git: BuzzGitRef | None = None,
) -> BuzzRelayEvent:
    payload = {
        "summary": "Stage 5 governed Buzz relay event.",
        "status": "proposed",
    }

    return BuzzRelayEvent(
        event_id=event_id,
        sequence=sequence,
        emitted_at=NOW,
        kind=kind,
        space=space(),
        actor=actor(),
        thread=thread(),
        correlation_id="corr-buzz-event-005",
        idempotency_key=idempotency_key,
        payload=payload,
        payload_digest=f"sha256:{canonical_digest(payload)}",
        previous_event_digest=previous_event_digest,
        signature=SIGNATURE,
        approval=approval,
        git=git,
        evidence=(evidence(),),
    )


def make_registry_entry(
    *,
    integration_id: str = "buzz-relay",
    category: IntegrationCategory = IntegrationCategory.COLLABORATION,
    lifecycle: LifecycleState = LifecycleState.DISCOVERED,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id=integration_id,
        canonical_project_name="Buzz Relay",
        category=category,
        repository_url="https://github.com/nousresearch/buzz",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="nousresearch/buzz",
        maintainer_identity="nousresearch",
        maturity="developer preview",
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
        declared_execution_model="descriptive relay adapter only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan", "hermes-mac"),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("collaboration_projection",),
        integration_overlap=("hermes-control-plane",),
        known_risks=("message spoofing", "replay attacks"),
        threat_model_references=("docs/threat-models/buzz.md",),
        evaluation_evidence_references=("docs/evidence/buzz.md",),
        rollback_instructions="Remove the disabled relay projection.",
        disable_instructions="Keep the adapter disabled.",
        quarantine_instructions="Reject all Buzz relay events.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 5 contract evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def test_config_is_disabled_and_has_no_authority() -> None:
    config = BuzzRelayConfig()

    assert config.schema_version == BUZZ_RELAY_ADAPTER_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_connect is False
    assert config.can_authenticate is False
    assert config.can_send is False
    assert config.can_subscribe is False
    assert config.can_dispatch is False
    assert config.can_approve is False
    assert config.authority == AuthorityDenials()


def test_config_rejects_worker_schema_mismatch() -> None:
    with pytest.raises(BuzzRelayValidationError, match="incompatible"):
        BuzzRelayConfig(expected_worker_contract_schema=999)


def test_registry_entry_validation_accepts_collaboration_entry() -> None:
    validate_buzz_registry_entry(
        BuzzRelayConfig(),
        make_registry_entry(),
    )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            make_registry_entry(integration_id="paperclip"),
            "identity mismatch",
        ),
        (
            make_registry_entry(
                category=IntegrationCategory.ORGANIZATION
            ),
            "collaboration integration",
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
    with pytest.raises(BuzzRelayValidationError, match=message):
        validate_buzz_registry_entry(
            BuzzRelayConfig(),
            entry,
        )


def test_event_is_immutable_and_deterministic() -> None:
    first = make_event()
    second = make_event()

    assert first == second
    assert first.event_digest == second.event_digest
    assert first.event_digest.startswith("sha256:")
    assert first.can_execute is False
    assert first.can_approve_work is False
    assert first.can_send_message is False


def test_event_rejects_digest_tampering() -> None:
    value = make_event()

    with pytest.raises(BuzzRelayValidationError, match="digest mismatch"):
        replace(value, sequence=9)


def test_event_rejects_payload_digest_mismatch() -> None:
    with pytest.raises(BuzzRelayValidationError, match="payload digest"):
        replace(make_event(), payload_digest=DIGEST, event_digest="")


def test_event_rejects_invalid_signature() -> None:
    with pytest.raises(BuzzRelayValidationError, match="signature"):
        replace(make_event(), signature="bad", event_digest="")


def test_approval_event_requires_approval_reference() -> None:
    with pytest.raises(BuzzRelayValidationError, match="requires"):
        make_event(kind=BuzzEventKind.APPROVAL_REFERENCE)


def test_approval_reference_rejected_on_message_event() -> None:
    approval = BuzzApprovalRef(
        approval_id="approval-005",
        policy_revision="policy-stage5",
        approval_scope=("paper_action",),
        approved=True,
        evidence_digest=DIGEST,
    )

    with pytest.raises(BuzzRelayValidationError, match="only valid"):
        make_event(
            kind=BuzzEventKind.MESSAGE,
            approval=approval,
        )


def test_git_event_requires_git_reference() -> None:
    with pytest.raises(BuzzRelayValidationError, match="requires"):
        make_event(kind=BuzzEventKind.GIT_EVENT)


def test_git_reference_rejects_mutable_revision() -> None:
    with pytest.raises(BuzzRelayValidationError, match="immutable"):
        BuzzGitRef(
            repository_identity="firecattechllc/hermes-agent",
            revision="main",
            event_name="push",
            workflow_reference=None,
        )


def test_evidence_rejects_credentials_before_path_validation() -> None:
    with pytest.raises(BuzzRelayValidationError, match="credential"):
        BuzzEvidenceRef(
            evidence_id="secret-evidence",
            kind="test_result",
            content_digest=DIGEST,
            provenance="api_key=secret-value",
            reference="evidence/result.json",
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/result.json",
        "/home/operator/result.json",
        "../outside.json",
        "evidence/../../outside.json",
        "http://127.0.0.1:3000/result",
    ],
)
def test_evidence_references_reject_private_or_escaping_values(
    bad_reference: str,
) -> None:
    with pytest.raises(BuzzRelayValidationError):
        BuzzEvidenceRef(
            evidence_id="bad-evidence",
            kind="test_result",
            content_digest=DIGEST,
            provenance="test",
            reference=bad_reference,
        )


def test_disabled_adapter_rejects_delivery_without_mutating_window() -> None:
    window = initial_replay_window()
    decision = evaluate_relay_event(
        BuzzRelayConfig(),
        make_event(),
        window,
        age_seconds=1,
    )

    assert decision.state is BuzzDeliveryState.DISABLED
    assert decision.accepted is False
    assert decision.next_window == window


def test_enabled_adapter_accepts_current_event() -> None:
    config = BuzzRelayConfig(enabled=True)
    window = initial_replay_window()
    event = make_event()

    decision = evaluate_relay_event(
        config,
        event,
        window,
        age_seconds=10,
    )

    assert decision.state is BuzzDeliveryState.ACCEPTED
    assert decision.accepted is True
    assert decision.next_window.highest_sequence == 0
    assert decision.next_window.last_event_digest == event.event_digest


def test_duplicate_event_identity_is_rejected() -> None:
    config = BuzzRelayConfig(enabled=True)
    first = make_event()
    accepted = evaluate_relay_event(
        config,
        first,
        initial_replay_window(),
        age_seconds=1,
    )

    duplicate = evaluate_relay_event(
        config,
        first,
        accepted.next_window,
        age_seconds=2,
    )

    assert duplicate.state is BuzzDeliveryState.DUPLICATE
    assert duplicate.accepted is False


def test_duplicate_idempotency_key_is_rejected() -> None:
    config = BuzzRelayConfig(enabled=True)
    first = make_event()
    accepted = evaluate_relay_event(
        config,
        first,
        initial_replay_window(),
        age_seconds=1,
    )

    second = make_event(
        event_id="event-006",
        sequence=1,
        idempotency_key=first.idempotency_key,
        previous_event_digest=first.event_digest,
    )

    duplicate = evaluate_relay_event(
        config,
        second,
        accepted.next_window,
        age_seconds=1,
    )

    assert duplicate.state is BuzzDeliveryState.DUPLICATE


def test_non_increasing_sequence_is_rejected() -> None:
    config = BuzzRelayConfig(enabled=True)
    first = make_event()
    accepted = evaluate_relay_event(
        config,
        first,
        initial_replay_window(),
        age_seconds=1,
    )

    second = make_event(
        event_id="event-006",
        sequence=0,
        idempotency_key="idem-event-006",
        previous_event_digest=first.event_digest,
    )

    duplicate = evaluate_relay_event(
        config,
        second,
        accepted.next_window,
        age_seconds=1,
    )

    assert duplicate.state is BuzzDeliveryState.DUPLICATE


def test_hash_chain_mismatch_fails_closed() -> None:
    config = BuzzRelayConfig(enabled=True)

    with pytest.raises(BuzzRelayValidationError, match="hash chain"):
        evaluate_relay_event(
            config,
            make_event(previous_event_digest=DIGEST),
            initial_replay_window(),
            age_seconds=1,
        )


def test_stale_event_is_not_accepted() -> None:
    decision = evaluate_relay_event(
        BuzzRelayConfig(enabled=True),
        make_event(),
        initial_replay_window(),
        age_seconds=301,
        stale_after_seconds=300,
    )

    assert decision.state is BuzzDeliveryState.STALE
    assert decision.accepted is False


def test_future_event_fails_closed() -> None:
    with pytest.raises(BuzzRelayValidationError, match="future"):
        evaluate_relay_event(
            BuzzRelayConfig(enabled=True),
            make_event(),
            initial_replay_window(),
            age_seconds=-1,
        )


@pytest.mark.parametrize(
    ("job_state", "buzz_state"),
    [
        (JobState.PROPOSED, BuzzWorkState.PROPOSED),
        (JobState.ADMITTED, BuzzWorkState.ADMITTED),
        (JobState.REJECTED, BuzzWorkState.REJECTED),
        (JobState.QUEUED, BuzzWorkState.QUEUED),
        (JobState.RUNNING, BuzzWorkState.RUNNING),
        (
            JobState.CANCELLATION_REQUESTED,
            BuzzWorkState.CANCELLATION_REQUESTED,
        ),
        (JobState.CANCELLED, BuzzWorkState.CANCELLED),
        (JobState.SUCCEEDED, BuzzWorkState.SUCCEEDED),
        (JobState.FAILED, BuzzWorkState.FAILED),
        (
            JobState.COMPLETION_UNKNOWN,
            BuzzWorkState.COMPLETION_UNKNOWN,
        ),
    ],
)
def test_worker_lifecycle_projection(
    job_state: JobState,
    buzz_state: BuzzWorkState,
) -> None:
    projection = project_worker_job(
        BuzzRelayConfig(),
        make_job(state=job_state),
    )

    assert projection.state is buzz_state
    assert lifecycle_projection()[job_state] is buzz_state


def test_projection_preserves_job_identity() -> None:
    job = make_job()
    projection = project_worker_job(BuzzRelayConfig(), job)

    assert projection.job_id == job.job_id
    assert projection.correlation_id == job.correlation_id
    assert projection.idempotency_key == job.idempotency_key
    assert projection.worker_contract_digest == job.contract_digest


def test_projection_rejects_wrong_integration() -> None:
    with pytest.raises(BuzzRelayValidationError, match="does not match"):
        project_worker_job(
            BuzzRelayConfig(),
            make_job(integration_id="paperclip"),
        )


def test_duplicate_evidence_fails_closed() -> None:
    duplicate = evidence()

    with pytest.raises(BuzzRelayValidationError, match="duplicate"):
        replace(
            make_event(),
            evidence=(duplicate, duplicate),
            event_digest="",
        )
