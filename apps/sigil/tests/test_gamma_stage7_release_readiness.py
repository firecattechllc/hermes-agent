from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GAMMA_GOVERNANCE_INVARIANT_FIELDS,
    GAMMA_RELIABILITY_OUTCOME_FIELDS,
    GAMMA_RELIABILITY_SUITE_ID,
    GammaClaudeProductionStatus,
    GammaEvidenceOutcome,
    GammaEvidenceVerification,
    GammaGovernanceEvidenceError,
    GammaReliabilityOutcomes,
    GammaStageEvidence,
    build_gamma_governance_evidence,
    build_gamma_release_readiness_manifest,
    build_gamma_test_evidence,
    certify_gamma_reliability,
    gamma_release_readiness_projection,
)

NOW = "2026-08-03T00:00:00Z"
GAMMA_REVISION = "514d83be5"
OTHER_REVISION = "26d38ee30"


def reliability_outcomes(**overrides) -> GammaReliabilityOutcomes:
    values = {name: True for name in GAMMA_RELIABILITY_OUTCOME_FIELDS}
    values.update(overrides)
    return GammaReliabilityOutcomes(**values)


def evidence(target_revision: str = GAMMA_REVISION):
    return build_gamma_test_evidence(
        target_revision=target_revision,
        suite_id=GAMMA_RELIABILITY_SUITE_ID,
        outcome=GammaEvidenceOutcome.PASSED,
        passed_count=42,
        failed_count=0,
        executed_at=NOW,
        run_id="local-cert-run-0001",
        verification_status=GammaEvidenceVerification.VERIFIED,
        reliability_outcomes=reliability_outcomes(),
    )


def reliability_certification(target_revision: str = GAMMA_REVISION):
    return certify_gamma_reliability(
        target_revision=target_revision,
        certified_at=NOW,
        evidence=evidence(target_revision),
    )


def governance_evidence(gamma_revision: str = GAMMA_REVISION, **overrides):
    values = {
        "gamma_revision": gamma_revision,
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


def stage_evidence() -> tuple[GammaStageEvidence, ...]:
    return (
        GammaStageEvidence(
            1,
            "c9e6a7f02",
            "Claude provider foundation",
        ),
        GammaStageEvidence(
            2,
            "429cb247f",
            "Hermes readiness and governed transport",
        ),
        GammaStageEvidence(
            3,
            "d576352e6",
            "Governed routing and responsibility admission",
        ),
        GammaStageEvidence(
            4,
            "16a564d29",
            "Independent Claude inspection",
        ),
        GammaStageEvidence(
            5,
            "c5c8f293e",
            "Cross-provider validation",
        ),
        GammaStageEvidence(
            6,
            "514d83be5",
            "Reliability and edge-case certification",
        ),
    )


def manifest(
    *,
    gamma_revision: str = GAMMA_REVISION,
    reliability_certification_override=None,
    governance_evidence_override=None,
    claude_wired_into_production_runtime: bool = False,
    claude_config_enabled: bool = False,
):
    return build_gamma_release_readiness_manifest(
        golden_master_revision="26d38ee30",
        golden_master_tag="sigil-golden-master-v3.5.0",
        gamma_revision=gamma_revision,
        stage_evidence=stage_evidence(),
        reliability_certification=(
            reliability_certification(gamma_revision)
            if reliability_certification_override is None
            else reliability_certification_override
        ),
        governance_evidence=(
            governance_evidence(gamma_revision)
            if governance_evidence_override is None
            else governance_evidence_override
        ),
        claude_wired_into_production_runtime=claude_wired_into_production_runtime,
        claude_config_enabled=claude_config_enabled,
        generated_at=NOW,
    )


def test_release_readiness_binds_golden_master_and_stages() -> None:
    result = manifest()

    assert result.golden_master_revision == "26d38ee30"
    assert result.golden_master_tag == "sigil-golden-master-v3.5.0"
    assert result.gamma_revision == GAMMA_REVISION
    assert tuple(item.stage for item in result.stage_evidence) == (
        1,
        2,
        3,
        4,
        5,
        6,
    )
    assert result.all_required_stages_present is True
    assert result.evidence_chain_ordered is True
    assert result.golden_master_preserved is True


def test_release_readiness_preserves_governance_boundaries() -> None:
    result = manifest()

    assert result.paper_only_preserved is True
    assert result.broker_submission_disabled is True
    assert result.external_provider_explicit_admission is True
    assert result.claude_advisory_only is True
    assert result.deterministic_replay_certified is True
    assert result.corruption_fail_closed_certified is True
    assert result.human_release_review_required is True
    assert result.release_ready_for_review is True

    assert result.release_authorized is False
    assert result.promotion_authorized is False
    assert result.approval_authority is False
    assert result.execution_authorized is False
    assert result.broker_submission is False
    assert result.portfolio_mutation is False
    assert result.tool_execution is False
    assert result.paper_only is True


def test_release_readiness_governance_fields_are_evidence_bound() -> None:
    evidence_record = governance_evidence()
    result = manifest(governance_evidence_override=evidence_record)

    assert result.governance_evidence_id == evidence_record.evidence_id
    assert result.governance_evidence_digest == evidence_record.evidence_digest


def test_release_readiness_manifest_is_deterministic() -> None:
    first = manifest()
    second = manifest()

    assert first == second
    assert first.manifest_id.startswith("gamma-release-readiness-")
    assert first.manifest_digest.startswith("sha256:")


def test_release_readiness_projection_is_sanitized() -> None:
    projection = gamma_release_readiness_projection(manifest())

    assert projection["release_ready_for_review"] is True
    assert projection["release_authorized"] is False
    assert projection["stage_evidence"][0]["stage"] == 1
    assert "prompt" not in projection
    assert "credential" not in projection
    assert "content" not in projection


def test_release_readiness_rejects_missing_or_unordered_stages() -> None:
    with pytest.raises(ValueError, match="ordered"):
        build_gamma_release_readiness_manifest(
            golden_master_revision="26d38ee30",
            golden_master_tag="sigil-golden-master-v3.5.0",
            gamma_revision=GAMMA_REVISION,
            stage_evidence=stage_evidence()[:-1],
            reliability_certification=reliability_certification(),
            governance_evidence=governance_evidence(),
            claude_wired_into_production_runtime=False,
            claude_config_enabled=False,
            generated_at=NOW,
        )

    with pytest.raises(ValueError, match="ordered"):
        build_gamma_release_readiness_manifest(
            golden_master_revision="26d38ee30",
            golden_master_tag="sigil-golden-master-v3.5.0",
            gamma_revision=GAMMA_REVISION,
            stage_evidence=tuple(reversed(stage_evidence())),
            reliability_certification=reliability_certification(),
            governance_evidence=governance_evidence(),
            claude_wired_into_production_runtime=False,
            claude_config_enabled=False,
            generated_at=NOW,
        )


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
def test_release_readiness_authority_fields_fail_closed(
    field: str,
    value: bool,
) -> None:
    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(manifest(), **{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "all_required_stages_present",
        "evidence_chain_ordered",
        "golden_master_preserved",
        "paper_only_preserved",
        "broker_submission_disabled",
        "external_provider_explicit_admission",
        "claude_advisory_only",
        "deterministic_replay_certified",
        "corruption_fail_closed_certified",
        "human_release_review_required",
        "release_ready_for_review",
    ],
)
def test_release_readiness_requires_every_guarantee(field: str) -> None:
    with pytest.raises(ValueError, match="guarantees are incomplete"):
        replace(manifest(), **{field: False})


def test_release_readiness_requires_a_verified_reliability_certification() -> None:
    with pytest.raises(ValueError, match="verified Gamma reliability certification"):
        build_gamma_release_readiness_manifest(
            golden_master_revision="26d38ee30",
            golden_master_tag="sigil-golden-master-v3.5.0",
            gamma_revision=GAMMA_REVISION,
            stage_evidence=stage_evidence(),
            reliability_certification=None,
            governance_evidence=governance_evidence(),
            claude_wired_into_production_runtime=False,
            claude_config_enabled=False,
            generated_at=NOW,
        )


def test_release_readiness_fails_closed_on_reliability_revision_mismatch() -> None:
    mismatched = reliability_certification("26d38ee30")

    with pytest.raises(ValueError, match="does not match the Gamma revision"):
        manifest(reliability_certification_override=mismatched)


def test_release_readiness_requires_verified_governance_evidence() -> None:
    with pytest.raises(
        GammaGovernanceEvidenceError, match="verified Gamma governance evidence"
    ):
        build_gamma_release_readiness_manifest(
            golden_master_revision="26d38ee30",
            golden_master_tag="sigil-golden-master-v3.5.0",
            gamma_revision=GAMMA_REVISION,
            stage_evidence=stage_evidence(),
            reliability_certification=reliability_certification(),
            governance_evidence=None,
            claude_wired_into_production_runtime=False,
            claude_config_enabled=False,
            generated_at=NOW,
        )


def test_release_readiness_fails_closed_on_governance_revision_mismatch() -> None:
    mismatched = governance_evidence(OTHER_REVISION)

    with pytest.raises(GammaGovernanceEvidenceError, match="does not match"):
        manifest(governance_evidence_override=mismatched)


@pytest.mark.parametrize("field", GAMMA_GOVERNANCE_INVARIANT_FIELDS)
def test_release_readiness_fails_closed_on_any_false_governance_invariant(
    field: str,
) -> None:
    with pytest.raises(GammaGovernanceEvidenceError, match="not all verified true"):
        manifest(governance_evidence_override=governance_evidence(**{field: False}))


def test_release_readiness_fails_closed_on_tampered_governance_evidence() -> None:
    valid = governance_evidence()
    broken = object.__new__(type(valid))
    for field in valid.__dataclass_fields__:
        object.__setattr__(broken, field, getattr(valid, field))
    object.__setattr__(broken, "paper_only_preserved", False)

    with pytest.raises(GammaGovernanceEvidenceError, match="not all verified true"):
        manifest(governance_evidence_override=broken)


def test_release_readiness_reports_truthful_claude_production_status() -> None:
    not_integrated = manifest(
        claude_wired_into_production_runtime=False,
        claude_config_enabled=True,
    )
    assert not_integrated.claude_subsystem_status == (
        GammaClaudeProductionStatus.NOT_PRODUCTION_INTEGRATED
    )
    assert not_integrated.claude_production_integrated is False
    assert not_integrated.claude_production_enabled is False

    integrated_disabled = manifest(
        claude_wired_into_production_runtime=True,
        claude_config_enabled=False,
    )
    assert integrated_disabled.claude_subsystem_status == (
        GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_DISABLED
    )
    assert integrated_disabled.claude_production_integrated is True
    assert integrated_disabled.claude_production_enabled is False

    integrated_enabled = manifest(
        claude_wired_into_production_runtime=True,
        claude_config_enabled=True,
    )
    assert integrated_enabled.claude_subsystem_status == (
        GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
    )
    assert integrated_enabled.claude_production_integrated is True
    assert integrated_enabled.claude_production_enabled is True


def test_implemented_and_tested_but_unwired_claude_cannot_claim_production_enabled() -> None:
    # Implemented + focus-tested (config_enabled=True, i.e. the flag is on
    # and the code path is exercised by unit tests) is not enough on its own
    # — without real production wiring it must never be represented as
    # fully production-enabled.
    result = manifest(
        claude_wired_into_production_runtime=False,
        claude_config_enabled=True,
    )

    assert result.claude_production_enabled is False
    assert result.claude_subsystem_status != (
        GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
    )


def test_release_readiness_rejects_inconsistent_claude_status_fields() -> None:
    valid = manifest()

    with pytest.raises(ValueError, match="inconsistent"):
        replace(valid, claude_production_integrated=not valid.claude_production_integrated)

    with pytest.raises(
        ValueError, match="production-enabled.*without also being production-integrated"
    ):
        replace(
            valid,
            claude_subsystem_status=(
                GammaClaudeProductionStatus.PRODUCTION_INTEGRATED_ENABLED
            ),
            claude_production_integrated=False,
            claude_production_enabled=True,
        )
