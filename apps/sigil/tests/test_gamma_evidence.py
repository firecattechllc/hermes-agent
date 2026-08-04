from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GammaEvidenceError,
    GammaEvidenceOutcome,
    GammaEvidenceVerification,
    GammaTestEvidence,
    build_gamma_test_evidence,
    require_passing_evidence,
)

REVISION = "862b1e611"
NOW = "2026-08-02T23:30:00Z"


def evidence(**overrides) -> GammaTestEvidence:
    values = {
        "target_revision": REVISION,
        "suite_id": "sigil-gamma-reliability-v1",
        "outcome": GammaEvidenceOutcome.PASSED,
        "passed_count": 10,
        "failed_count": 0,
        "executed_at": NOW,
        "run_id": "local-cert-run-0001",
        "verification_status": GammaEvidenceVerification.VERIFIED,
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
    ):
        with pytest.raises(GammaEvidenceError, match="does not match recorded"):
            replace(valid, **{field: value})


def test_evidence_rejects_forged_identity() -> None:
    valid = evidence()

    with pytest.raises(GammaEvidenceError, match="identity does not match"):
        replace(valid, evidence_id="gamma-test-evidence-" + "0" * 64)


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
    )

    with pytest.raises(GammaEvidenceError, match="missing pass/fail counts"):
        require_passing_evidence(stripped, target_revision=REVISION)
