from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GAMMA_GOVERNANCE_INVARIANT_FIELDS,
    GammaGovernanceEvidence,
    GammaGovernanceEvidenceError,
    build_gamma_governance_evidence,
    require_verified_governance_evidence,
)

REVISION = "862b1e611"
OTHER_REVISION = "26d38ee30"
NOW = "2026-08-02T23:30:00Z"


def governance_evidence(**overrides) -> GammaGovernanceEvidence:
    values = {
        "gamma_revision": REVISION,
        "paper_only_preserved": True,
        "broker_submission_disabled": True,
        "external_provider_explicit_admission": True,
        "claude_advisory_only": True,
        "human_release_review_required": True,
        "verified_by": "release-engineering",
        "verified_at": NOW,
    }
    values.update(overrides)
    return build_gamma_governance_evidence(**values)


def test_governance_evidence_is_self_consistent_and_deterministic() -> None:
    first = governance_evidence()
    second = governance_evidence()

    assert first == second
    assert first.evidence_id == second.evidence_id
    assert first.evidence_digest == second.evidence_digest
    assert first.evidence_id.startswith("gamma-governance-evidence-")
    assert first.evidence_digest.startswith("sha256:")


def test_governance_evidence_digest_changes_with_content() -> None:
    baseline = governance_evidence()
    changed = governance_evidence(verified_by="someone-else")

    assert baseline.evidence_digest != changed.evidence_digest
    assert baseline.evidence_id != changed.evidence_id


@pytest.mark.parametrize(
    "field,value",
    [
        ("gamma_revision", "not-hex"),
        ("verified_by", "   "),
        ("verified_at", ""),
    ],
)
def test_governance_evidence_rejects_malformed_fields(field: str, value: object) -> None:
    with pytest.raises(GammaGovernanceEvidenceError):
        governance_evidence(**{field: value})


@pytest.mark.parametrize("field", GAMMA_GOVERNANCE_INVARIANT_FIELDS)
def test_governance_evidence_rejects_non_boolean_invariant(field: str) -> None:
    with pytest.raises(GammaGovernanceEvidenceError, match="explicit boolean"):
        governance_evidence(**{field: 1})


def test_governance_evidence_construction_recomputes_and_rejects_any_tamper() -> None:
    valid = governance_evidence()

    for field, value in (
        ("gamma_revision", OTHER_REVISION),
        ("paper_only_preserved", False),
        ("broker_submission_disabled", False),
        ("external_provider_explicit_admission", False),
        ("claude_advisory_only", False),
        ("human_release_review_required", False),
        ("verified_by", "someone-else"),
        ("verified_at", "2026-08-03T00:00:00Z"),
    ):
        with pytest.raises(GammaGovernanceEvidenceError, match="does not match recorded"):
            replace(valid, **{field: value})


def test_governance_evidence_rejects_forged_identity() -> None:
    valid = governance_evidence()

    with pytest.raises(GammaGovernanceEvidenceError, match="identity does not match"):
        replace(valid, evidence_id="gamma-governance-evidence-" + "0" * 64)


def test_require_verified_governance_evidence_accepts_valid_record() -> None:
    result = require_verified_governance_evidence(
        governance_evidence(), gamma_revision=REVISION
    )

    assert result == governance_evidence()


def test_require_verified_governance_evidence_rejects_wrong_type() -> None:
    with pytest.raises(GammaGovernanceEvidenceError, match="verified Gamma governance evidence"):
        require_verified_governance_evidence(None, gamma_revision=REVISION)


def test_require_verified_governance_evidence_rejects_revision_mismatch() -> None:
    with pytest.raises(GammaGovernanceEvidenceError, match="does not match"):
        require_verified_governance_evidence(
            governance_evidence(gamma_revision=REVISION),
            gamma_revision=OTHER_REVISION,
        )


@pytest.mark.parametrize("field", GAMMA_GOVERNANCE_INVARIANT_FIELDS)
def test_require_verified_governance_evidence_rejects_any_false_invariant(field: str) -> None:
    tampered = governance_evidence(**{field: False})

    with pytest.raises(GammaGovernanceEvidenceError, match="not all verified true"):
        require_verified_governance_evidence(tampered, gamma_revision=REVISION)


def test_require_verified_governance_evidence_rejects_tampered_bypassed_record() -> None:
    valid = governance_evidence()
    broken = object.__new__(type(valid))
    for field in valid.__dataclass_fields__:
        object.__setattr__(broken, field, getattr(valid, field))
    object.__setattr__(broken, "human_release_review_required", False)

    with pytest.raises(GammaGovernanceEvidenceError, match="not all verified true"):
        require_verified_governance_evidence(broken, gamma_revision=REVISION)
