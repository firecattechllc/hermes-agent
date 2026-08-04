"""Deterministic, tamper-evident test/CI evidence for Gamma certification.

Gamma certification claims (reliability, readiness, review, sign-off) must be
backed by a concrete, verifiable record of what was actually tested — not by
literals asserted at construction time. ``GammaTestEvidence`` is that record.
It is self-validating: any field tampered with after construction (including
via ``dataclasses.replace``) invalidates its own digest and identity, so a
forged or partially-edited evidence record fails closed instead of silently
carrying a false claim downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .registry import canonical_digest

GAMMA_TEST_EVIDENCE_VERSION = 1
_REVISION = re.compile(r"^[0-9a-f]{9,40}$")


class GammaEvidenceOutcome(str, Enum):
    """The result reported by the bound test/CI run."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_TESTED = "not_tested"
    UNKNOWN = "unknown"


class GammaEvidenceVerification(str, Enum):
    """Whether the evidence record itself has been independently verified."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class GammaEvidenceError(ValueError):
    """A Gamma test evidence record is missing, malformed, or inconsistent."""


@dataclass(frozen=True, slots=True)
class GammaTestEvidence:
    """A single, self-verifying test/CI evidence record.

    ``evidence_id`` and ``evidence_digest`` are both recomputed from the
    remaining fields on every construction (including ``replace()``), so any
    mutation that is not accompanied by a matching digest recomputation is
    rejected — this is what makes tampering fail closed rather than silently
    propagate.
    """

    evidence_id: str
    target_revision: str
    suite_id: str
    outcome: GammaEvidenceOutcome
    passed_count: int | None
    failed_count: int | None
    executed_at: str
    run_id: str
    verification_status: GammaEvidenceVerification
    evidence_digest: str

    def __post_init__(self) -> None:
        if _REVISION.fullmatch(self.target_revision) is None:
            raise GammaEvidenceError("evidence target revision is invalid")
        if not self.suite_id.strip():
            raise GammaEvidenceError("evidence suite id cannot be blank")
        if not self.run_id.strip():
            raise GammaEvidenceError("evidence run id cannot be blank")
        if not self.executed_at:
            raise GammaEvidenceError("evidence execution timestamp cannot be blank")
        if self.outcome not in set(GammaEvidenceOutcome):
            raise GammaEvidenceError("evidence outcome is malformed")
        if self.verification_status not in set(GammaEvidenceVerification):
            raise GammaEvidenceError("evidence verification status is malformed")
        if self.passed_count is not None and (
            isinstance(self.passed_count, bool) or self.passed_count < 0
        ):
            raise GammaEvidenceError("evidence passed count is invalid")
        if self.failed_count is not None and (
            isinstance(self.failed_count, bool) or self.failed_count < 0
        ):
            raise GammaEvidenceError("evidence failed count is invalid")
        if not self.evidence_digest.startswith("sha256:"):
            raise GammaEvidenceError("evidence digest must be SHA-256")

        digest_hex = canonical_digest(_evidence_payload(self))
        if self.evidence_digest != f"sha256:{digest_hex}":
            raise GammaEvidenceError(
                "evidence digest does not match recorded evidence content"
            )
        if self.evidence_id != f"gamma-test-evidence-{digest_hex}":
            raise GammaEvidenceError(
                "evidence identity does not match recorded evidence content"
            )


def _evidence_payload(evidence: GammaTestEvidence) -> dict[str, object]:
    return {
        "version": GAMMA_TEST_EVIDENCE_VERSION,
        "target_revision": evidence.target_revision,
        "suite_id": evidence.suite_id,
        "outcome": GammaEvidenceOutcome(evidence.outcome).value,
        "passed_count": evidence.passed_count,
        "failed_count": evidence.failed_count,
        "executed_at": evidence.executed_at,
        "run_id": evidence.run_id,
        "verification_status": GammaEvidenceVerification(
            evidence.verification_status
        ).value,
    }


def build_gamma_test_evidence(
    *,
    target_revision: str,
    suite_id: str,
    outcome: GammaEvidenceOutcome,
    passed_count: int | None,
    failed_count: int | None,
    executed_at: str,
    run_id: str,
    verification_status: GammaEvidenceVerification,
) -> GammaTestEvidence:
    """Build a deterministic, self-verifying Gamma test evidence record."""

    draft = {
        "version": GAMMA_TEST_EVIDENCE_VERSION,
        "target_revision": target_revision,
        "suite_id": suite_id,
        "outcome": GammaEvidenceOutcome(outcome).value,
        "passed_count": passed_count,
        "failed_count": failed_count,
        "executed_at": executed_at,
        "run_id": run_id,
        "verification_status": GammaEvidenceVerification(verification_status).value,
    }
    digest_hex = canonical_digest(draft)
    return GammaTestEvidence(
        evidence_id=f"gamma-test-evidence-{digest_hex}",
        target_revision=target_revision,
        suite_id=suite_id,
        outcome=GammaEvidenceOutcome(outcome),
        passed_count=passed_count,
        failed_count=failed_count,
        executed_at=executed_at,
        run_id=run_id,
        verification_status=GammaEvidenceVerification(verification_status),
        evidence_digest=f"sha256:{digest_hex}",
    )


def require_passing_evidence(
    evidence: GammaTestEvidence,
    *,
    target_revision: str,
) -> None:
    """Fail closed unless ``evidence`` is a verified, clean pass for the revision.

    Raises :class:`GammaEvidenceError` for every non-affirmative case: wrong
    type, missing evidence, revision mismatch, unverified evidence, and any
    outcome other than a clean ``PASSED`` with a positive pass count and zero
    failures.
    """

    if not isinstance(evidence, GammaTestEvidence):
        raise GammaEvidenceError("Gamma certification requires verified test evidence")
    if evidence.target_revision != target_revision:
        raise GammaEvidenceError(
            "Gamma evidence target revision does not match the certified revision"
        )
    if evidence.verification_status != GammaEvidenceVerification.VERIFIED:
        raise GammaEvidenceError("Gamma evidence has not been verified")
    if evidence.outcome != GammaEvidenceOutcome.PASSED:
        raise GammaEvidenceError(
            f"Gamma evidence outcome is not a pass: {evidence.outcome.value}"
        )
    if evidence.passed_count is None or evidence.failed_count is None:
        raise GammaEvidenceError("Gamma evidence is missing pass/fail counts")
    if evidence.failed_count != 0:
        raise GammaEvidenceError("Gamma evidence recorded failing checks")
    if evidence.passed_count <= 0:
        raise GammaEvidenceError("Gamma evidence recorded no passing checks")
