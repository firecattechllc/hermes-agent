"""Deterministic, tamper-evident test/CI evidence for Gamma certification.

Gamma certification claims (reliability, readiness, review, sign-off) must be
backed by a concrete, verifiable record of what was actually tested — not by
literals asserted at construction time. ``GammaTestEvidence`` is that record,
and its ``reliability_outcomes`` field carries an explicit, per-guarantee
verified result (see ``GammaReliabilityOutcomes``) rather than a single
pass/fail summary that a certifier would otherwise have to take on faith.
The whole record is self-validating: any field tampered with after
construction (including via ``dataclasses.replace``) invalidates its own
digest and identity, so a forged or partially-edited evidence record fails
closed instead of silently carrying a false claim downstream.
``require_expected_suite`` additionally binds certification to a specific,
versioned suite identifier so an unrelated passing suite cannot certify
guarantees it never exercised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .registry import canonical_digest

GAMMA_TEST_EVIDENCE_VERSION = 2
_REVISION = re.compile(r"^[0-9a-f]{9,40}$")

GAMMA_RELIABILITY_OUTCOME_FIELDS = (
    "replay_deterministic",
    "corruption_fails_closed",
    "malformed_output_fails_closed",
    "partial_evidence_requires_review",
    "timeout_failures_typed",
    "explicit_recovery_required",
)


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
class GammaReliabilityOutcomes:
    """Explicit, per-guarantee verified outcomes bound into a test evidence record.

    Each field is a real, independently reported result for one Gamma
    reliability guarantee — not a placeholder. Every field is required (no
    defaults), so an incomplete report fails at construction with
    ``TypeError`` rather than silently defaulting a guarantee to true or
    false. Booleans are checked strictly (``isinstance``) so truthy-but-wrong
    types (e.g. ``1``) cannot masquerade as a verified outcome.
    """

    replay_deterministic: bool
    corruption_fails_closed: bool
    malformed_output_fails_closed: bool
    partial_evidence_requires_review: bool
    timeout_failures_typed: bool
    explicit_recovery_required: bool

    def __post_init__(self) -> None:
        for name in GAMMA_RELIABILITY_OUTCOME_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise GammaEvidenceError(
                    f"reliability outcome {name!r} must be an explicit boolean"
                )

    def as_payload(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in GAMMA_RELIABILITY_OUTCOME_FIELDS}


@dataclass(frozen=True, slots=True)
class GammaTestEvidence:
    """A single, self-verifying test/CI evidence record.

    ``evidence_id`` and ``evidence_digest`` are both recomputed from the
    remaining fields (including ``reliability_outcomes``) on every
    construction (including ``replace()``), so any mutation that is not
    accompanied by a matching digest recomputation is rejected — this is
    what makes tampering fail closed rather than silently propagate.
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
    reliability_outcomes: GammaReliabilityOutcomes
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
        if not isinstance(self.reliability_outcomes, GammaReliabilityOutcomes):
            raise GammaEvidenceError(
                "evidence is missing explicit reliability outcomes"
            )
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
        "reliability_outcomes": evidence.reliability_outcomes.as_payload(),
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
    reliability_outcomes: GammaReliabilityOutcomes,
) -> GammaTestEvidence:
    """Build a deterministic, self-verifying Gamma test evidence record."""

    if not isinstance(reliability_outcomes, GammaReliabilityOutcomes):
        raise GammaEvidenceError("evidence is missing explicit reliability outcomes")

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
        "reliability_outcomes": reliability_outcomes.as_payload(),
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
        reliability_outcomes=reliability_outcomes,
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


def require_expected_suite(
    evidence: GammaTestEvidence,
    *,
    expected_suite_id: str,
) -> None:
    """Fail closed unless ``evidence`` was produced by the expected suite.

    Without this check an arbitrary, unrelated passing suite (any
    ``GammaTestEvidence`` with ``outcome=PASSED``) could certify guarantees
    it never actually exercised. Binding certification to a specific,
    versioned suite identifier closes that gap.
    """

    if evidence.suite_id != expected_suite_id:
        raise GammaEvidenceError(
            "Gamma evidence suite id does not match the expected certification "
            f"suite: expected {expected_suite_id!r}, got {evidence.suite_id!r}"
        )


def require_reliability_outcomes(
    evidence: GammaTestEvidence,
) -> GammaReliabilityOutcomes:
    """Fail closed unless every reliability guarantee outcome is verified true.

    This is what lets a certifier derive its guarantee fields directly from
    ``evidence`` instead of assigning literals: a missing, false, or
    malformed outcome is rejected here, before a certification can be built
    from it.
    """

    outcomes = evidence.reliability_outcomes
    if not isinstance(outcomes, GammaReliabilityOutcomes):
        raise GammaEvidenceError(
            "Gamma evidence is missing verified reliability outcomes"
        )
    unverified = [
        name
        for name in GAMMA_RELIABILITY_OUTCOME_FIELDS
        if getattr(outcomes, name) is not True
    ]
    if unverified:
        raise GammaEvidenceError(
            "Gamma evidence reliability outcomes are not all verified true: "
            + ", ".join(unverified)
        )
    return outcomes
