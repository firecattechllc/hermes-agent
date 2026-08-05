from __future__ import annotations

import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ecosystem_replay_certification import (
    REPLAY_CERTIFICATION_SCHEMA_VERSION,
    ReplayCertificationStatus,
    ReplayCertificationValidationError,
    build_authoritative_replay_certification,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ZERO_DIGEST = f"sha256:{'0' * 64}"


def certification():
    return build_authoritative_replay_certification()


def test_authoritative_replay_certification_is_certified() -> None:
    result = certification()

    assert result.certification_stage == "12C"
    assert result.certified is True
    assert len(result.proofs) == 8
    assert result.paper_only is True
    assert result.broker_submission is False
    assert result.replay_external_side_effects is False
    assert result.authority_escalation is False
    assert result.feature_expansion_prohibited is True
    assert result.validate_paths(REPOSITORY_ROOT) == ()


def test_replay_certification_identity_is_deterministic() -> None:
    first = certification()
    second = certification()

    assert first == second
    assert first.certification_identity == second.certification_identity
    assert first.expected_identity() == second.expected_identity()
    assert first.to_json() == second.to_json()


def test_reordered_proofs_produce_same_identity() -> None:
    original = certification()
    reordered = replace(
        original,
        proofs=tuple(reversed(original.proofs)),
        certification_identity="",
    )

    assert reordered.proofs == original.proofs
    assert (
        reordered.certification_identity
        == original.certification_identity
    )
    assert (
        reordered.canonical_projection()
        == original.canonical_projection()
    )


def test_duplicate_replay_proof_fails_closed() -> None:
    original = certification()

    with pytest.raises(
        ReplayCertificationValidationError,
        match="duplicate replay proof ID",
    ):
        replace(
            original,
            proofs=original.proofs + (original.proofs[0],),
            certification_identity="",
        )


def test_missing_replay_proof_fails_closed() -> None:
    original = certification()

    with pytest.raises(
        ReplayCertificationValidationError,
        match="replay proof inventory mismatch",
    ):
        replace(
            original,
            proofs=original.proofs[:-1],
            certification_identity="",
        )


def test_uncertified_replay_proof_fails_certification() -> None:
    original = certification()
    first = original.proofs[0]
    uncertified = replace(
        first,
        recovery_proven=False,
        status=ReplayCertificationStatus.NOT_CERTIFIED,
    )

    with pytest.raises(
        ReplayCertificationValidationError,
        match="all Stage 12C replay proofs must be certified",
    ):
        replace(
            original,
            proofs=(uncertified,) + original.proofs[1:],
            certification_identity="",
        )


def test_contradictory_replay_status_fails_closed() -> None:
    first = certification().proofs[0]

    with pytest.raises(
        ReplayCertificationValidationError,
        match="replay certification status contradicts proof results",
    ):
        replace(
            first,
            recovery_proven=False,
            status=ReplayCertificationStatus.CERTIFIED,
        )


def test_nonpaper_certification_fails_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="Stage 12C must remain paper-only",
    ):
        replace(
            certification(),
            paper_only=False,
            certification_identity="",
        )


def test_broker_submission_fails_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="broker submission must remain disabled",
    ):
        replace(
            certification(),
            broker_submission=True,
            certification_identity="",
        )


def test_external_replay_side_effects_fail_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="replay must not invoke external side effects",
    ):
        replace(
            certification(),
            replay_external_side_effects=True,
            certification_identity="",
        )


def test_authority_escalation_fails_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="replay and recovery must not escalate authority",
    ):
        replace(
            certification(),
            authority_escalation=True,
            certification_identity="",
        )


def test_feature_expansion_fails_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="Stage 12 feature expansion must remain prohibited",
    ):
        replace(
            certification(),
            feature_expansion_prohibited=False,
            certification_identity="",
        )


def test_unknown_schema_fails_closed() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="unsupported replay certification schema version",
    ):
        replace(
            certification(),
            schema_version=REPLAY_CERTIFICATION_SCHEMA_VERSION + 1,
            certification_identity="",
        )


def test_source_boundary_identity_tampering_is_detected() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="Stage 12B boundary matrix identity mismatch",
    ):
        replace(
            certification(),
            source_boundary_matrix_identity=ZERO_DIGEST,
            certification_identity="",
        )


def test_certification_identity_tampering_is_detected() -> None:
    with pytest.raises(
        ReplayCertificationValidationError,
        match="replay certification identity mismatch",
    ):
        replace(
            certification(),
            certification_identity=ZERO_DIGEST,
        )


def test_missing_repository_references_are_reported(
    tmp_path: Path,
) -> None:
    errors = certification().validate_paths(tmp_path)

    assert errors
    assert tuple(sorted(errors)) == errors


def test_certification_construction_performs_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    build_authoritative_replay_certification()


def test_certification_construction_performs_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess access attempted")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    build_authoritative_replay_certification()
