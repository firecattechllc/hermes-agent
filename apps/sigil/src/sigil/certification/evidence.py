"""Deterministic, fail-closed certification evidence validation.

Sigil certification evidence -- independent Claude review artifacts under
``certification/claude-review/``, and Golden Master / Gamma verification
records under ``docs/certification/`` -- must never be read as passing
unless it explicitly, truthfully says so. This module classifies evidence
artifacts into an explicit status and only ever treats an approved review as
certifying: an execution error, a blocked run, a missing result, an unknown
status, or a malformed file all fail closed rather than silently defaulting
to "fine".

This module deliberately does not reuse or duplicate the structured,
in-process ``sigil.ai.claude_inspection`` / Gamma evidence machinery, which
models a different kind of evidence (programmatic provider invocations with
digest-bound request/response payloads). The artifacts here are committed
markdown records of external ``claude`` CLI review runs; the schema below
(a ``Status:`` field plus supporting metadata lines) is the minimal
structure needed to classify and gate that specific evidence genre.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class CertificationEvidenceStatus(str, Enum):
    """Every recognized state a certification evidence artifact can declare."""

    # Positive lifecycle states.
    REVIEW_REQUESTED = "review_requested"
    REVIEW_EXECUTED = "review_executed"
    REVIEW_SUCCEEDED = "review_succeeded"
    REVIEW_APPROVED = "review_approved"
    REVIEW_APPROVED_WITH_FINDINGS = "review_approved_with_non_blocking_findings"

    # Explicit negative / non-certifying outcomes.
    REVIEW_NOT_APPROVED = "review_not_approved"
    EXECUTION_ERROR = "execution_error"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_TESTED = "not_tested"

    # Parsing / integrity failure states.
    MISSING = "missing"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


# Only these statuses may ever be treated as certifying/passing evidence.
# Everything else -- including REVIEW_SUCCEEDED, which means the review ran
# to completion but has not itself rendered an approval verdict -- fails
# closed.
CERTIFYING_STATUSES = frozenset(
    {
        CertificationEvidenceStatus.REVIEW_APPROVED,
        CertificationEvidenceStatus.REVIEW_APPROVED_WITH_FINDINGS,
    }
)

_STATUS_VALUES = {member.value: member for member in CertificationEvidenceStatus}
_METADATA_LINE = re.compile(r"^-\s+([A-Za-z][A-Za-z0-9 /]*):\s*`?([^`]*?)`?\s*$")


class CertificationEvidenceError(ValueError):
    """A certification evidence artifact is missing, malformed, or fails closed."""


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")


def parse_metadata(text: str) -> dict[str, str]:
    """Extract ``- Key: value`` / ``- Key: `value` `` lines into a dict.

    Keys are slugified (lowercase, non-alphanumeric runs collapsed to a
    single underscore) so ``- Reviewed commit:`` and ``- reviewed-commit:``
    both resolve to ``reviewed_commit``.
    """
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        match = _METADATA_LINE.match(line.strip())
        if match is None:
            continue
        metadata[_slug(match.group(1))] = match.group(2).strip()
    return metadata


@dataclass(frozen=True, slots=True)
class CertificationEvidenceArtifact:
    path: Path
    status: CertificationEvidenceStatus
    metadata: Mapping[str, str]
    raw_text: str

    @property
    def reviewed_commit(self) -> str | None:
        return self.metadata.get("reviewed_commit") or None

    @property
    def certifying(self) -> bool:
        return self.status in CERTIFYING_STATUSES


def load_evidence_artifact(path: Path) -> CertificationEvidenceArtifact:
    """Parse a certification evidence markdown artifact, failing closed.

    A missing or empty file, or one without a recognized ``Status:`` field,
    is classified as ``MISSING``/``MALFORMED`` rather than raising --
    callers decide whether that classification is acceptable via
    :func:`validate_certification_evidence`, so a caller that forgets to
    check never silently treats absence-of-evidence as passing evidence.
    """
    if not path.exists() or not path.is_file():
        return CertificationEvidenceArtifact(
            path=path,
            status=CertificationEvidenceStatus.MISSING,
            metadata={},
            raw_text="",
        )
    text = path.read_text(encoding="utf-8")
    return parse_evidence_text(path, text)


def parse_evidence_text(path: Path, text: str) -> CertificationEvidenceArtifact:
    if not text.strip():
        return CertificationEvidenceArtifact(
            path=path,
            status=CertificationEvidenceStatus.MISSING,
            metadata={},
            raw_text=text,
        )
    metadata = parse_metadata(text)
    status_token = metadata.get("status")
    if status_token is None or status_token not in _STATUS_VALUES:
        return CertificationEvidenceArtifact(
            path=path,
            status=CertificationEvidenceStatus.MALFORMED,
            metadata=metadata,
            raw_text=text,
        )
    status = _STATUS_VALUES[status_token]
    certifying_token = metadata.get("certifying")
    if certifying_token is not None:
        declared_certifying = certifying_token.strip().lower() == "true"
        if declared_certifying != (status in CERTIFYING_STATUSES):
            # The artifact's self-declared "Certifying" claim disagrees with
            # what its own Status implies -- treat the whole record as
            # untrustworthy rather than picking a side.
            return CertificationEvidenceArtifact(
                path=path,
                status=CertificationEvidenceStatus.MALFORMED,
                metadata=metadata,
                raw_text=text,
            )
    return CertificationEvidenceArtifact(path=path, status=status, metadata=metadata, raw_text=text)


def validate_certification_evidence(
    artifact: CertificationEvidenceArtifact,
    *,
    expected_revision: str | None = None,
    expected_digest: str | None = None,
) -> None:
    """Fail closed unless ``artifact`` is truthfully certifying evidence.

    Raises :class:`CertificationEvidenceError` for anything short of an
    explicit approval, a revision mismatch against ``expected_revision``, or
    a digest mismatch against ``expected_digest`` when one is required.
    """
    if artifact.status not in CERTIFYING_STATUSES:
        raise CertificationEvidenceError(
            f"{artifact.path}: evidence status {artifact.status.value!r} is not "
            "certifying (must be 'review_approved' or "
            "'review_approved_with_non_blocking_findings')"
        )
    if expected_revision is not None:
        recorded = artifact.reviewed_commit
        if not recorded:
            raise CertificationEvidenceError(
                f"{artifact.path}: evidence does not record a reviewed commit"
            )
        if not (expected_revision.startswith(recorded) or recorded.startswith(expected_revision)):
            raise CertificationEvidenceError(
                f"{artifact.path}: evidence reviewed commit {recorded!r} does not "
                f"match expected revision {expected_revision!r}"
            )
    if expected_digest is not None:
        recorded_digest = artifact.metadata.get("content_digest") or artifact.metadata.get(
            "evidence_digest"
        )
        if not recorded_digest:
            raise CertificationEvidenceError(
                f"{artifact.path}: expected a content digest but the artifact records none"
            )
        if recorded_digest != expected_digest:
            raise CertificationEvidenceError(
                f"{artifact.path}: evidence digest {recorded_digest!r} does not match "
                f"expected digest {expected_digest!r}"
            )


FLEET_FAILOVER_REQUIREMENT_ID = "sigil-fleet-failover-v1"
_HOME_ASSISTANT_MARKERS = (
    "homeassistant",
    "home_assistant",
    "home assistant",
    "test_ha_integration",
)


def validate_fleet_failover_evidence_source(suite_identity: str) -> None:
    """Reject Home Assistant integration coverage as fleet failover evidence.

    ``tests/integration/test_ha_integration.py`` exercises the Home
    Assistant smart-home platform adapter (see its own module docstring),
    not Sigil fleet high-availability or failover behavior. A certification
    document that cites "HA" test coverage for fleet purposes is conflating
    the two; this check makes that conflation fail instead of passing
    silently.
    """
    normalized = suite_identity.lower()
    if any(marker in normalized for marker in _HOME_ASSISTANT_MARKERS):
        raise CertificationEvidenceError(
            f"{suite_identity!r} is Home Assistant integration coverage and cannot "
            "satisfy a Sigil fleet high-availability/failover evidence requirement "
            f"({FLEET_FAILOVER_REQUIREMENT_ID})"
        )
