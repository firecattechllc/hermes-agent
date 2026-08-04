from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GAMMA_RELIABILITY_OUTCOME_FIELDS,
    GAMMA_RELIABILITY_SUITE_ID,
    GammaEvidenceError,
    GammaEvidenceOutcome,
    GammaEvidenceVerification,
    GammaReliabilityCertification,
    GammaReliabilityOutcomes,
    build_gamma_test_evidence,
    certify_gamma_reliability,
    gamma_reliability_manifest,
)

NOW = "2026-08-02T23:30:00Z"
REVISION = "862b1e611"
OTHER_REVISION = "26d38ee30"


def reliability_outcomes(**overrides) -> GammaReliabilityOutcomes:
    values = {name: True for name in GAMMA_RELIABILITY_OUTCOME_FIELDS}
    values.update(overrides)
    return GammaReliabilityOutcomes(**values)


def passing_evidence(**overrides) -> object:
    values = {
        "target_revision": REVISION,
        "suite_id": GAMMA_RELIABILITY_SUITE_ID,
        "outcome": GammaEvidenceOutcome.PASSED,
        "passed_count": 42,
        "failed_count": 0,
        "executed_at": NOW,
        "run_id": "local-cert-run-0001",
        "verification_status": GammaEvidenceVerification.VERIFIED,
        "reliability_outcomes": reliability_outcomes(),
    }
    values.update(overrides)
    return build_gamma_test_evidence(**values)


def certification():
    return certify_gamma_reliability(
        target_revision=REVISION,
        certified_at=NOW,
        evidence=passing_evidence(),
    )


def test_stage6_manifest_certifies_all_reliability_domains() -> None:
    result = certification()

    assert result.certified_domains == (
        "certification_replay",
        "claude_transport",
        "cross_provider_validation",
        "independent_inspection",
        "inspection_store",
        "validation_store",
    )
    assert result.replay_deterministic is True
    assert result.corruption_fails_closed is True
    assert result.malformed_output_fails_closed is True
    assert result.partial_evidence_requires_review is True
    assert result.timeout_failures_typed is True
    assert result.explicit_recovery_required is True
    assert result.evidence_id.startswith("gamma-test-evidence-")
    assert result.evidence_digest.startswith("sha256:")


def test_stage6_manifest_is_deterministic() -> None:
    first = certification()
    second = certification()

    assert first == second
    assert first.certification_id.startswith(
        "gamma-reliability-certification-"
    )
    assert first.certification_digest.startswith("sha256:")


def test_stage6_manifest_is_sanitized_and_replayable() -> None:
    result = certification()
    manifest = gamma_reliability_manifest(result)

    assert manifest["target_revision"] == REVISION
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
    result = certification()

    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(result, **{field: value})


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
    result = certification()

    with pytest.raises(ValueError, match="all reliability guarantees"):
        replace(result, **{field: False})


def test_stage6_manifest_rejects_unsorted_or_duplicate_domains() -> None:
    result = certification()

    with pytest.raises(ValueError, match="sorted"):
        replace(
            result,
            certified_domains=("validation_store", "claude_transport"),
        )

    with pytest.raises(ValueError, match="unique"):
        replace(
            result,
            certified_domains=("claude_transport", "claude_transport"),
        )


def test_stage6_manifest_type_is_stable() -> None:
    result = certification()

    assert isinstance(result, GammaReliabilityCertification)


def test_certification_requires_evidence_to_be_supplied() -> None:
    with pytest.raises(TypeError):
        certify_gamma_reliability(target_revision=REVISION, certified_at=NOW)


def test_certification_rejects_non_evidence_object() -> None:
    with pytest.raises(GammaEvidenceError, match="verified test evidence"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence={"outcome": "passed"},
        )


def test_certification_fails_closed_on_failed_evidence() -> None:
    with pytest.raises(GammaEvidenceError, match="not a pass"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(
                outcome=GammaEvidenceOutcome.FAILED,
                passed_count=10,
                failed_count=2,
            ),
        )


@pytest.mark.parametrize(
    "outcome",
    [
        GammaEvidenceOutcome.BLOCKED,
        GammaEvidenceOutcome.NOT_TESTED,
        GammaEvidenceOutcome.UNKNOWN,
    ],
)
def test_certification_fails_closed_on_non_passing_outcomes(
    outcome: GammaEvidenceOutcome,
) -> None:
    with pytest.raises(GammaEvidenceError, match="not a pass"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(
                outcome=outcome,
                passed_count=0,
                failed_count=0,
            ),
        )


def test_certification_fails_closed_on_unverified_evidence() -> None:
    with pytest.raises(GammaEvidenceError, match="not been verified"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(
                verification_status=GammaEvidenceVerification.UNVERIFIED,
            ),
        )


def test_certification_fails_closed_on_contradictory_evidence() -> None:
    # outcome PASSED but failures were recorded — internally contradictory.
    with pytest.raises(GammaEvidenceError, match="failing checks"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(failed_count=1),
        )


def test_certification_fails_closed_on_zero_passed_checks() -> None:
    with pytest.raises(GammaEvidenceError, match="no passing checks"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(passed_count=0),
        )


def test_certification_fails_closed_on_revision_mismatch() -> None:
    with pytest.raises(GammaEvidenceError, match="does not match"):
        certify_gamma_reliability(
            target_revision=OTHER_REVISION,
            certified_at=NOW,
            evidence=passing_evidence(target_revision=REVISION),
        )


def test_certification_fails_closed_on_tampered_evidence() -> None:
    evidence = passing_evidence()

    with pytest.raises(GammaEvidenceError, match="does not match recorded evidence"):
        # Bypass the builder to simulate a tampered record: flip the failure
        # count without recomputing the digest that binds it.
        replace(evidence, failed_count=99)


def test_certification_fails_closed_on_forged_digest() -> None:
    evidence = passing_evidence()

    with pytest.raises(GammaEvidenceError, match="does not match recorded evidence"):
        replace(evidence, evidence_digest="sha256:" + "f" * 64)


def test_certification_fails_closed_on_unrelated_suite_id() -> None:
    with pytest.raises(GammaEvidenceError, match="does not match the expected"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(suite_id="some-other-passing-suite-v1"),
        )


@pytest.mark.parametrize("field", GAMMA_RELIABILITY_OUTCOME_FIELDS)
def test_certification_fails_closed_on_false_reliability_outcome(field: str) -> None:
    # Proves the six guarantee fields are read from evidence rather than
    # assigned as unconditional literals: a single false per-guarantee
    # outcome in otherwise-clean, expected-suite, verified evidence is
    # enough to block certification outright.
    with pytest.raises(GammaEvidenceError, match="not all verified true"):
        certify_gamma_reliability(
            target_revision=REVISION,
            certified_at=NOW,
            evidence=passing_evidence(
                reliability_outcomes=reliability_outcomes(**{field: False}),
            ),
        )
