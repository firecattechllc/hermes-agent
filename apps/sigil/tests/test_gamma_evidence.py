from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GAMMA_RELIABILITY_OUTCOME_FIELDS,
    GammaEvidenceError,
    GammaEvidenceOutcome,
    GammaEvidenceVerification,
    GammaReliabilityOutcomes,
    GammaTestEvidence,
    build_gamma_test_evidence,
    require_expected_suite,
    require_passing_evidence,
    require_reliability_outcomes,
)

REVISION = "862b1e611"
NOW = "2026-08-02T23:30:00Z"
SUITE_ID = "sigil-gamma-reliability-v1"


def reliability_outcomes(**overrides) -> GammaReliabilityOutcomes:
    values = {name: True for name in GAMMA_RELIABILITY_OUTCOME_FIELDS}
    values.update(overrides)
    return GammaReliabilityOutcomes(**values)


def evidence(**overrides) -> GammaTestEvidence:
    values = {
        "target_revision": REVISION,
        "suite_id": SUITE_ID,
        "outcome": GammaEvidenceOutcome.PASSED,
        "passed_count": 10,
        "failed_count": 0,
        "executed_at": NOW,
        "run_id": "local-cert-run-0001",
        "verification_status": GammaEvidenceVerification.VERIFIED,
        "reliability_outcomes": reliability_outcomes(),
    }
    values.update(overrides)
    return build_gamma_test_evidence(**values)


def test_evidence_is_self_consistent_and_deterministic() -> None:
    first = evidence()
    second = evidence()

    assert first == second
    assert first.evidence_id == second.evidence_id
    assert first.evidence_digest == second.evidence_digest
    assert first.evidence_id.startswith("gamma-test-evidence-")
    assert first.evidence_digest.startswith("sha256:")


def test_evidence_digest_changes_with_content() -> None:
    baseline = evidence()
    changed = evidence(run_id="local-cert-run-0002")

    assert baseline.evidence_digest != changed.evidence_digest
    assert baseline.evidence_id != changed.evidence_id


def test_evidence_digest_changes_with_reliability_outcomes() -> None:
    baseline = evidence()
    changed = evidence(reliability_outcomes=reliability_outcomes(timeout_failures_typed=False))

    assert baseline.evidence_digest != changed.evidence_digest
    assert baseline.evidence_id != changed.evidence_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_revision", "not-hex"),
        ("suite_id", "   "),
        ("run_id", ""),
        ("executed_at", ""),
        ("passed_count", -1),
        ("failed_count", -1),
    ],
)
def test_evidence_rejects_malformed_fields(field: str, value: object) -> None:
    with pytest.raises(GammaEvidenceError):
        evidence(**{field: value})


def test_evidence_rejects_missing_reliability_outcomes() -> None:
    with pytest.raises(GammaEvidenceError, match="reliability outcomes"):
        evidence(reliability_outcomes=None)


@pytest.mark.parametrize("field", GAMMA_RELIABILITY_OUTCOME_FIELDS)
def test_reliability_outcomes_rejects_non_boolean_field(field: str) -> None:
    with pytest.raises(GammaEvidenceError, match="explicit boolean"):
        reliability_outcomes(**{field: 1})


def test_reliability_outcomes_rejects_missing_field() -> None:
    with pytest.raises(TypeError):
        GammaReliabilityOutcomes(
            **{
                name: True
                for name in GAMMA_RELIABILITY_OUTCOME_FIELDS[:-1]
            }
        )


def test_evidence_construction_recomputes_and_rejects_any_tamper() -> None:
    valid = evidence()

    for field, value in (
        ("target_revision", "26d38ee30"),
        ("suite_id", "different-suite"),
        ("outcome", GammaEvidenceOutcome.FAILED),
        ("passed_count", 99),
        ("failed_count", 1),
        ("executed_at", "2026-08-03T00:00:00Z"),
        ("run_id", "different-run"),
        ("verification_status", GammaEvidenceVerification.UNVERIFIED),
        ("reliability_outcomes", reliability_outcomes(replay_deterministic=False)),
    ):
        with pytest.raises(GammaEvidenceError, match="does not match recorded"):
            replace(valid, **{field: value})


def test_evidence_rejects_forged_identity() -> None:
    valid = evidence()

    with pytest.raises(GammaEvidenceError, match="identity does not match"):
        replace(valid, evidence_id="gamma-test-evidence-" + "0" * 64)


def test_replace_cannot_silently_alter_reliability_outcomes() -> None:
    valid = evidence()
    tampered_outcomes = replace(valid.reliability_outcomes, corruption_fails_closed=False)

    with pytest.raises(GammaEvidenceError, match="does not match recorded"):
        replace(valid, reliability_outcomes=tampered_outcomes)


def test_require_passing_evidence_accepts_clean_pass() -> None:
    require_passing_evidence(evidence(), target_revision=REVISION)


def test_require_passing_evidence_rejects_wrong_type() -> None:
    with pytest.raises(GammaEvidenceError, match="verified test evidence"):
        require_passing_evidence(None, target_revision=REVISION)


def test_require_passing_evidence_rejects_missing_counts() -> None:
    valid = evidence()
    stripped = build_gamma_test_evidence(
        target_revision=valid.target_revision,
        suite_id=valid.suite_id,
        outcome=valid.outcome,
        passed_count=None,
        failed_count=None,
        executed_at=valid.executed_at,
        run_id=valid.run_id,
        verification_status=valid.verification_status,
        reliability_outcomes=valid.reliability_outcomes,
    )

    with pytest.raises(GammaEvidenceError, match="missing pass/fail counts"):
        require_passing_evidence(stripped, target_revision=REVISION)


def test_require_expected_suite_accepts_matching_suite() -> None:
    require_expected_suite(evidence(), expected_suite_id=SUITE_ID)


def test_require_expected_suite_rejects_unrelated_suite() -> None:
    with pytest.raises(GammaEvidenceError, match="does not match the expected"):
        require_expected_suite(
            evidence(suite_id="totally-unrelated-suite"),
            expected_suite_id=SUITE_ID,
        )


def test_require_reliability_outcomes_accepts_all_true() -> None:
    outcomes = require_reliability_outcomes(evidence())

    assert all(getattr(outcomes, name) is True for name in GAMMA_RELIABILITY_OUTCOME_FIELDS)


@pytest.mark.parametrize("field", GAMMA_RELIABILITY_OUTCOME_FIELDS)
def test_require_reliability_outcomes_rejects_any_false_outcome(field: str) -> None:
    tampered = evidence(reliability_outcomes=reliability_outcomes(**{field: False}))

    with pytest.raises(GammaEvidenceError, match="not all verified true"):
        require_reliability_outcomes(tampered)


def test_require_reliability_outcomes_rejects_missing_outcomes_object() -> None:
    valid = evidence()
    broken = object.__new__(type(valid))
    for field in valid.__dataclass_fields__:
        object.__setattr__(broken, field, getattr(valid, field))
    object.__setattr__(broken, "reliability_outcomes", None)

    with pytest.raises(GammaEvidenceError, match="missing verified reliability outcomes"):
        require_reliability_outcomes(broken)
