from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GammaReliabilityCertification,
    certify_gamma_reliability,
    gamma_reliability_manifest,
)

NOW = "2026-08-02T23:30:00Z"


def test_stage6_manifest_certifies_all_reliability_domains() -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    assert certification.certified_domains == (
        "certification_replay",
        "claude_transport",
        "cross_provider_validation",
        "independent_inspection",
        "inspection_store",
        "validation_store",
    )
    assert certification.replay_deterministic is True
    assert certification.corruption_fails_closed is True
    assert certification.malformed_output_fails_closed is True
    assert certification.partial_evidence_requires_review is True
    assert certification.timeout_failures_typed is True
    assert certification.explicit_recovery_required is True


def test_stage6_manifest_is_deterministic() -> None:
    first = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )
    second = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    assert first == second
    assert first.certification_id.startswith(
        "gamma-reliability-certification-"
    )
    assert first.certification_digest.startswith("sha256:")


def test_stage6_manifest_is_sanitized_and_replayable() -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )
    manifest = gamma_reliability_manifest(certification)

    assert manifest["target_revision"] == "862b1e611"
    assert manifest["promotion_authorized"] is False
    assert manifest["release_authority"] is False
    assert manifest["approval_authority"] is False
    assert "prompt" not in manifest
    assert "content" not in manifest
    assert "credential" not in manifest


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
def test_stage6_manifest_authority_fields_fail_closed(
    field: str,
    value: bool,
) -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(certification, **{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "replay_deterministic",
        "corruption_fails_closed",
        "malformed_output_fails_closed",
        "partial_evidence_requires_review",
        "timeout_failures_typed",
        "explicit_recovery_required",
    ],
)
def test_stage6_manifest_requires_every_reliability_guarantee(
    field: str,
) -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    with pytest.raises(ValueError, match="all reliability guarantees"):
        replace(certification, **{field: False})


def test_stage6_manifest_rejects_unsorted_or_duplicate_domains() -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    with pytest.raises(ValueError, match="sorted"):
        replace(
            certification,
            certified_domains=("validation_store", "claude_transport"),
        )

    with pytest.raises(ValueError, match="unique"):
        replace(
            certification,
            certified_domains=("claude_transport", "claude_transport"),
        )


def test_stage6_manifest_type_is_stable() -> None:
    certification = certify_gamma_reliability(
        target_revision="862b1e611",
        certified_at=NOW,
    )

    assert isinstance(certification, GammaReliabilityCertification)
