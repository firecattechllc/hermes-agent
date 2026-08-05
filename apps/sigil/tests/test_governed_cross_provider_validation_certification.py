from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    CrossProviderValidationCertificationState,
    ProviderClaim,
    certify_cross_provider_validation,
    cross_provider_certification_manifest,
    validate_cross_provider_claims,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T22:30:00Z"


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
    )


def validation(
    *,
    gemma_value: str = "safe",
    claude_value: str = "safe",
    claude_evidence: tuple[str, ...] = (DIGEST_B,),
):
    return validate_cross_provider_claims(
        target_revision="9f1b545c6",
        target_digest=DIGEST_A,
        gemma_claims=(
            claim("g-1", "local-gemma", "routing", gemma_value),
        ),
        claude_claims=(
            claim(
                "c-1",
                "hermes-claude",
                "routing",
                claude_value,
                claude_evidence,
            ),
        ),
        validated_at=NOW,
    )


def test_consistent_validation_produces_advisory_pass() -> None:
    certification = certify_cross_provider_validation(
        validation(),
        certified_at=NOW,
    )

    assert (
        certification.state
        == CrossProviderValidationCertificationState.ADVISORY_PASS
    )
    assert certification.human_review_required is False
    assert certification.promotion_authorized is False
    assert certification.release_authority is False
    assert certification.approval_authority is False
    assert certification.execution_authorized is False
    assert certification.broker_submission is False
    assert certification.portfolio_mutation is False
    assert certification.tool_execution is False
    assert certification.paper_only is True


def test_disagreement_requires_human_review() -> None:
    certification = certify_cross_provider_validation(
        validation(
            gemma_value="low",
            claude_value="high",
        ),
        certified_at=NOW,
    )

    assert (
        certification.state
        == CrossProviderValidationCertificationState.REVIEW_REQUIRED
    )
    assert certification.human_review_required is True
    assert certification.disagreement_count == 1
    assert certification.promotion_authorized is False


def test_insufficient_evidence_requires_human_review() -> None:
    certification = certify_cross_provider_validation(
        validation(
            claude_evidence=("sha256:" + "c" * 64,),
        ),
        certified_at=NOW,
    )

    assert (
        certification.state
        == CrossProviderValidationCertificationState.REVIEW_REQUIRED
    )
    assert certification.human_review_required is True
    assert certification.missing_coverage_count == 1
    assert certification.release_authority is False


def test_certification_is_deterministic_and_validation_linked() -> None:
    report = validation()

    first = certify_cross_provider_validation(report, certified_at=NOW)
    second = certify_cross_provider_validation(report, certified_at=NOW)

    assert first == second
    assert first.validation_id == report.validation_id
    assert first.validation_digest == report.validation_digest
    assert first.target_revision == "9f1b545c6"
    assert first.target_digest == DIGEST_A
    assert first.certification_id.startswith(
        "cross-provider-validation-certification-"
    )
    assert first.certification_digest.startswith("sha256:")


def test_manifest_is_sanitized_and_replayable() -> None:
    certification = certify_cross_provider_validation(
        validation(),
        certified_at=NOW,
    )
    manifest = cross_provider_certification_manifest(certification)

    assert manifest["state"] == "advisory_pass"
    assert manifest["validation_digest"] == certification.validation_digest
    assert manifest["promotion_authorized"] is False
    assert manifest["release_authority"] is False
    assert "gemma_value" not in manifest
    assert "claude_value" not in manifest
    assert "shared_evidence" not in manifest
    assert "comparisons" not in manifest


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
def test_certification_authority_fields_fail_closed(
    field: str,
    value: bool,
) -> None:
    certification = certify_cross_provider_validation(
        validation(),
        certified_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(certification, **{field: value})
