from __future__ import annotations

import hashlib
import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sigil.ecosystem_boundary_certification import (
    build_authoritative_boundary_matrix,
)
from sigil.ecosystem_certification import build_authoritative_manifest
from sigil.ecosystem_golden_master import (
    GOLDEN_MASTER_SCHEMA_VERSION,
    ArtifactRecord,
    GoldenMasterDecision,
    GoldenMasterReadiness,
    GoldenMasterValidationError,
    ValidationRecord,
    ValidationStatus,
)
from sigil.ecosystem_replay_certification import (
    build_authoritative_replay_certification,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ZERO_DIGEST = f"sha256:{'0' * 64}"

EXPECTED_PATHS = (
    "apps/sigil/src/sigil/ecosystem_golden_master.py",
    "apps/sigil/tests/test_ecosystem_golden_master.py",
    "docs/sigil/ECOSYSTEM_STAGE12_CERTIFICATION.md",
    "docs/sigil/evidence/ECOSYSTEM_STAGE12D_GOLDEN_MASTER_READINESS.json",
)


def validation(
    validation_id: str,
    *,
    status: ValidationStatus = ValidationStatus.PASSED,
) -> ValidationRecord:
    return ValidationRecord(
        validation_id=validation_id,
        command=f"certify {validation_id}",
        status=status,
        passed_count=1 if status == ValidationStatus.PASSED else None,
        failed_count=0 if status == ValidationStatus.PASSED else 1,
        result_summary=(
            "validation passed"
            if status == ValidationStatus.PASSED
            else "validation failed"
        ),
    )


def validations() -> tuple[ValidationRecord, ...]:
    identifiers = (
        "stage12-focused-tests",
        "backend-critical-ruff",
        "backend-compileall",
        "backend-import-paper-runtime",
        "backend-full-pytest",
        "desktop-typecheck",
        "desktop-tests",
        "desktop-lint",
        "packaged-backend",
        "desktop-production-build",
        "release-guardian-tests",
        "release-verification",
        "paper-only-source-scan",
        "git-diff-check",
        "expected-working-tree-only",
    )
    return tuple(validation(item) for item in identifiers)


def artifact(
    artifact_id: str,
    reference: str,
) -> ArtifactRecord:
    path = REPOSITORY_ROOT / reference
    return ArtifactRecord(
        artifact_id=artifact_id,
        repository_reference=reference,
        sha256=f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
    )


def artifacts() -> tuple[ArtifactRecord, ...]:
    return (
        artifact(
            "stage12a-manifest",
            "docs/sigil/evidence/"
            "ECOSYSTEM_STAGE12A_CERTIFICATION_MANIFEST.json",
        ),
        artifact(
            "stage12b-boundary-matrix",
            "docs/sigil/evidence/"
            "ECOSYSTEM_STAGE12B_BOUNDARY_MATRIX.json",
        ),
        artifact(
            "stage12c-replay-certification",
            "docs/sigil/evidence/"
            "ECOSYSTEM_STAGE12C_REPLAY_CERTIFICATION.json",
        ),
        artifact(
            "stage12-certification-document",
            "docs/sigil/ECOSYSTEM_STAGE12_CERTIFICATION.md",
        ),
    )


def readiness() -> GoldenMasterReadiness:
    return GoldenMasterReadiness(
        stage12a_identity=(
            build_authoritative_manifest().manifest_identity
        ),
        stage12b_identity=(
            build_authoritative_boundary_matrix().matrix_identity
        ),
        stage12c_identity=(
            build_authoritative_replay_certification()
            .certification_identity
        ),
        validations=validations(),
        artifacts=artifacts(),
        unresolved_blockers=(),
        expected_working_tree_paths=EXPECTED_PATHS,
        paper_only=True,
        broker_submission=False,
        feature_expansion_prohibited=True,
        automatic_promotion=False,
        decision=GoldenMasterDecision.READY,
    )


def test_authoritative_readiness_is_ready() -> None:
    result = readiness()

    assert result.ready is True
    assert result.decision == GoldenMasterDecision.READY
    assert result.paper_only is True
    assert result.broker_submission is False
    assert result.automatic_promotion is False
    assert result.validate_artifacts(REPOSITORY_ROOT) == ()


def test_readiness_identity_is_deterministic() -> None:
    first = readiness()
    second = readiness()

    assert first == second
    assert first.readiness_identity == second.readiness_identity
    assert first.expected_identity() == second.expected_identity()
    assert first.to_json() == second.to_json()


def test_reordered_records_produce_same_identity() -> None:
    original = readiness()
    reordered = replace(
        original,
        validations=tuple(reversed(original.validations)),
        artifacts=tuple(reversed(original.artifacts)),
        expected_working_tree_paths=tuple(
            reversed(original.expected_working_tree_paths)
        ),
        readiness_identity="",
    )

    assert reordered == original


def test_failed_validation_requires_not_ready() -> None:
    original = readiness()
    failed = replace(
        original.validations[0],
        status=ValidationStatus.FAILED,
        passed_count=None,
        failed_count=1,
        result_summary="validation failed",
    )

    with pytest.raises(
        GoldenMasterValidationError,
        match="decision contradicts readiness evidence",
    ):
        replace(
            original,
            validations=(failed,) + original.validations[1:],
            readiness_identity="",
        )


def test_failed_validation_can_be_not_ready() -> None:
    original = readiness()
    failed = replace(
        original.validations[0],
        status=ValidationStatus.FAILED,
        passed_count=None,
        failed_count=1,
        result_summary="validation failed",
    )
    result = replace(
        original,
        validations=(failed,) + original.validations[1:],
        decision=GoldenMasterDecision.NOT_READY,
        readiness_identity="",
    )

    assert result.ready is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paper_only", False),
        ("broker_submission", True),
        ("feature_expansion_prohibited", False),
        ("automatic_promotion", True),
        ("unresolved_blockers", ("release blocker",)),
    ],
)
def test_blocking_condition_cannot_return_ready(
    field: str,
    value: object,
) -> None:
    with pytest.raises(
        GoldenMasterValidationError,
        match="decision contradicts readiness evidence",
    ):
        replace(
            readiness(),
            **{
                field: value,
                "readiness_identity": "",
            },
        )


def test_duplicate_validation_fails_closed() -> None:
    original = readiness()

    with pytest.raises(
        GoldenMasterValidationError,
        match="duplicate validation ID",
    ):
        replace(
            original,
            validations=original.validations + (
                original.validations[0],
            ),
            readiness_identity="",
        )


def test_missing_validation_fails_closed() -> None:
    original = readiness()

    with pytest.raises(
        GoldenMasterValidationError,
        match="validation inventory mismatch",
    ):
        replace(
            original,
            validations=original.validations[:-1],
            readiness_identity="",
        )


def test_duplicate_artifact_fails_closed() -> None:
    original = readiness()

    with pytest.raises(
        GoldenMasterValidationError,
        match="duplicate artifact ID",
    ):
        replace(
            original,
            artifacts=original.artifacts + (
                original.artifacts[0],
            ),
            readiness_identity="",
        )


def test_stage_identity_tampering_is_detected() -> None:
    with pytest.raises(
        GoldenMasterValidationError,
        match="Stage 12C identity mismatch",
    ):
        replace(
            readiness(),
            stage12c_identity=ZERO_DIGEST,
            readiness_identity="",
        )


def test_readiness_identity_tampering_is_detected() -> None:
    with pytest.raises(
        GoldenMasterValidationError,
        match="Golden Master readiness identity mismatch",
    ):
        replace(
            readiness(),
            readiness_identity=ZERO_DIGEST,
        )


def test_unknown_schema_fails_closed() -> None:
    with pytest.raises(
        GoldenMasterValidationError,
        match="unsupported Golden Master schema version",
    ):
        replace(
            readiness(),
            schema_version=GOLDEN_MASTER_SCHEMA_VERSION + 1,
            readiness_identity="",
        )


def test_missing_artifacts_are_reported(tmp_path: Path) -> None:
    errors = readiness().validate_artifacts(tmp_path)

    assert errors
    assert tuple(sorted(errors)) == errors


def test_construction_performs_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    readiness()


def test_construction_performs_no_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess access attempted")

    monkeypatch.setattr(subprocess, "run", fail_subprocess)
    readiness()
