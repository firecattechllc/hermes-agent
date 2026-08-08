from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GammaReleaseReviewState,
    GammaStageEvidence,
    build_gamma_release_readiness_manifest,
    gamma_release_review_projection,
    review_gamma_release_readiness,
)

NOW = "2026-08-03T00:30:00Z"


def readiness():
    return build_gamma_release_readiness_manifest(
        golden_master_revision="26d38ee30",
        golden_master_tag="sigil-golden-master-v3.5.0",
        gamma_revision="aaad4a554",
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


def test_clean_manifest_is_ready_for_human_decision() -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )

    assert review.state == GammaReleaseReviewState.READY_FOR_HUMAN_DECISION
    assert review.blockers == ()
    assert review.human_decision_required is True
    assert review.release_authorized is False
    assert review.promotion_authorized is False
    assert review.approval_authority is False
    assert review.execution_authorized is False
    assert review.broker_submission is False
    assert review.portfolio_mutation is False
    assert review.tool_execution is False
    assert review.paper_only is True


def test_missing_guarantee_blocks_release_review() -> None:
    valid = readiness()
    broken = object.__new__(type(valid))
    for field in valid.__dataclass_fields__:
        object.__setattr__(broken, field, getattr(valid, field))
    object.__setattr__(broken, "release_ready_for_review", False)

    review = review_gamma_release_readiness(
        broken,
        reviewed_at=NOW,
    )

    assert review.state == GammaReleaseReviewState.BLOCKED
    assert "release_ready_for_review" in review.blockers
    assert review.human_decision_required is True
    assert review.release_authorized is False


def test_review_is_deterministic_and_manifest_linked() -> None:
    manifest = readiness()

    first = review_gamma_release_readiness(manifest, reviewed_at=NOW)
    second = review_gamma_release_readiness(manifest, reviewed_at=NOW)

    assert first == second
    assert first.manifest_id == manifest.manifest_id
    assert first.manifest_digest == manifest.manifest_digest
    assert first.golden_master_revision == "26d38ee30"
    assert first.gamma_revision == "aaad4a554"
    assert first.review_id.startswith("gamma-release-review-")
    assert first.review_digest.startswith("sha256:")


def test_review_projection_is_sanitized() -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )
    projection = gamma_release_review_projection(review)

    assert projection["state"] == "ready_for_human_decision"
    assert projection["release_authorized"] is False
    assert projection["promotion_authorized"] is False
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
def test_review_authority_fields_fail_closed(
    field: str,
    value: bool,
) -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(review, **{field: value})


def test_ready_review_cannot_contain_blockers() -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot contain blockers"):
        replace(review, blockers=("unexpected",))


def test_blocked_review_requires_blockers() -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )

    with pytest.raises(ValueError, match="requires blockers"):
        replace(
            review,
            state=GammaReleaseReviewState.BLOCKED,
            blockers=(),
        )


def test_review_always_requires_human_decision() -> None:
    review = review_gamma_release_readiness(
        readiness(),
        reviewed_at=NOW,
    )

    with pytest.raises(ValueError, match="human decision"):
        replace(review, human_decision_required=False)
