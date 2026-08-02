from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.integration_registry import AuthorityDenials
from sigil.self_evolution import (
    SELF_EVOLUTION_SCHEMA_VERSION,
    EvolutionBudget,
    EvolutionEvidenceRef,
    EvolutionFrameworkConfig,
    ExperimentOutcome,
    ExperimentPlan,
    ExperimentResult,
    ImprovementCategory,
    ImprovementOpportunity,
    ImprovementProposal,
    IndependentReview,
    PromotionReadiness,
    ProposalState,
    ReviewDecision,
    RiskAssessment,
    RiskLevel,
    RollbackPlan,
    SelfEvolutionValidationError,
    assess_promotion_readiness,
    create_lifecycle_event,
    transition_proposal,
    validate_proposal_transition,
)


NOW = "2026-08-02T00:30:00Z"
LATER = "2026-08-02T00:35:00Z"
DIGEST = "sha256:" + "b" * 64


def evidence(
    *,
    evidence_id: str = "evidence-stage9",
    kind: str = "metric_observation",
) -> EvolutionEvidenceRef:
    return EvolutionEvidenceRef(
        evidence_id=evidence_id,
        kind=kind,
        content_digest=DIGEST,
        provenance="Stage 9 governed observation",
        observed_at=NOW,
        reference=f"evidence/{evidence_id}.json",
    )


def opportunity() -> ImprovementOpportunity:
    return ImprovementOpportunity(
        opportunity_id="opportunity-stage9",
        category=ImprovementCategory.RELIABILITY,
        title="Reduce stale integration projections",
        problem_statement=(
            "Injected health evidence may remain stale longer than desired."
        ),
        affected_components=("integration-health", "mission-control"),
        affected_integrations=("buzznode", "hermes-wiki"),
        observed_at=NOW,
        evidence=(evidence(),),
    )


def budget() -> EvolutionBudget:
    return EvolutionBudget(
        maximum_cost_usd="5.00",
        maximum_runtime_seconds=3600,
        maximum_attempts=3,
        maximum_compute_units=100,
        maximum_input_bytes=100000,
        maximum_output_bytes=100000,
    )


def risk(
    *,
    level: RiskLevel = RiskLevel.MODERATE,
) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        risk_factors=("incorrect freshness classification",),
        blast_radius=("integration-health",),
        mitigations=("isolated evaluation", "full regression suite"),
        requires_security_review=level in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        },
        requires_financial_review=False,
    )


def experiment() -> ExperimentPlan:
    return ExperimentPlan(
        experiment_id="experiment-stage9",
        hypothesis=(
            "A narrower freshness policy reduces stale projections "
            "without increasing false availability."
        ),
        control_description="Existing freshness evaluation.",
        treatment_description="Proposed bounded freshness evaluation.",
        success_metrics=("stale-detection-accuracy",),
        guardrail_metrics=("false-availability-rate",),
        required_tests=(
            "focused-self-evolution-tests",
            "full-sigil-python-suite",
        ),
        certification_requirements=(
            "ruff",
            "git-diff-check",
            "full-python-suite",
        ),
        budget=budget(),
        isolated=True,
        paper_only=True,
    )


def rollback() -> RollbackPlan:
    return RollbackPlan(
        rollback_id="rollback-stage9",
        trigger_conditions=(
            "focused test regression",
            "availability false-positive",
        ),
        rollback_steps=(
            "discard proposed patch",
            "restore previous policy projection",
        ),
        verification_tests=("full-sigil-python-suite",),
        maximum_recovery_seconds=3600,
    )


def proposal(
    *,
    state: ProposalState = ProposalState.DRAFT,
    risk_value: RiskAssessment | None = None,
    minimum_reviews: int = 1,
) -> ImprovementProposal:
    source = opportunity()

    return ImprovementProposal(
        proposal_id="proposal-stage9",
        opportunity_id=source.opportunity_id,
        opportunity_digest=source.opportunity_digest,
        title="Bound integration freshness policy",
        summary=(
            "Evaluate a bounded freshness-policy adjustment in isolation."
        ),
        expected_benefits=(
            "faster stale-state detection",
            "clearer operator evidence",
        ),
        affected_components=source.affected_components,
        affected_integrations=source.affected_integrations,
        risk=risk() if risk_value is None else risk_value,
        experiment=experiment(),
        rollback=rollback(),
        minimum_independent_reviews=minimum_reviews,
        created_at=NOW,
        created_by="hermes-control-plane",
        state=state,
    )


def review(
    *,
    reviewer: str = "independent-reviewer",
    decision: ReviewDecision = ReviewDecision.APPROVED,
) -> IndependentReview:
    return IndependentReview(
        review_id=f"review-{reviewer}",
        reviewer_identity=reviewer,
        reviewed_at=LATER,
        decision=decision,
        scope=("risk", "experiment", "rollback"),
        evidence_digest=DIGEST,
        comments_reference=f"reviews/{reviewer}.md",
    )


def result(
    *,
    proposal_value: ImprovementProposal | None = None,
    outcome: ExperimentOutcome = ExperimentOutcome.PASSED,
    failed_tests: tuple[str, ...] = (),
    regression_evidence: tuple[EvolutionEvidenceRef, ...] = (),
    cost_usd: str = "1.00",
) -> ExperimentResult:
    value = proposal() if proposal_value is None else proposal_value

    return ExperimentResult(
        result_id="result-stage9",
        proposal_id=value.proposal_id,
        proposal_digest=value.proposal_digest,
        outcome=outcome,
        recorded_at=LATER,
        metrics={
            "stale-detection-accuracy": "0.99",
            "false-availability-rate": "0.00",
        },
        passed_tests=(
            "focused-self-evolution-tests",
            "full-sigil-python-suite",
        ),
        failed_tests=failed_tests,
        regression_evidence=regression_evidence,
        runtime_seconds=100,
        attempt_count=1,
        compute_units=10,
        input_bytes=1000,
        output_bytes=1000,
        cost_usd=cost_usd,
    )


def certification_results(
    *,
    complete: bool = True,
) -> dict[str, bool]:
    return {
        "ruff": True,
        "git-diff-check": True,
        "full-python-suite": complete,
    }


def test_framework_is_disabled_and_non_executing() -> None:
    config = EvolutionFrameworkConfig()

    assert config.schema_version == SELF_EVOLUTION_SCHEMA_VERSION
    assert config.enabled is False
    assert config.can_modify_source is False
    assert config.can_execute_experiment is False
    assert config.can_commit is False
    assert config.can_push is False
    assert config.can_open_pull_request is False
    assert config.can_install_dependencies is False
    assert config.can_mutate_policy is False
    assert config.can_promote is False
    assert config.authority == AuthorityDenials()


def test_opportunity_is_immutable_and_deterministic() -> None:
    first = opportunity()
    second = opportunity()

    assert first == second
    assert first.opportunity_digest == second.opportunity_digest
    assert first.opportunity_digest.startswith("sha256:")


def test_opportunity_rejects_digest_tampering() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="digest mismatch",
    ):
        replace(opportunity(), title="Changed")


def test_opportunity_requires_evidence() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="requires evidence",
    ):
        replace(
            opportunity(),
            evidence=(),
            opportunity_digest="",
        )


def test_evidence_rejects_credentials() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="credential",
    ):
        EvolutionEvidenceRef(
            evidence_id="secret-evidence",
            kind="metric_observation",
            content_digest=DIGEST,
            provenance="api_key=secret-value",
            observed_at=NOW,
            reference="evidence/secret.json",
        )


@pytest.mark.parametrize(
    "bad_reference",
    [
        "/Users/operator/evidence.json",
        "/home/operator/evidence.json",
        "../outside.json",
        "evidence/../../outside.json",
        "http://127.0.0.1:3000/evidence",
    ],
)
def test_evidence_references_fail_closed(
    bad_reference: str,
) -> None:
    with pytest.raises(SelfEvolutionValidationError):
        replace(evidence(), reference=bad_reference)


def test_high_risk_requires_security_review() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="security review",
    ):
        RiskAssessment(
            level=RiskLevel.HIGH,
            risk_factors=("security regression",),
            blast_radius=("governance",),
            mitigations=("isolated evaluation",),
            requires_security_review=False,
            requires_financial_review=False,
        )


def test_experiment_must_be_isolated_and_paper_only() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="isolated",
    ):
        replace(experiment(), isolated=False)

    with pytest.raises(
        SelfEvolutionValidationError,
        match="paper-only",
    ):
        replace(experiment(), paper_only=False)

    with pytest.raises(
        SelfEvolutionValidationError,
        match="cannot enable",
    ):
        replace(experiment(), execution_enabled=True)


def test_rollback_cannot_execute_automatically() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="automatic rollback",
    ):
        replace(rollback(), automatic_rollback_enabled=True)


def test_proposal_is_immutable_and_non_applying() -> None:
    first = proposal()
    second = proposal()

    assert first == second
    assert first.proposal_digest == second.proposal_digest
    assert first.proposal_digest.startswith("sha256:")
    assert first.can_apply_change is False
    assert first.can_self_approve is False
    assert first.can_promote is False


def test_proposal_rejects_digest_tampering() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="digest mismatch",
    ):
        replace(proposal(), summary="Changed")


def test_invalid_transition_fails_closed() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="transition",
    ):
        validate_proposal_transition(
            ProposalState.DRAFT,
            ProposalState.PROMOTION_READY,
        )


def test_transition_returns_new_immutable_proposal() -> None:
    current = proposal()
    updated = transition_proposal(
        current,
        ProposalState.READY_FOR_REVIEW,
    )

    assert current.state is ProposalState.DRAFT
    assert updated.state is ProposalState.READY_FOR_REVIEW
    assert updated.proposal_digest != current.proposal_digest


def test_lifecycle_event_is_hash_linked() -> None:
    value = proposal()
    event = create_lifecycle_event(
        value,
        event_id="event-stage9-001",
        sequence=0,
        occurred_at=LATER,
        actor_identity="governance-reviewer",
        requested_state=ProposalState.READY_FOR_REVIEW,
        reason="Evidence and proposal plan are complete.",
    )

    assert event.event_digest.startswith("sha256:")
    assert event.previous_state is ProposalState.DRAFT
    assert event.requested_state is ProposalState.READY_FOR_REVIEW


def test_lifecycle_event_rejects_digest_tampering() -> None:
    event = create_lifecycle_event(
        proposal(),
        event_id="event-stage9-001",
        sequence=0,
        occurred_at=LATER,
        actor_identity="governance-reviewer",
        requested_state=ProposalState.READY_FOR_REVIEW,
        reason="Ready.",
    )

    with pytest.raises(
        SelfEvolutionValidationError,
        match="digest mismatch",
    ):
        replace(event, reason="Changed")


def test_result_validates_against_proposal_budget() -> None:
    value = proposal()
    recorded = result()

    recorded.validate_for(value)


def test_result_rejects_cost_over_budget() -> None:
    with pytest.raises(
        SelfEvolutionValidationError,
        match="cost budget",
    ):
        result(cost_usd="5.01").validate_for(proposal())


def test_result_rejects_wrong_proposal_digest() -> None:
    recorded = replace(
        result(),
        proposal_digest="sha256:" + "c" * 64,
        result_digest="",
    )

    with pytest.raises(
        SelfEvolutionValidationError,
        match="proposal digest",
    ):
        recorded.validate_for(proposal())


def test_creator_review_does_not_count_as_independent() -> None:
    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(reviewer="hermes-control-plane"),),
        result=result(),
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.REVIEW_REQUIRED
    )
    assert assessment.independent_reviews_satisfied is False


def test_incomplete_evidence_blocks_promotion() -> None:
    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=result(),
        evidence_complete=False,
        certification_results=certification_results(),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.EVIDENCE_INCOMPLETE
    )


def test_critical_risk_blocks_promotion() -> None:
    critical_proposal = proposal(
        risk_value=risk(level=RiskLevel.CRITICAL)
    )

    assessment = assess_promotion_readiness(
        critical_proposal,
        reviews=(review(),),
        result=result(proposal_value=critical_proposal),
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert assessment.readiness is PromotionReadiness.RISK_BLOCKED


def test_missing_result_requires_experiment() -> None:
    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=None,
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.EXPERIMENT_REQUIRED
    )


def test_regression_blocks_promotion() -> None:
    regression = evidence(
        evidence_id="regression-stage9",
        kind="regression",
    )
    recorded = result(
        outcome=ExperimentOutcome.REGRESSION,
        regression_evidence=(regression,),
    )

    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=recorded,
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.REGRESSION_BLOCKED
    )
    assert assessment.regression_free is False


def test_failed_required_test_requires_experiment() -> None:
    recorded = result(
        outcome=ExperimentOutcome.FAILED,
        failed_tests=("full-sigil-python-suite",),
    )

    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=recorded,
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.EXPERIMENT_REQUIRED
    )


def test_incomplete_certification_blocks_promotion() -> None:
    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=result(),
        evidence_complete=True,
        certification_results=certification_results(
            complete=False
        ),
    )

    assert (
        assessment.readiness
        is PromotionReadiness.CERTIFICATION_REQUIRED
    )


def test_complete_proposal_is_promotion_ready_but_cannot_promote() -> None:
    assessment = assess_promotion_readiness(
        proposal(),
        reviews=(review(),),
        result=result(),
        evidence_complete=True,
        certification_results=certification_results(),
    )

    assert assessment.readiness is PromotionReadiness.READY
    assert assessment.evidence_complete is True
    assert assessment.independent_reviews_satisfied is True
    assert assessment.experiment_passed is True
    assert assessment.required_tests_passed is True
    assert assessment.certification_satisfied is True
    assert assessment.regression_free is True
    assert assessment.risk_acceptable is True
    assert assessment.can_promote is False
