from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.agent_reach_adapter import (
    AGENT_REACH_ADAPTER_SCHEMA_VERSION,
    AgentReachConfig,
    AgentReachState,
    AgentReachValidationError,
    AgentReachWorkState,
    AgentTrustTier,
    ReachAgentIdentity,
    ReachEvidenceRef,
    ReachEvidenceRequirement,
    ReachHeartbeat,
    ReachLimits,
    ReachRequestEnvelope,
    ReachResponseEnvelope,
    ReachResponseState,
    ReachRouteRef,
    evaluate_agent_reach,
    lifecycle_projection,
    project_worker_job,
    validate_agent_reach_registry_entry,
)
from sigil.ai.registry import canonical_digest
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


NOW = "2026-08-02T00:20:00Z"
LATER = "2026-08-02T01:20:00Z"
REVISION = "a" * 40
DIGEST = "sha256:" + "b" * 64


def identity(
    *,
    trust_tier: AgentTrustTier = AgentTrustTier.REVIEWED,
    worker_contract_schema: int = 1,
) -> ReachAgentIdentity:
    return ReachAgentIdentity(
        agent_id="external-agent-001",
        organization_identity="approved-external-org",
        display_name="External Research Agent",
        trust_tier=trust_tier,
        worker_contract_schema=worker_contract_schema,
        capabilities=("research_summary", "evidence_collection"),
        supported_machines=("hermes-titan",),
        supported_profiles=("governed-worker",),
    )


def limits() -> ReachLimits:
    return ReachLimits(
        maximum_requests_per_hour=10,
        maximum_in_flight_requests=2,
        maximum_request_bytes=100000,
        maximum_response_bytes=100000,
        maximum_runtime_seconds=300,
        maximum_cost_usd="2.00",
    )


def evidence_requirements() -> ReachEvidenceRequirement:
    return ReachEvidenceRequirement(
        minimum_references=1,
        required_kinds=("source_reference",),
        require_content_digests=True,
        require_provenance=True,
    )


def request(
    *,
    requested_capability: str = "research_summary",
    target_machine: str = "hermes-titan",
    target_profile: str = "governed-worker",
) -> ReachRequestEnvelope:
    payload = {
        "topic": "Stage 8A Agent Reach",
        "mode": "descriptive_only",
    }

    return ReachRequestEnvelope(
        request_id="reach-request-001",
        correlation_id="corr-reach-001",
        idempotency_key="idem-reach-001",
        requesting_actor_identity="hermes-control-plane",
        target_agent_id="external-agent-001",
        requested_capability=requested_capability,
        target_machine=target_machine,
        target_profile=target_profile,
        created_at=NOW,
        deadline_at=LATER,
        payload=payload,
        payload_digest=f"sha256:{canonical_digest(payload)}",
        limits=limits(),
        evidence_requirements=evidence_requirements(),
    )


def evidence() -> ReachEvidenceRef:
    return ReachEvidenceRef(
        evidence_id="reach-evidence-001",
        kind="source_reference",
        content_digest=DIGEST,
        provenance="governed external evidence projection",
        reference="evidence/agent-reach/source-001.json",
    )


def response(
    *,
    responding_agent_id: str = "external-agent-001",
    cost_usd: str = "1.00",
    runtime_seconds: int = 100,
    response_bytes: int = 1000,
    evidence_items: tuple[ReachEvidenceRef, ...] | None = None,
) -> ReachResponseEnvelope:
    output = {
        "summary": "Projected external agent response.",
        "execution_performed": False,
    }

    return ReachResponseEnvelope(
        response_id="reach-response-001",
        request_id="reach-request-001",
        correlation_id="corr-reach-001",
        responding_agent_id=responding_agent_id,
        state=ReachResponseState.SUCCEEDED,
        completed_at=LATER,
        output_payload=output,
        output_digest=f"sha256:{canonical_digest(output)}",
        evidence=(evidence(),) if evidence_items is None else evidence_items,
        runtime_seconds=runtime_seconds,
        response_bytes=response_bytes,
        cost_usd=cost_usd,
    )


def heartbeat(
    *,
    agent_id: str = "external-agent-001",
    online: bool = True,
    worker_contract_schema: int = 1,
    active_requests: int = 0,
    requests_last_hour: int = 0,
) -> ReachHeartbeat:
    return ReachHeartbeat(
        agent_id=agent_id,
        observed_at=NOW,
        sequence=1,
        online=online,
        worker_contract_schema=worker_contract_schema,
        active_requests=active_requests,
        requests_last_hour=requests_last_hour,
        sanitized_summary="Projected Agent Reach heartbeat.",
    )


def make_job(
    *,
    state: JobState = JobState.PROPOSED,
    integration_id: str = "agent-reach",
) -> GovernedWorkerJob:
    payload = {
        "target_agent_id": "external-agent-001",
        "capability": "research_summary",
    }

    return GovernedWorkerJob(
        job_id="job-agent-reach-08a",
        correlation_id="corr-agent-reach-08a",
        idempotency_key="idem-agent-reach-08a",
        integration_id=integration_id,
        requested_capability="research_summary",
        requesting_actor_identity="hermes-control-plane",
        target_machine="hermes-titan",
        target_profile="governed-worker",
        created_at=NOW,
        deadline_at=LATER,
        input_payload=payload,
        input_digest=f"sha256:{canonical_digest(payload)}",
        budget=JobBudget(
            maximum_cost_usd="2.00",
            maximum_runtime_seconds=300,
            maximum_attempts=2,
            maximum_input_bytes=100000,
            maximum_output_bytes=100000,
        ),
        evidence_requirements=EvidenceRequirements(
            required=True,
            minimum_references=1,
            required_kinds=("source_reference",),
            require_content_digests=True,
            require_provenance=True,
        ),
        approval_requirements=ApprovalRequirements(
            required=False,
            policy_revision="agent-reach-stage8a",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
        state=state,
    )


def registry_entry(
    *,
    integration_id: str = "agent-reach",
    category: IntegrationCategory = (
        IntegrationCategory.INTERNET_CAPABILITY
    ),
    lifecycle: LifecycleState = LifecycleState.DISCOVERED,
) -> IntegrationRegistryEntry:
    return IntegrationRegistryEntry(
        integration_id=integration_id,
        canonical_project_name="Agent Reach",
        category=category,
        repository_url="https://github.com/example/agent-reach",
        pinned_identity=REVISION,
        release_label=None,
        upstream_repository_identity="example/agent-reach",
        maintainer_identity="example",
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
        declared_execution_model="descriptive reachability adapter only",
        declared_external_data_transmission=(),
        install_mechanism="not installed",
        dependency_summary=(),
        supported_machines=("hermes-titan",),
        approved_machines=(),
        supported_profiles=("governed-worker",),
        approved_profiles=(),
        capabilities=("research_summary",),
        integration_overlap=("hermes-control-plane",),
        known_risks=("external-agent-trust",),
        threat_model_references=(
            "docs/threat-models/agent-reach.md",
        ),
        evaluation_evidence_references=(
            "docs/evidence/agent-reach.md",
        ),
        rollback_instructions="Remove the reachability projection.",
        disable_instructions="Keep Agent Reach disabled.",
        quarantine_instructions="Reject all Agent Reach envelopes.",
        lifecycle_state=lifecycle,
        lifecycle_reason="Stage 8A contract evaluation only.",
        created_at=NOW,
        observed_at=NOW,
    )


def test_config_is_disabled_and_has_no_authority() -> None:
    config = AgentReachConfig()

    assert config.schema_version == AGENT_REACH_ADAPTER_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_connect is False
    assert config.can_authenticate is False
    assert config.can_exchange_credentials is False
    assert config.can_send_message is False
    assert config.can_dispatch is False
    assert config.can_execute is False
    assert config.can_approve is False
    assert config.authority == AuthorityDenials()


def test_config_rejects_worker_schema_mismatch() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="incompatible",
    ):
        AgentReachConfig(expected_worker_contract_schema=999)


def test_registry_entry_accepts_internet_capability() -> None:
    validate_agent_reach_registry_entry(
        AgentReachConfig(),
        registry_entry(),
    )


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            registry_entry(integration_id="different"),
            "identity mismatch",
        ),
        (
            registry_entry(category=IntegrationCategory.WORKER),
            "internet capability",
        ),
        (
            registry_entry(lifecycle=LifecycleState.QUARANTINED),
            "not eligible",
        ),
    ],
)
def test_registry_entry_fails_closed(
    entry: IntegrationRegistryEntry,
    message: str,
) -> None:
    with pytest.raises(
        AgentReachValidationError,
        match=message,
    ):
        validate_agent_reach_registry_entry(
            AgentReachConfig(),
            entry,
        )


def test_identity_is_immutable_and_deterministic() -> None:
    first = identity()
    second = identity()

    assert first == second
    assert first.identity_digest == second.identity_digest
    assert first.identity_digest.startswith("sha256:")
    assert first.can_execute is False
    assert first.can_approve is False


def test_identity_rejects_digest_tampering() -> None:
    value = identity()

    with pytest.raises(
        AgentReachValidationError,
        match="digest mismatch",
    ):
        replace(value, display_name="Changed")


def test_route_rejects_authentication_and_credentials() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="authentication",
    ):
        ReachRouteRef(
            route_id="route-001",
            transport_classification="relay",
            route_reference="routes/agent-001.json",
            one_way=True,
            authenticated=True,
        )

    with pytest.raises(
        AgentReachValidationError,
        match="credential exchange",
    ):
        ReachRouteRef(
            route_id="route-001",
            transport_classification="relay",
            route_reference="routes/agent-001.json",
            one_way=True,
            credential_exchange_required=True,
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/route.json",
        "/home/operator/route.json",
        "../outside.json",
        "routes/../../outside.json",
        "http://127.0.0.1:3000/reach",
    ],
)
def test_route_references_fail_closed(
    bad_reference: str,
) -> None:
    with pytest.raises(AgentReachValidationError):
        ReachRouteRef(
            route_id="route-001",
            transport_classification="relay",
            route_reference=bad_reference,
            one_way=True,
        )


def test_request_is_immutable_and_deterministic() -> None:
    first = request()
    second = request()

    assert first == second
    assert first.request_digest == second.request_digest
    assert first.can_send is False
    assert first.can_execute is False


def test_request_rejects_payload_tampering() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="payload digest",
    ):
        replace(
            request(),
            payload={"changed": True},
            request_digest="",
        )


def test_evidence_rejects_credentials() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="credential",
    ):
        ReachEvidenceRef(
            evidence_id="secret-evidence",
            kind="source_reference",
            content_digest=DIGEST,
            provenance="api_key=secret-value",
            reference="evidence/source.json",
        )


def test_response_is_immutable_and_deterministic() -> None:
    first = response()
    second = response()

    assert first == second
    assert first.response_digest == second.response_digest


def test_response_rejects_output_tampering() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="output digest",
    ):
        replace(
            response(),
            output_payload={"changed": True},
            response_digest="",
        )


def test_response_validates_against_request_and_identity() -> None:
    response().validate_for(request(), identity())


def test_response_rejects_wrong_agent() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="response agent",
    ):
        response(
            responding_agent_id="different-agent"
        ).validate_for(request(), identity())


def test_response_rejects_missing_required_evidence() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="insufficient evidence",
    ):
        response(evidence_items=()).validate_for(
            request(),
            identity(),
        )


def test_response_rejects_runtime_overage() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="runtime exceeds",
    ):
        response(runtime_seconds=301).validate_for(
            request(),
            identity(),
        )


def test_response_rejects_byte_overage() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="bytes exceed",
    ):
        response(response_bytes=100001).validate_for(
            request(),
            identity(),
        )


def test_response_rejects_budget_overage() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="cost exceeds",
    ):
        response(cost_usd="2.01").validate_for(
            request(),
            identity(),
        )


def test_disabled_assessment_fails_closed() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(),
        identity(),
        request(),
        heartbeat(),
        heartbeat_age_seconds=1,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.DISABLED
    assert assessment.enabled is False
    assert assessment.can_reach is False
    assert assessment.can_dispatch is False


def test_current_compatible_agent_is_available() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.AVAILABLE
    assert assessment.trust_sufficient is True
    assert assessment.worker_contract_compatible is True
    assert assessment.heartbeat_current is True
    assert assessment.capability_allowed is True
    assert assessment.machine_supported is True
    assert assessment.profile_supported is True
    assert assessment.rate_available is True
    assert assessment.budget_available is True


def test_low_trust_agent_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(trust_tier=AgentTrustTier.OBSERVED),
        request(),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.TRUST_BLOCKED


def test_incompatible_agent_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(worker_contract_schema=999),
        request(),
        heartbeat(worker_contract_schema=999),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.INCOMPATIBLE


def test_unapproved_capability_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(requested_capability="arbitrary_execution"),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.CAPABILITY_BLOCKED
    assert assessment.capability_allowed is False


def test_unsupported_machine_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(target_machine="unknown-machine"),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.CAPABILITY_BLOCKED
    assert assessment.machine_supported is False


def test_rate_limit_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=10,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.RATE_BLOCKED
    assert assessment.rate_available is False


def test_budget_limit_is_blocked() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        heartbeat(),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="2.00",
    )

    assert assessment.state is AgentReachState.BUDGET_BLOCKED
    assert assessment.budget_available is False


def test_missing_heartbeat_is_stale() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        None,
        heartbeat_age_seconds=None,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.STALE


def test_stale_heartbeat_is_stale() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        heartbeat(),
        heartbeat_age_seconds=121,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
        stale_after_seconds=120,
    )

    assert assessment.state is AgentReachState.STALE


def test_offline_heartbeat_is_offline() -> None:
    assessment = evaluate_agent_reach(
        AgentReachConfig(enabled=True),
        identity(),
        request(),
        heartbeat(online=False),
        heartbeat_age_seconds=10,
        current_hourly_requests=0,
        current_in_flight_requests=0,
        current_cost_usd="0",
    )

    assert assessment.state is AgentReachState.OFFLINE


def test_future_heartbeat_fails_closed() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="future",
    ):
        evaluate_agent_reach(
            AgentReachConfig(enabled=True),
            identity(),
            request(),
            heartbeat(),
            heartbeat_age_seconds=-1,
            current_hourly_requests=0,
            current_in_flight_requests=0,
            current_cost_usd="0",
        )


def test_mismatched_heartbeat_agent_fails_closed() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="does not match",
    ):
        evaluate_agent_reach(
            AgentReachConfig(enabled=True),
            identity(),
            request(),
            heartbeat(agent_id="different-agent"),
            heartbeat_age_seconds=1,
            current_hourly_requests=0,
            current_in_flight_requests=0,
            current_cost_usd="0",
        )


@pytest.mark.parametrize(
    ("job_state", "reach_state"),
    [
        (JobState.PROPOSED, AgentReachWorkState.PROPOSED),
        (JobState.ADMITTED, AgentReachWorkState.ADMITTED),
        (JobState.REJECTED, AgentReachWorkState.REJECTED),
        (JobState.QUEUED, AgentReachWorkState.QUEUED),
        (JobState.RUNNING, AgentReachWorkState.RUNNING),
        (
            JobState.CANCELLATION_REQUESTED,
            AgentReachWorkState.CANCELLATION_REQUESTED,
        ),
        (JobState.CANCELLED, AgentReachWorkState.CANCELLED),
        (JobState.SUCCEEDED, AgentReachWorkState.SUCCEEDED),
        (JobState.FAILED, AgentReachWorkState.FAILED),
        (
            JobState.COMPLETION_UNKNOWN,
            AgentReachWorkState.COMPLETION_UNKNOWN,
        ),
    ],
)
def test_worker_lifecycle_projection(
    job_state: JobState,
    reach_state: AgentReachWorkState,
) -> None:
    result = project_worker_job(
        AgentReachConfig(),
        make_job(state=job_state),
    )

    assert result.state is reach_state
    assert lifecycle_projection()[job_state] is reach_state


def test_projection_preserves_worker_identity() -> None:
    job = make_job()
    result = project_worker_job(AgentReachConfig(), job)

    assert result.job_id == job.job_id
    assert result.correlation_id == job.correlation_id
    assert result.idempotency_key == job.idempotency_key
    assert result.requested_capability == job.requested_capability
    assert result.target_machine == job.target_machine
    assert result.target_profile == job.target_profile
    assert result.worker_contract_digest == job.contract_digest


def test_projection_rejects_wrong_integration() -> None:
    with pytest.raises(
        AgentReachValidationError,
        match="does not match",
    ):
        project_worker_job(
            AgentReachConfig(),
            make_job(integration_id="ecosystem-catalog"),
        )
