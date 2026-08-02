"""Strict Stage 12D Golden Master readiness certification.

Stage 12D aggregates immutable Stage 12A, 12B, and 12C identities with
explicitly supplied validation results. It never executes tests, builds,
network requests, subprocesses, broker actions, or repository mutations.

Golden Master promotion remains a separate operator action.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from sigil.ai.registry import canonical_digest
from sigil.ecosystem_boundary_certification import (
    build_authoritative_boundary_matrix,
)
from sigil.ecosystem_certification import build_authoritative_manifest
from sigil.ecosystem_replay_certification import (
    build_authoritative_replay_certification,
)

GOLDEN_MASTER_SCHEMA_VERSION = 1

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class GoldenMasterDecision(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class ValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class GoldenMasterValidationError(ValueError):
    """Stage 12D readiness input failed closed."""


_REQUIRED_VALIDATION_IDS = frozenset(
    {
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
    }
)

_REQUIRED_ARTIFACT_IDS = frozenset(
    {
        "stage12a-manifest",
        "stage12b-boundary-matrix",
        "stage12c-replay-certification",
        "stage12-certification-document",
    }
)


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise GoldenMasterValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise GoldenMasterValidationError(f"malformed {label}")


def _require_reference(value: str, label: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or value.startswith(".")
    ):
        raise GoldenMasterValidationError(
            f"{label} must be a repository-relative reference"
        )


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    validation_id: str
    command: str
    status: ValidationStatus
    passed_count: int | None = None
    failed_count: int | None = None
    result_summary: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.validation_id, "validation ID")

        if not self.command.strip():
            raise GoldenMasterValidationError(
                "validation command is required"
            )
        if not isinstance(self.status, ValidationStatus):
            raise GoldenMasterValidationError(
                "unknown validation status"
            )
        if not self.result_summary.strip():
            raise GoldenMasterValidationError(
                "validation result summary is required"
            )

        for count in (self.passed_count, self.failed_count):
            if count is not None and count < 0:
                raise GoldenMasterValidationError(
                    "validation counts cannot be negative"
                )

        if self.status == ValidationStatus.PASSED:
            if self.failed_count not in {None, 0}:
                raise GoldenMasterValidationError(
                    "passed validation cannot report failures"
                )
        elif (
            self.status == ValidationStatus.FAILED
            and self.failed_count in {None, 0}
        ):
            raise GoldenMasterValidationError(
                "failed validation must report a failure"
            )


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    repository_reference: str
    sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.artifact_id, "artifact ID")
        _require_reference(
            self.repository_reference,
            "artifact repository reference",
        )
        _require_digest(self.sha256, "artifact digest")


@dataclass(frozen=True, slots=True)
class GoldenMasterReadiness:
    stage12a_identity: str
    stage12b_identity: str
    stage12c_identity: str
    validations: tuple[ValidationRecord, ...]
    artifacts: tuple[ArtifactRecord, ...]
    unresolved_blockers: tuple[str, ...]
    expected_working_tree_paths: tuple[str, ...]
    paper_only: bool
    broker_submission: bool
    feature_expansion_prohibited: bool
    automatic_promotion: bool
    certification_stage: str = "12D"
    schema_version: int = GOLDEN_MASTER_SCHEMA_VERSION
    decision: GoldenMasterDecision = GoldenMasterDecision.NOT_READY
    readiness_identity: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validations",
            tuple(
                sorted(
                    self.validations,
                    key=lambda item: item.validation_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "artifacts",
            tuple(
                sorted(
                    self.artifacts,
                    key=lambda item: item.artifact_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "unresolved_blockers",
            tuple(sorted(set(self.unresolved_blockers))),
        )
        object.__setattr__(
            self,
            "expected_working_tree_paths",
            tuple(sorted(set(self.expected_working_tree_paths))),
        )

        if self.schema_version != GOLDEN_MASTER_SCHEMA_VERSION:
            raise GoldenMasterValidationError(
                "unsupported Golden Master schema version"
            )
        if self.certification_stage != "12D":
            raise GoldenMasterValidationError(
                "Golden Master certification stage must be 12D"
            )

        for identity, label in (
            (self.stage12a_identity, "Stage 12A identity"),
            (self.stage12b_identity, "Stage 12B identity"),
            (self.stage12c_identity, "Stage 12C identity"),
        ):
            _require_digest(identity, label)

        validation_ids = tuple(
            item.validation_id for item in self.validations
        )
        artifact_ids = tuple(item.artifact_id for item in self.artifacts)

        if len(validation_ids) != len(set(validation_ids)):
            raise GoldenMasterValidationError(
                "duplicate validation ID"
            )
        if len(artifact_ids) != len(set(artifact_ids)):
            raise GoldenMasterValidationError(
                "duplicate artifact ID"
            )

        if set(validation_ids) != _REQUIRED_VALIDATION_IDS:
            missing = sorted(
                _REQUIRED_VALIDATION_IDS - set(validation_ids)
            )
            unexpected = sorted(
                set(validation_ids) - _REQUIRED_VALIDATION_IDS
            )
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise GoldenMasterValidationError(
                "validation inventory mismatch: " + "; ".join(details)
            )

        if set(artifact_ids) != _REQUIRED_ARTIFACT_IDS:
            missing = sorted(
                _REQUIRED_ARTIFACT_IDS - set(artifact_ids)
            )
            unexpected = sorted(
                set(artifact_ids) - _REQUIRED_ARTIFACT_IDS
            )
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise GoldenMasterValidationError(
                "artifact inventory mismatch: " + "; ".join(details)
            )

        manifest = build_authoritative_manifest()
        boundary = build_authoritative_boundary_matrix()
        replay = build_authoritative_replay_certification()

        if self.stage12a_identity != manifest.manifest_identity:
            raise GoldenMasterValidationError(
                "Stage 12A identity mismatch"
            )
        if self.stage12b_identity != boundary.matrix_identity:
            raise GoldenMasterValidationError(
                "Stage 12B identity mismatch"
            )
        if self.stage12c_identity != replay.certification_identity:
            raise GoldenMasterValidationError(
                "Stage 12C identity mismatch"
            )

        for reference in self.expected_working_tree_paths:
            _require_reference(reference, "expected working-tree path")

        expected_decision = self.expected_decision()
        if self.decision != expected_decision:
            raise GoldenMasterValidationError(
                "Golden Master decision contradicts readiness evidence"
            )

        expected_identity = self.expected_identity()
        if (
            self.readiness_identity
            and self.readiness_identity != expected_identity
        ):
            raise GoldenMasterValidationError(
                "Golden Master readiness identity mismatch"
            )
        if not self.readiness_identity:
            object.__setattr__(
                self,
                "readiness_identity",
                expected_identity,
            )

    def expected_decision(self) -> GoldenMasterDecision:
        ready = (
            self.paper_only
            and not self.broker_submission
            and self.feature_expansion_prohibited
            and not self.automatic_promotion
            and not self.unresolved_blockers
            and all(
                item.status == ValidationStatus.PASSED
                for item in self.validations
            )
        )
        return (
            GoldenMasterDecision.READY
            if ready
            else GoldenMasterDecision.NOT_READY
        )

    @property
    def ready(self) -> bool:
        return self.decision == GoldenMasterDecision.READY

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("readiness_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def canonical_projection(self) -> dict[str, object]:
        return asdict(self)

    def validate_artifacts(
        self,
        repository_root: Path,
    ) -> tuple[str, ...]:
        errors: list[str] = []

        for artifact in self.artifacts:
            path = repository_root / artifact.repository_reference
            if not path.is_file():
                errors.append(
                    f"missing Golden Master artifact: "
                    f"{artifact.repository_reference}"
                )
                continue

            actual = (
                "sha256:"
                + hashlib.sha256(path.read_bytes()).hexdigest()
            )
            if actual != artifact.sha256:
                errors.append(
                    f"Golden Master artifact digest mismatch: "
                    f"{artifact.repository_reference}"
                )

        return tuple(sorted(set(errors)))

    def to_json(self) -> str:
        return json.dumps(
            self.canonical_projection(),
            indent=2,
            sort_keys=True,
        ) + "\n"
