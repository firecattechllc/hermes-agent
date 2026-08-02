from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    GammaStageEvidence,
    build_gamma_release_readiness_manifest,
    gamma_release_readiness_projection,
)

NOW = "2026-08-03T00:00:00Z"


def evidence() -> tuple[GammaStageEvidence, ...]:
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


def manifest():
    return build_gamma_release_readiness_manifest(
        golden_master_revision="26d38ee30",
        golden_master_tag="sigil-golden-master-v3.5.0",
        gamma_revision="514d83be5",
        stage_evidence=evidence(),
        generated_at=NOW,
    )


def test_release_readiness_binds_golden_master_and_stages() -> None:
    result = manifest()

    assert result.golden_master_revision == "26d38ee30"
    assert result.golden_master_tag == "sigil-golden-master-v3.5.0"
    assert result.gamma_revision == "514d83be5"
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
            gamma_revision="514d83be5",
            stage_evidence=evidence()[:-1],
            generated_at=NOW,
        )

    with pytest.raises(ValueError, match="ordered"):
        build_gamma_release_readiness_manifest(
            golden_master_revision="26d38ee30",
            golden_master_tag="sigil-golden-master-v3.5.0",
            gamma_revision="514d83be5",
            stage_evidence=tuple(reversed(evidence())),
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
