from __future__ import annotations

import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ecosystem_boundary_certification import (
    BOUNDARY_CERTIFICATION_SCHEMA_VERSION,
    BoundaryCertificationStatus,
    BoundaryCertificationValidationError,
    build_authoritative_boundary_matrix,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ZERO_DIGEST = f"sha256:{'0' * 64}"


def matrix():
    return build_authoritative_boundary_matrix()


def test_authoritative_boundary_matrix_is_certified() -> None:
    result = matrix()

    assert result.certification_stage == "12B"
    assert result.certified is True
    assert len(result.proofs) == 13
    assert result.paper_only is True
    assert result.broker_submission is False
    assert result.feature_expansion_prohibited is True
    assert result.validate_paths(REPOSITORY_ROOT) == ()


def test_boundary_matrix_identity_is_deterministic() -> None:
    first = matrix()
    second = matrix()

    assert first == second
    assert first.matrix_identity == second.matrix_identity
    assert first.expected_identity() == second.expected_identity()
    assert first.to_json() == second.to_json()


def test_reordered_proofs_produce_same_identity() -> None:
    original = matrix()
    reordered = replace(
        original,
        proofs=tuple(reversed(original.proofs)),
        matrix_identity="",
    )

    assert reordered.proofs == original.proofs
    assert reordered.matrix_identity == original.matrix_identity
    assert reordered.canonical_projection() == original.canonical_projection()


def test_duplicate_boundary_proof_fails_closed() -> None:
    original = matrix()

    with pytest.raises(
        BoundaryCertificationValidationError,
        match="duplicate boundary proof ID",
    ):
        replace(
            original,
            proofs=original.proofs + (original.proofs[0],),
            matrix_identity="",
        )


def test_missing_boundary_proof_fails_closed() -> None:
    original = matrix()

    with pytest.raises(
        BoundaryCertificationValidationError,
        match="boundary proof inventory mismatch",
    ):
        replace(
            original,
            proofs=original.proofs[:-1],
            matrix_identity="",
        )


def test_uncertified_boundary_fails_matrix() -> None:
    original = matrix()
    first = original.proofs[0]
    uncertified = replace(
        first,
        denied_flow_proven=False,
        status=BoundaryCertificationStatus.NOT_CERTIFIED,
    )

    with pytest.raises(
        BoundaryCertificationValidationError,
        match="all Stage 12B boundaries must be certified",
    ):
        replace(
            original,
            proofs=(uncertified,) + original.proofs[1:],
            matrix_identity="",
        )


def test_contradictory_proof_status_fails_closed() -> None:
    first = matrix().proofs[0]

    with pytest.raises(
        BoundaryCertificationValidationError,
        match="boundary certification status contradicts proof results",
    ):
        replace(
            first,
            denied_flow_proven=False,
            status=BoundaryCertificationStatus.CERTIFIED,
        )


def test_broker_submission_fails_closed() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="broker submission must remain disabled",
    ):
        replace(
            matrix(),
            broker_submission=True,
            matrix_identity="",
        )


def test_nonpaper_boundary_matrix_fails_closed() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="Stage 12B must remain paper-only",
    ):
        replace(
            matrix(),
            paper_only=False,
            matrix_identity="",
        )


def test_feature_expansion_fails_closed() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="Stage 12 feature expansion must remain prohibited",
    ):
        replace(
            matrix(),
            feature_expansion_prohibited=False,
            matrix_identity="",
        )


def test_unknown_schema_fails_closed() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="unsupported boundary certification schema version",
    ):
        replace(
            matrix(),
            schema_version=BOUNDARY_CERTIFICATION_SCHEMA_VERSION + 1,
            matrix_identity="",
        )


def test_source_manifest_identity_tampering_is_detected() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="Stage 12A manifest identity mismatch",
    ):
        replace(
            matrix(),
            source_manifest_identity=ZERO_DIGEST,
            matrix_identity="",
        )


def test_matrix_identity_tampering_is_detected() -> None:
    with pytest.raises(
        BoundaryCertificationValidationError,
        match="boundary matrix identity mismatch",
    ):
        replace(
            matrix(),
            matrix_identity=ZERO_DIGEST,
        )


def test_missing_repository_reference_is_reported(
    tmp_path: Path,
) -> None:
    errors = matrix().validate_paths(tmp_path)

    assert errors
    assert tuple(sorted(errors)) == errors


def test_matrix_construction_performs_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    build_authoritative_boundary_matrix()


def test_matrix_construction_performs_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess access attempted")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    build_authoritative_boundary_matrix()
