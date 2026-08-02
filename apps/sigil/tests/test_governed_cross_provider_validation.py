from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    CrossProviderValidationState,
    ProviderClaim,
    validate_cross_provider_claims,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T21:30:00Z"


def claim(
    claim_id: str,
    provider_id: str,
    subject: str,
    value: str,
    evidence: tuple[str, ...] = (DIGEST_B,),
) -> ProviderClaim:
    return ProviderClaim(
        claim_id=claim_id,
        provider_id=provider_id,
        model_id=(
            "gemma-governed"
            if provider_id == "local-gemma"
            else "claude-sonnet-governed"
        ),
        subject=subject,
        normalized_value=value,
        evidence_references=evidence,
        confidence=0.8,
    )


def validate(gemma, claude):
    return validate_cross_provider_claims(
        target_revision="16a564d29",
        target_digest=DIGEST_A,
        gemma_claims=tuple(gemma),
        claude_claims=tuple(claude),
        validated_at=NOW,
    )


def test_matching_claims_are_consistent_and_advisory_only() -> None:
    report = validate(
        [claim("g-1", "local-gemma", "routing-policy", "explicit-admission")],
        [claim("c-1", "hermes-claude", "routing-policy", "explicit-admission")],
    )

    assert report.state == CrossProviderValidationState.CONSISTENT
    assert report.agreement_count == 1
    assert report.disagreement_count == 0
    assert report.missing_coverage_count == 0
    assert report.human_review_required is False
    assert report.promotion_authorized is False
    assert report.release_authority is False
    assert report.approval_authority is False
    assert report.execution_authorized is False
    assert report.broker_submission is False
    assert report.portfolio_mutation is False
    assert report.tool_execution is False
    assert report.paper_only is True


def test_disagreement_requires_human_review() -> None:
    report = validate(
        [claim("g-1", "local-gemma", "risk", "low")],
        [claim("c-1", "hermes-claude", "risk", "high")],
    )

    assert report.state == CrossProviderValidationState.REVIEW_REQUIRED
    assert report.disagreement_count == 1
    assert report.human_review_required is True
    assert report.comparisons[0].state == "disagreement"
    assert report.promotion_authorized is False


def test_missing_coverage_is_insufficient_evidence() -> None:
    report = validate(
        [
            claim("g-1", "local-gemma", "routing", "safe"),
            claim("g-2", "local-gemma", "privacy", "external-approved"),
        ],
        [claim("c-1", "hermes-claude", "routing", "safe")],
    )

    assert report.state == CrossProviderValidationState.INSUFFICIENT_EVIDENCE
    assert report.missing_coverage_count == 1
    assert report.human_review_required is True


def test_no_shared_evidence_is_insufficient_even_when_values_match() -> None:
    report = validate(
        [claim("g-1", "local-gemma", "routing", "safe", (DIGEST_A,))],
        [claim("c-1", "hermes-claude", "routing", "safe", (DIGEST_B,))],
    )

    assert report.state == CrossProviderValidationState.INSUFFICIENT_EVIDENCE
    assert report.comparisons[0].state == "insufficient_shared_evidence"
    assert report.agreement_count == 0


def test_validation_is_deterministic_and_sorted() -> None:
    gemma = (
        claim("g-2", "local-gemma", "zeta", "yes"),
        claim("g-1", "local-gemma", "alpha", "yes"),
    )
    claude = (
        claim("c-2", "hermes-claude", "zeta", "yes"),
        claim("c-1", "hermes-claude", "alpha", "yes"),
    )

    first = validate(gemma, claude)
    second = validate(gemma, claude)

    assert first == second
    assert first.validation_digest.startswith("sha256:")
    assert first.validation_id.startswith("cross-provider-validation-")
    assert tuple(item.subject for item in first.comparisons) == ("alpha", "zeta")


@pytest.mark.parametrize(
    "field,value",
    [
        ("promotion_authorized", True),
        ("release_authority", True),
        ("approval_authority", True),
        ("execution_authorized", True),
        ("broker_submission", True),
        ("portfolio_mutation", True),
        ("tool_execution", True),
        ("paper_only", False),
    ],
)
def test_validation_authority_fields_fail_closed(field: str, value: bool) -> None:
    report = validate(
        [claim("g-1", "local-gemma", "routing", "safe")],
        [claim("c-1", "hermes-claude", "routing", "safe")],
    )
    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(report, **{field: value})


def test_invalid_provider_identity_and_duplicate_subjects_fail_closed() -> None:
    with pytest.raises(ValueError, match="local-gemma"):
        validate(
            [claim("g-1", "other-gemma", "routing", "safe")],
            [claim("c-1", "hermes-claude", "routing", "safe")],
        )

    duplicate = claim("g-2", "local-gemma", "routing", "safe")
    with pytest.raises(ValueError, match="duplicate a subject"):
        validate(
            [claim("g-1", "local-gemma", "routing", "safe"), duplicate],
            [claim("c-1", "hermes-claude", "routing", "safe")],
        )


def test_validation_identity_changes_when_claim_content_changes() -> None:
    first = validate_cross_provider_claims(
        target_revision="360d4730d",
        target_digest=DIGEST_A,
        gemma_claims=(
            claim("g-1", "local-gemma", "routing", "safe"),
        ),
        claude_claims=(
            claim("c-1", "hermes-claude", "routing", "safe"),
        ),
        validated_at=NOW,
    )
    second = validate_cross_provider_claims(
        target_revision="360d4730d",
        target_digest=DIGEST_A,
        gemma_claims=(
            claim("g-1", "local-gemma", "risk", "low"),
        ),
        claude_claims=(
            claim("c-1", "hermes-claude", "risk", "high"),
        ),
        validated_at=NOW,
    )

    assert first.validation_id != second.validation_id
    assert first.validation_digest != second.validation_digest


def test_validation_identity_is_order_independent() -> None:
    gemma = (
        claim("g-2", "local-gemma", "zeta", "yes"),
        claim("g-1", "local-gemma", "alpha", "yes"),
    )
    claude = (
        claim("c-2", "hermes-claude", "zeta", "yes"),
        claim("c-1", "hermes-claude", "alpha", "yes"),
    )

    first = validate_cross_provider_claims(
        target_revision="360d4730d",
        target_digest=DIGEST_A,
        gemma_claims=gemma,
        claude_claims=claude,
        validated_at=NOW,
    )
    second = validate_cross_provider_claims(
        target_revision="360d4730d",
        target_digest=DIGEST_A,
        gemma_claims=tuple(reversed(gemma)),
        claude_claims=tuple(reversed(claude)),
        validated_at=NOW,
    )

    assert first.validation_id == second.validation_id
    assert first.validation_digest == second.validation_digest
