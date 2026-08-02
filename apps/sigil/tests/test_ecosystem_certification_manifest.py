from __future__ import annotations

import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ecosystem_certification import (
    ECOSYSTEM_CERTIFICATION_SCHEMA_VERSION,
    CertificationBlockId,
    EcosystemCertificationManifest,
    EcosystemCertificationValidationError,
    ExecutionAuthority,
    FinancialBoundary,
    build_authoritative_manifest,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def manifest() -> EcosystemCertificationManifest:
    return build_authoritative_manifest()


def test_authoritative_manifest_validates() -> None:
    result = manifest().validate(REPOSITORY_ROOT)

    assert result.valid is True
    assert result.errors == ()
    assert result.component_count == 12
    assert result.boundary_count == 13
    assert result.invariant_count == 14
    assert result.certification_block_count == 4
    assert result.paper_only_confirmed is True
    assert result.broker_submission_disabled_confirmed is True
    assert result.feature_freeze_confirmed is True


def test_manifest_identity_is_deterministic() -> None:
    first = manifest()
    second = manifest()

    assert first == second
    assert first.manifest_identity == second.manifest_identity
    assert first.expected_identity() == second.expected_identity()


def test_reordered_inputs_produce_same_identity() -> None:
    original = manifest()
    reordered = replace(
        original,
        components=tuple(reversed(original.components)),
        boundaries=tuple(reversed(original.boundaries)),
        invariants=tuple(reversed(original.invariants)),
        certification_blocks=tuple(reversed(original.certification_blocks)),
        manifest_identity="",
    )

    assert reordered.manifest_identity == original.manifest_identity
    assert reordered.canonical_projection() == original.canonical_projection()


def test_duplicate_component_is_rejected() -> None:
    original = manifest()

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="duplicate certification component ID",
    ):
        replace(
            original,
            components=original.components + (original.components[0],),
            manifest_identity="",
        )


def test_duplicate_boundary_is_rejected() -> None:
    original = manifest()

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="duplicate certification boundary ID",
    ):
        replace(
            original,
            boundaries=original.boundaries + (original.boundaries[0],),
            manifest_identity="",
        )


def test_duplicate_invariant_is_rejected() -> None:
    original = manifest()

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="duplicate certification invariant ID",
    ):
        replace(
            original,
            invariants=original.invariants + (original.invariants[0],),
            manifest_identity="",
        )


def test_unknown_schema_is_rejected() -> None:
    with pytest.raises(
        EcosystemCertificationValidationError,
        match="unsupported ecosystem certification schema version",
    ):
        replace(
            manifest(),
            schema_version=ECOSYSTEM_CERTIFICATION_SCHEMA_VERSION + 1,
            manifest_identity="",
        )


def test_missing_stage12_block_is_rejected() -> None:
    original = manifest()

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="Stage 12 block inventory is incomplete",
    ):
        replace(
            original,
            certification_blocks=tuple(
                block
                for block in original.certification_blocks
                if block.block_id != CertificationBlockId.STAGE_12D
            ),
            manifest_identity="",
        )


def test_missing_required_component_is_rejected() -> None:
    original = manifest()

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="missing required certification components",
    ):
        replace(
            original,
            components=tuple(
                component
                for component in original.components
                if component.component_id != "fleet-routing"
            ),
            manifest_identity="",
        )


def test_unauthorized_authority_is_rejected() -> None:
    original = manifest()
    bridge = next(
        component
        for component in original.components
        if component.component_id == "sigil-desktop-bridge"
    )
    invalid = replace(
        bridge,
        execution_authority=ExecutionAuthority.PROHIBITED,
    )

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="Sigil desktop bridge has unauthorized execution authority",
    ):
        replace(
            original,
            components=tuple(
                invalid if item.component_id == bridge.component_id else item
                for item in original.components
            ),
            manifest_identity="",
        )


def test_live_broker_submission_is_rejected() -> None:
    with pytest.raises(
        EcosystemCertificationValidationError,
        match="broker submission must remain disabled",
    ):
        replace(
            manifest(),
            broker_submission=True,
            manifest_identity="",
        )


def test_nonpaper_sigil_boundary_is_rejected() -> None:
    original = manifest()
    bridge = next(
        component
        for component in original.components
        if component.component_id == "sigil-desktop-bridge"
    )
    invalid = replace(
        bridge,
        financial_boundary=FinancialBoundary.NO_FINANCIAL_AUTHORITY,
    )

    with pytest.raises(
        EcosystemCertificationValidationError,
        match="Sigil desktop bridge must remain paper-only",
    ):
        replace(
            original,
            components=tuple(
                invalid if item.component_id == bridge.component_id else item
                for item in original.components
            ),
            manifest_identity="",
        )


def test_manifest_identity_tampering_is_detected() -> None:
    with pytest.raises(
        EcosystemCertificationValidationError,
        match="certification manifest identity mismatch",
    ):
        replace(
            manifest(),
            manifest_identity=f"sha256:{'0' * 64}",
        )


def test_repository_paths_validate() -> None:
    result = manifest().validate(REPOSITORY_ROOT)

    assert result.valid is True
    assert result.errors == ()


def test_nonexistent_path_fails_validation(tmp_path: Path) -> None:
    result = manifest().validate(tmp_path)

    assert result.valid is False
    assert result.errors


def test_manifest_construction_performs_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    build_authoritative_manifest()


def test_manifest_construction_performs_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess access attempted")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    build_authoritative_manifest()


def test_feature_expansion_is_prohibited() -> None:
    with pytest.raises(
        EcosystemCertificationValidationError,
        match="Stage 12 feature expansion must remain prohibited",
    ):
        replace(
            manifest(),
            feature_expansion_prohibited=False,
            manifest_identity="",
        )


def test_stage11_has_no_fabricated_historical_evidence() -> None:
    bridge = next(
        component
        for component in manifest().components
        if component.component_id == "sigil-desktop-bridge"
    )

    assert bridge.stage == "11"
    assert bridge.evidence_references == ()


def test_stage12a_does_not_claim_later_certification() -> None:
    current = manifest()

    assert current.certification_stage == "12A"
    assert "Golden Master" not in current.certification_stage
    assert {
        block.block_id
        for block in current.certification_blocks
    } == set(CertificationBlockId)
