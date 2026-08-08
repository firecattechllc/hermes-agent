from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai.registry import canonical_digest
from sigil.fleet_routing import (
    FLEET_ROUTING_SCHEMA_VERSION,
    FleetCapacity,
    FleetEvidence,
    FleetHealthState,
    FleetLeaseState,
    FleetNode,
    FleetNodeRole,
    FleetRoutingConfig,
    FleetRoutingValidationError,
    FleetTrustTier,
    RouteEligibility,
    RoutingRequirements,
    evaluate_candidate,
    route_worker_job,
)
from sigil.integration_registry import AuthorityDenials
from sigil.worker_contract import (
    ApprovalRequirements,
    EvidenceRequirements,
    GovernedWorkerJob,
    JobBudget,
)


NOW = "2026-08-02T00:40:00Z"
LATER = "2026-08-02T01:40:00Z"
DIGEST = "sha256:" + "b" * 64


def job(
    *,
    capability: str = "research_summary",
    target_machine: str = "hermes-titan",
    target_profile: str = "governed-worker",
) -> GovernedWorkerJob:
    payload = {
        "topic": "Stage 10 routing",
        "mode": "descriptive_only",
    }

    return GovernedWorkerJob(
        job_id="job-routing-stage10",
        correlation_id="corr-routing-stage10",
        idempotency_key="idem-routing-stage10",
        integration_id="fleet-routing",
        requested_capability=capability,
        requesting_actor_identity="hermes-control-plane",
        target_machine=target_machine,
        target_profile=target_profile,
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
            required_kinds=("fleet_health",),
            require_content_digests=True,
            require_provenance=True,
        ),
        approval_requirements=ApprovalRequirements(
            required=False,
            policy_revision="fleet-routing-stage10",
            approval_scope=(),
            minimum_independent_approvers=0,
        ),
    )


def capacity(
    *,
    slots: int = 4,
    memory: int = 8192,
    compute: int = 100,
) -> FleetCapacity:
    return FleetCapacity(
        total_job_slots=8,
        available_job_slots=slots,
        total_memory_megabytes=16384,
        available_memory_megabytes=memory,
        total_compute_units=200,
        available_compute_units=compute,
    )


def node(
    node_id: str,
    *,
    role: FleetNodeRole,
    priority: int,
    trust: FleetTrustTier = FleetTrustTier.CERTIFIED,
    capability: str = "research_summary",
    machines: tuple[str, ...] = ("hermes-titan",),
    profiles: tuple[str, ...] = ("governed-worker",),
    cost: str = "0.10",
    enabled: bool = True,
    worker_schema: int = 1,
) -> FleetNode:
    return FleetNode(
        node_id=node_id,
        machine_id=f"machine-{node_id}",
        display_name=node_id.replace("-", " ").title(),
        role=role,
        priority=priority,
        trust_tier=trust,
        worker_contract_schema=worker_schema,
        capabilities=(capability,),
        supported_machines=machines,
        supported_profiles=profiles,
        cost_per_hour_usd=cost,
        enabled=enabled,
    )


def evidence(
    node_id: str,
    *,
    health: FleetHealthState = FleetHealthState.HEALTHY,
    lease: FleetLeaseState = FleetLeaseState.AVAILABLE,
    capacity_value: FleetCapacity | None = None,
    running_jobs: int = 0,
    failures: int = 0,
    latency: int = 10,
) -> FleetEvidence:
    return FleetEvidence(
        node_id=node_id,
        observed_at=NOW,
        health=health,
        lease_state=lease,
        capacity=capacity() if capacity_value is None else capacity_value,
        running_jobs=running_jobs,
        recent_failures=failures,
        latency_milliseconds=latency,
        evidence_digest=DIGEST,
        sanitized_summary="Injected governed fleet evidence.",
    )


def requirements(
    *,
    memory: int = 1024,
    compute: int = 10,
    maximum_cost: str = "1.00",
) -> RoutingRequirements:
    return RoutingRequirements(
        minimum_memory_megabytes=memory,
        minimum_compute_units=compute,
        required_evidence_kinds=("fleet_health",),
        maximum_hourly_cost_usd=maximum_cost,
    )


def titan() -> FleetNode:
    return node(
        "hermes-titan",
        role=FleetNodeRole.PRIMARY,
        priority=100,
        machines=("hermes-titan",),
    )


def mac() -> FleetNode:
    return node(
        "hermes-mac",
        role=FleetNodeRole.SENIOR,
        priority=90,
        machines=("hermes-titan",),
        cost="0.25",
    )


def buzznode() -> FleetNode:
    return node(
        "buzznode-001",
        role=FleetNodeRole.PERSISTENT_WORKER,
        priority=60,
        machines=("hermes-titan",),
        cost="0.05",
    )


def test_config_is_disabled_and_non_dispatching() -> None:
    config = FleetRoutingConfig()

    assert config.schema_version == FLEET_ROUTING_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_dispatch is False
    assert config.can_provision is False
    assert config.can_connect is False
    assert config.can_ssh is False
    assert config.can_execute_shell is False
    assert config.can_use_credentials is False
    assert config.can_activate_integration is False
    assert config.authority == AuthorityDenials()


def test_node_is_immutable_and_deterministic() -> None:
    first = titan()
    second = titan()

    assert first == second
    assert first.node_digest == second.node_digest
    assert first.node_digest.startswith("sha256:")
    assert first.can_dispatch is False
    assert first.can_execute is False


def test_node_rejects_digest_tampering() -> None:
    with pytest.raises(
        FleetRoutingValidationError,
        match="digest mismatch",
    ):
        replace(titan(), priority=999)


def test_disabled_routing_excludes_candidate() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(),
        job(),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert candidate.eligibility is RouteEligibility.DISABLED
    assert candidate.eligible is False
    assert candidate.can_dispatch is False


def test_healthy_titan_is_eligible() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert candidate.eligibility is RouteEligibility.ELIGIBLE
    assert candidate.eligible is True
    assert candidate.score > 0


def test_capability_mismatch_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(capability="browser_automation"),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.CAPABILITY_MISMATCH
    )


def test_machine_mismatch_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(target_machine="hermes-mac"),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.MACHINE_MISMATCH
    )


def test_profile_mismatch_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(target_profile="unapproved-profile"),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.PROFILE_MISMATCH
    )


def test_stale_evidence_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        titan(),
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=121,
        stale_after_seconds=120,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.STALE_EVIDENCE
    )


def test_offline_health_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        titan(),
        evidence(
            "hermes-titan",
            health=FleetHealthState.OFFLINE,
        ),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.HEALTH_BLOCKED
    )


def test_expired_lease_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        titan(),
        evidence(
            "hermes-titan",
            lease=FleetLeaseState.EXPIRED,
        ),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.LEASE_BLOCKED
    )


def test_capacity_shortage_is_excluded() -> None:
    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        titan(),
        evidence(
            "hermes-titan",
            capacity_value=capacity(
                slots=0,
                memory=0,
                compute=0,
            ),
        ),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.CAPACITY_BLOCKED
    )


def test_budget_overage_is_excluded() -> None:
    expensive = replace(
        titan(),
        cost_per_hour_usd="2.00",
        node_digest="",
    )

    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        expensive,
        evidence("hermes-titan"),
        requirements(maximum_cost="1.00"),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.BUDGET_BLOCKED
    )


def test_low_trust_is_excluded() -> None:
    untrusted = replace(
        titan(),
        trust_tier=FleetTrustTier.OBSERVED,
        node_digest="",
    )

    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        untrusted,
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.TRUST_BLOCKED
    )


def test_worker_schema_mismatch_is_excluded() -> None:
    incompatible = replace(
        titan(),
        worker_contract_schema=999,
        node_digest="",
    )

    candidate = evaluate_candidate(
        FleetRoutingConfig(enabled=True),
        job(),
        incompatible,
        evidence("hermes-titan"),
        requirements(),
        evidence_age_seconds=10,
    )

    assert (
        candidate.eligibility
        is RouteEligibility.SCHEMA_INCOMPATIBLE
    )


def test_future_evidence_fails_closed() -> None:
    with pytest.raises(
        FleetRoutingValidationError,
        match="future",
    ):
        evaluate_candidate(
            FleetRoutingConfig(enabled=True),
            job(),
            titan(),
            evidence("hermes-titan"),
            requirements(),
            evidence_age_seconds=-1,
        )


def test_mismatched_evidence_node_fails_closed() -> None:
    with pytest.raises(
        FleetRoutingValidationError,
        match="does not match",
    ):
        evaluate_candidate(
            FleetRoutingConfig(enabled=True),
            job(),
            titan(),
            evidence("different-node"),
            requirements(),
            evidence_age_seconds=1,
        )


def test_titan_primary_mac_fallback_buzznode_second_fallback() -> None:
    nodes = (buzznode(), mac(), titan())
    evidence_by_node = {
        "hermes-titan": evidence("hermes-titan"),
        "hermes-mac": evidence("hermes-mac"),
        "buzznode-001": evidence("buzznode-001"),
    }

    decision = route_worker_job(
        FleetRoutingConfig(
            enabled=True,
            maximum_fallbacks=2,
        ),
        job(),
        nodes,
        evidence_by_node,
        requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
            "hermes-mac": 10,
            "buzznode-001": 10,
        },
    )

    assert decision.primary_node_id == "hermes-titan"
    assert decision.fallback_node_ids == (
        "hermes-mac",
        "buzznode-001",
    )
    assert decision.can_dispatch is False
    assert decision.can_failover is False


def test_busy_titan_can_lose_to_healthy_mac() -> None:
    decision = route_worker_job(
        FleetRoutingConfig(enabled=True),
        job(),
        (titan(), mac()),
        {
            "hermes-titan": evidence(
                "hermes-titan",
                health=FleetHealthState.BUSY,
                running_jobs=10,
                failures=4,
                latency=5000,
            ),
            "hermes-mac": evidence("hermes-mac"),
        },
        requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
            "hermes-mac": 10,
        },
    )

    assert decision.primary_node_id == "hermes-mac"
    assert decision.fallback_node_ids == ("hermes-titan",)


def test_ineligible_buzznode_preserves_reason() -> None:
    bad_buzznode = replace(
        buzznode(),
        capabilities=("browser_automation",),
        node_digest="",
    )

    decision = route_worker_job(
        FleetRoutingConfig(enabled=True),
        job(),
        (titan(), bad_buzznode),
        {
            "hermes-titan": evidence("hermes-titan"),
            "buzznode-001": evidence("buzznode-001"),
        },
        requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
            "buzznode-001": 10,
        },
    )

    excluded = next(
        item
        for item in decision.candidates
        if item.node_id == "buzznode-001"
    )

    assert excluded.eligible is False
    assert (
        excluded.eligibility
        is RouteEligibility.CAPABILITY_MISMATCH
    )
    assert excluded.exclusion_reasons


def test_no_eligible_node_returns_no_primary() -> None:
    decision = route_worker_job(
        FleetRoutingConfig(enabled=True),
        job(),
        (titan(),),
        {
            "hermes-titan": evidence(
                "hermes-titan",
                health=FleetHealthState.OFFLINE,
            )
        },
        requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
        },
    )

    assert decision.primary_node_id is None
    assert decision.fallback_node_ids == ()
    assert "No governed fleet node" in decision.decision_reason


def test_missing_evidence_fails_closed() -> None:
    with pytest.raises(
        FleetRoutingValidationError,
        match="missing fleet evidence",
    ):
        route_worker_job(
            FleetRoutingConfig(enabled=True),
            job(),
            (titan(),),
            {},
            requirements(),
            evidence_age_seconds_by_node={},
        )


def test_duplicate_node_identity_fails_closed() -> None:
    with pytest.raises(
        FleetRoutingValidationError,
        match="duplicate fleet node",
    ):
        route_worker_job(
            FleetRoutingConfig(enabled=True),
            job(),
            (titan(), titan()),
            {
                "hermes-titan": evidence("hermes-titan"),
            },
            requirements(),
            evidence_age_seconds_by_node={
                "hermes-titan": 10,
            },
        )


def test_tie_breaking_is_deterministic_by_node_id() -> None:
    first = node(
        "worker-a",
        role=FleetNodeRole.PERSISTENT_WORKER,
        priority=50,
        machines=("hermes-titan",),
    )
    second = node(
        "worker-b",
        role=FleetNodeRole.PERSISTENT_WORKER,
        priority=50,
        machines=("hermes-titan",),
    )

    decision = route_worker_job(
        FleetRoutingConfig(enabled=True),
        job(),
        (second, first),
        {
            "worker-a": evidence("worker-a"),
            "worker-b": evidence("worker-b"),
        },
        requirements(),
        evidence_age_seconds_by_node={
            "worker-a": 10,
            "worker-b": 10,
        },
    )

    assert decision.primary_node_id == "worker-a"
    assert decision.fallback_node_ids == ("worker-b",)


def test_route_decision_is_deterministic() -> None:
    arguments = dict(
        config=FleetRoutingConfig(enabled=True),
        job=job(),
        nodes=(titan(), mac(), buzznode()),
        evidence_by_node={
            "hermes-titan": evidence("hermes-titan"),
            "hermes-mac": evidence("hermes-mac"),
            "buzznode-001": evidence("buzznode-001"),
        },
        requirements=requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
            "hermes-mac": 10,
            "buzznode-001": 10,
        },
    )

    first = route_worker_job(**arguments)
    second = route_worker_job(**arguments)

    assert first == second
    assert first.decision_digest == second.decision_digest
    assert first.decision_digest.startswith("sha256:")


def test_route_decision_rejects_digest_tampering() -> None:
    value = route_worker_job(
        FleetRoutingConfig(enabled=True),
        job(),
        (titan(),),
        {
            "hermes-titan": evidence("hermes-titan"),
        },
        requirements(),
        evidence_age_seconds_by_node={
            "hermes-titan": 10,
        },
    )

    with pytest.raises(
        FleetRoutingValidationError,
        match="digest mismatch",
    ):
        replace(value, decision_reason="Changed")
