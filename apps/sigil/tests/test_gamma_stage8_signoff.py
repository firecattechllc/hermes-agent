from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GammaReleaseReviewState,
    GammaSignoffState,
    GammaStageEvidence,
    build_gamma_release_readiness_manifest,
    build_gamma_signoff,
    gamma_signoff_projection,
    review_gamma_release_readiness,
)

NOW = "2026-08-03T01:00:00Z"
TAG = "sigil-gamma-v3.5.0"


def readiness():
    return build_gamma_release_readiness_manifest(
        golden_master_revision="26d38ee30",
        golden_master_tag="sigil-golden-master-v3.5.0",
        gamma_revision="c39604e07",
        stage_evidence=(
            GammaStageEvidence(1, "c9e6a7f02", "Claude provider foundation"),
            GammaStageEvidence(2, "429cb247f", "Governed Claude transport"),
            GammaStageEvidence(3, "d576352e6", "Governed Claude routing"),
            GammaStageEvidence(4, "16a564d29", "Independent inspection"),
            GammaStageEvidence(5, "c5c8f293e", "Cross-provider validation"),
            GammaStageEvidence(6, "514d83be5", "Reliability certification"),
        ),
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
    assert first.gamma_revision == "c39604e07"
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
