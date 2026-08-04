from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GAMMA_RELIABILITY_OUTCOME_FIELDS,
    GAMMA_RELIABILITY_SUITE_ID,
    GammaClaudeProductionStatus,
    GammaEvidenceOutcome,
    GammaEvidenceVerification,
    GammaReleaseReviewState,
    GammaReliabilityOutcomes,
    GammaSignoffState,
    GammaStageEvidence,
    build_gamma_governance_evidence,
    build_gamma_release_readiness_manifest,
    build_gamma_signoff,
    build_gamma_test_evidence,
    certify_gamma_reliability,
    gamma_signoff_projection,
    review_gamma_release_readiness,
)

NOW = "2026-08-03T01:00:00Z"
TAG = "sigil-gamma-v3.5.0"
GAMMA_REVISION = "c39604e07"


def reliability_outcomes(**overrides) -> GammaReliabilityOutcomes:
    values = {name: True for name in GAMMA_RELIABILITY_OUTCOME_FIELDS}
    values.update(overrides)
    return GammaReliabilityOutcomes(**values)


def evidence(target_revision: str = GAMMA_REVISION):
    return build_gamma_test_evidence(
        target_revision=target_revision,
        suite_id=GAMMA_RELIABILITY_SUITE_ID,
        outcome=GammaEvidenceOutcome.PASSED,
        passed_count=5,
        failed_count=0,
        executed_at=NOW,
        run_id="local-cert-run-0003",
        verification_status=GammaEvidenceVerification.VERIFIED,
        reliability_outcomes=reliability_outcomes(),
    )


def reliability_certification(target_revision: str = GAMMA_REVISION):
    return certify_gamma_reliability(
        target_revision=target_revision,
        certified_at=NOW,
        evidence=evidence(target_revision),
    )


def governance_evidence(gamma_revision: str = GAMMA_REVISION):
    return build_gamma_governance_evidence(
        gamma_revision=gamma_revision,
        paper_only_preserved=True,
        broker_submission_disabled=True,
        external_provider_explicit_admission=True,
        claude_advisory_only=True,
        human_release_review_required=True,
        verified_by="release-engineering",
        verified_at=NOW,
    )


def readiness():
    return build_gamma_release_readiness_manifest(
        golden_master_revision="26d38ee30",
        golden_master_tag="sigil-golden-master-v3.5.0",
        gamma_revision=GAMMA_REVISION,
        stage_evidence=(
            GammaStageEvidence(1, "c9e6a7f02", "Claude provider foundation"),
            GammaStageEvidence(2, "429cb247f", "Governed Claude transport"),
            GammaStageEvidence(3, "d576352e6", "Governed Claude routing"),
            GammaStageEvidence(4, "16a564d29", "Independent inspection"),
            GammaStageEvidence(5, "c5c8f293e", "Cross-provider validation"),
            GammaStageEvidence(6, "514d83be5", "Reliability certification"),
        ),
        reliability_certification=reliability_certification(),
        governance_evidence=governance_evidence(),
        claude_wired_into_production_runtime=False,
        claude_config_enabled=False,
        generated_at=NOW,
    )


def review():
    return review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )


def test_clean_release_review_produces_ready_signoff() -> None:
    signoff = build_gamma_signoff(
        review(),
        gamma_tag=TAG,
        signed_at=NOW,
    )

    assert signoff.state == GammaSignoffState.READY_FOR_PROMOTION_DECISION
    assert signoff.blockers == ()
    assert signoff.human_promotion_decision_required is True
    assert signoff.gamma_tag == TAG
    assert signoff.release_authorized is False
    assert signoff.promotion_authorized is False
    assert signoff.approval_authority is False
    assert signoff.execution_authorized is False
    assert signoff.broker_submission is False
    assert signoff.portfolio_mutation is False
    assert signoff.tool_execution is False
    assert signoff.paper_only is True


def test_signoff_carries_truthful_claude_production_status() -> None:
    signoff = build_gamma_signoff(
        review(),
        gamma_tag=TAG,
        signed_at=NOW,
    )

    assert signoff.claude_subsystem_status == (
        GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED
    )
    assert signoff.claude_production_integrated is False
    assert signoff.claude_production_enabled is False


def test_signoff_preserves_governance_evidence_identity_and_digest() -> None:
    release_review = review()
    signoff = build_gamma_signoff(
        release_review,
        gamma_tag=TAG,
        signed_at=NOW,
    )

    assert signoff.governance_evidence_id == release_review.governance_evidence_id
    assert signoff.governance_evidence_digest == release_review.governance_evidence_digest


def test_blocked_review_produces_blocked_signoff() -> None:
    clean = review()
    blocked = replace(
        clean,
        state=GammaReleaseReviewState.BLOCKED,
        blockers=("release_ready_for_review",),
    )

    signoff = build_gamma_signoff(
        blocked,
        gamma_tag=TAG,
        signed_at=NOW,
    )

    assert signoff.state == GammaSignoffState.BLOCKED
    assert "release_ready_for_review" in signoff.blockers
    assert "release_review_not_ready" in signoff.blockers
    assert signoff.promotion_authorized is False


def test_signoff_is_deterministic_and_review_linked() -> None:
    release_review = review()

    first = build_gamma_signoff(
        release_review,
        gamma_tag=TAG,
        signed_at=NOW,
    )
    second = build_gamma_signoff(
        release_review,
        gamma_tag=TAG,
        signed_at=NOW,
    )

    assert first == second
    assert first.release_review_id == release_review.review_id
    assert first.release_review_digest == release_review.review_digest
    assert first.golden_master_revision == "26d38ee30"
    assert first.gamma_revision == GAMMA_REVISION
    assert first.signoff_id.startswith("gamma-signoff-")
    assert first.signoff_digest.startswith("sha256:")


def test_signoff_projection_is_sanitized() -> None:
    projection = gamma_signoff_projection(
        build_gamma_signoff(
            review(),
            gamma_tag=TAG,
            signed_at=NOW,
        )
    )

    assert projection["state"] == "ready_for_promotion_decision"
    assert projection["promotion_authorized"] is False
    assert projection["release_authorized"] is False
    assert projection["claude_subsystem_status"] == "not_production_integrated"
    assert "prompt" not in projection
    assert "credential" not in projection
    assert "content" not in projection


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_authorized", True),
        ("promotion_authorized", True),
        ("approval_authority", True),
        ("execution_authorized", True),
        ("broker_submission", True),
        ("portfolio_mutation", True),
        ("tool_execution", True),
        ("paper_only", False),
    ],
)
def test_signoff_authority_fields_fail_closed(
    field: str,
    value: bool,
) -> None:
    signoff = build_gamma_signoff(
        review(),
        gamma_tag=TAG,
        signed_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(signoff, **{field: value})


def test_signoff_requires_human_promotion_decision() -> None:
    signoff = build_gamma_signoff(
        review(),
        gamma_tag=TAG,
        signed_at=NOW,
    )

    with pytest.raises(ValueError, match="human promotion decision"):
        replace(signoff, human_promotion_decision_required=False)


def test_signoff_rejects_invalid_tag() -> None:
    with pytest.raises(ValueError, match="tag is invalid"):
        build_gamma_signoff(
            review(),
            gamma_tag="invalid-tag",
            signed_at=NOW,
        )


def test_signoff_rejects_inconsistent_claude_production_claim() -> None:
    signoff = build_gamma_signoff(
        review(),
        gamma_tag=TAG,
        signed_at=NOW,
    )

    with pytest.raises(ValueError, match="production-enabled"):
        replace(
            signoff,
            claude_subsystem_status=(
                GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
            ),
            claude_production_integrated=False,
            claude_production_enabled=True,
        )
