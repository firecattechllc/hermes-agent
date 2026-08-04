"""Deterministic, tamper-evident evidence for Sigil's Stage-7 governance invariants.

Gamma release readiness asserts five governance invariants — paper-only
runtime, no broker submission, explicit external-provider admission, Claude
advisory-only, and mandatory human release review. Those claims must be
backed by a concrete, verified record rather than literals asserted at
build time. ``GammaGovernanceEvidence`` is that record: it is bound to a
specific ``gamma_revision``, self-validating (tampering with any field,
including via ``dataclasses.replace``, invalidates its own digest and
identity), and every invariant is an explicit boolean supplied by the
caller from real, checkable verification rather than defaulted.

Pure builders (readiness, review, sign-off) never inspect live runtime
state themselves — they require a verified ``GammaGovernanceEvidence``
record and fail closed if it is missing, of the wrong type, bound to a
different revision, tampered with, or asserts anything other than a clean
pass on every invariant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .registry import canonical_digest

GAMMA_GOVERNANCE_EVIDENCE_VERSION = 1
_REVISION = re.compile(r"^[0-9a-f]{9,40}$")

GAMMA_GOVERNANCE_INVARIANT_FIELDS = (
    "paper_only_preserved",
    "broker_submission_disabled",
    "external_provider_explicit_admission",
    "claude_advisory_only",
    "human_release_review_required",
)


class GammaGovernanceEvidenceError(ValueError):
    """Gamma governance evidence is missing, malformed, or inconsistent."""


@dataclass(frozen=True, slots=True)
class GammaGovernanceEvidence:
    """A single, self-verifying record of Sigil's Stage-7 governance invariants.

    ``evidence_id`` and ``evidence_digest`` are both recomputed from the
    remaining fields on every construction (including ``replace()``), so any
    mutation that is not accompanied by a matching digest recomputation is
    rejected — this is what makes tampering fail closed rather than silently
    propagate.
    """

    evidence_id: str
    gamma_revision: str
    paper_only_preserved: bool
    broker_submission_disabled: bool
    external_provider_explicit_admission: bool
    claude_advisory_only: bool
    human_release_review_required: bool
    verified_by: str
    verified_at: str
    evidence_digest: str

    def __post_init__(self) -> None:
        if _REVISION.fullmatch(self.gamma_revision) is None:
            raise GammaGovernanceEvidenceError(
                "governance evidence revision is invalid"
            )
        if not self.verified_by.strip():
            raise GammaGovernanceEvidenceError(
                "governance evidence verifier cannot be blank"
            )
        if not self.verified_at:
            raise GammaGovernanceEvidenceError(
                "governance evidence timestamp cannot be blank"
            )
        for name in GAMMA_GOVERNANCE_INVARIANT_FIELDS:
            if not isinstance(getattr(self, name), bool):
                raise GammaGovernanceEvidenceError(
                    f"governance evidence outcome {name!r} must be an explicit boolean"
                )
        if not self.evidence_digest.startswith("sha256:"):
            raise GammaGovernanceEvidenceError(
                "governance evidence digest must be SHA-256"
            )

        digest_hex = canonical_digest(_governance_payload(self))
        if self.evidence_digest != f"sha256:{digest_hex}":
            raise GammaGovernanceEvidenceError(
                "governance evidence digest does not match recorded evidence content"
            )
        if self.evidence_id != f"gamma-governance-evidence-{digest_hex}":
            raise GammaGovernanceEvidenceError(
                "governance evidence identity does not match recorded evidence content"
            )


def _governance_payload(evidence: GammaGovernanceEvidence) -> dict[str, object]:
    return {
        "version": GAMMA_GOVERNANCE_EVIDENCE_VERSION,
        "gamma_revision": evidence.gamma_revision,
        **{
            name: getattr(evidence, name)
            for name in GAMMA_GOVERNANCE_INVARIANT_FIELDS
        },
        "verified_by": evidence.verified_by,
        "verified_at": evidence.verified_at,
    }


def build_gamma_governance_evidence(
    *,
    gamma_revision: str,
    paper_only_preserved: bool,
    broker_submission_disabled: bool,
    external_provider_explicit_admission: bool,
    claude_advisory_only: bool,
    human_release_review_required: bool,
    verified_by: str,
    verified_at: str,
) -> GammaGovernanceEvidence:
    """Build a deterministic, self-verifying Gamma governance evidence record."""

    draft = {
        "version": GAMMA_GOVERNANCE_EVIDENCE_VERSION,
        "gamma_revision": gamma_revision,
        "paper_only_preserved": paper_only_preserved,
        "broker_submission_disabled": broker_submission_disabled,
        "external_provider_explicit_admission": external_provider_explicit_admission,
        "claude_advisory_only": claude_advisory_only,
        "human_release_review_required": human_release_review_required,
        "verified_by": verified_by,
        "verified_at": verified_at,
    }
    digest_hex = canonical_digest(draft)
    return GammaGovernanceEvidence(
        evidence_id=f"gamma-governance-evidence-{digest_hex}",
        gamma_revision=gamma_revision,
        paper_only_preserved=paper_only_preserved,
        broker_submission_disabled=broker_submission_disabled,
        external_provider_explicit_admission=external_provider_explicit_admission,
        claude_advisory_only=claude_advisory_only,
        human_release_review_required=human_release_review_required,
        verified_by=verified_by,
        verified_at=verified_at,
        evidence_digest=f"sha256:{digest_hex}",
    )


def require_verified_governance_evidence(
    evidence: GammaGovernanceEvidence,
    *,
    gamma_revision: str,
) -> GammaGovernanceEvidence:
    """Fail closed unless every Stage-7 governance invariant is verified true.

    Raises :class:`GammaGovernanceEvidenceError` for every non-affirmative
    case: wrong type, missing evidence, revision mismatch, and any invariant
    other than an explicit ``True``.
    """

    if not isinstance(evidence, GammaGovernanceEvidence):
        raise GammaGovernanceEvidenceError(
            "release readiness requires verified Gamma governance evidence"
        )
    if evidence.gamma_revision != gamma_revision:
        raise GammaGovernanceEvidenceError(
            "Gamma governance evidence revision does not match the certified "
            "revision"
        )
    unverified = [
        name
        for name in GAMMA_GOVERNANCE_INVARIANT_FIELDS
        if getattr(evidence, name) is not True
    ]
    if unverified:
        raise GammaGovernanceEvidenceError(
            "Gamma governance evidence invariants are not all verified true: "
            + ", ".join(unverified)
        )
    return evidence
