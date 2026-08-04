from __future__ import annotations

from dataclasses import asdict, dataclass

from .gamma_evidence import GammaTestEvidence, require_passing_evidence
from .registry import canonical_digest

GAMMA_RELIABILITY_CERTIFICATION_VERSION = 2


@dataclass(frozen=True, slots=True)
class GammaReliabilityCertification:
    certification_id: str
    target_revision: str
    certified_domains: tuple[str, ...]
    replay_deterministic: bool
    corruption_fails_closed: bool
    malformed_output_fails_closed: bool
    partial_evidence_requires_review: bool
    timeout_failures_typed: bool
    explicit_recovery_required: bool
    evidence_id: str
    evidence_digest: str
    certified_at: str
    certification_digest: str
    promotion_authorized: bool = False
    release_authority: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.certification_id or not self.target_revision:
            raise ValueError("reliability certification identities cannot be blank")
        if not self.certified_domains:
            raise ValueError("reliability certification requires domains")
        if tuple(sorted(self.certified_domains)) != self.certified_domains:
            raise ValueError("certified domains must be sorted")
        if len(set(self.certified_domains)) != len(self.certified_domains):
            raise ValueError("certified domains must be unique")
        if not self.certified_at:
            raise ValueError("reliability certification timestamp cannot be blank")
        if not self.certification_digest.startswith("sha256:"):
            raise ValueError("reliability certification digest must be SHA-256")
        if not self.evidence_digest.startswith("sha256:"):
            raise ValueError("reliability certification evidence digest must be SHA-256")
        if not self.evidence_id:
            raise ValueError("reliability certification evidence id cannot be blank")
        if not all(
            (
                self.replay_deterministic,
                self.corruption_fails_closed,
                self.malformed_output_fails_closed,
                self.partial_evidence_requires_review,
                self.timeout_failures_typed,
                self.explicit_recovery_required,
            )
        ):
            raise ValueError("all reliability guarantees must be certified")
        if (
            self.promotion_authorized is not False
            or self.release_authority is not False
            or self.approval_authority is not False
            or self.execution_authorized is not False
            or self.broker_submission is not False
            or self.portfolio_mutation is not False
            or self.tool_execution is not False
            or self.paper_only is not True
        ):
            raise ValueError("Gamma reliability certification cannot receive authority")


def certify_gamma_reliability(
    *,
    target_revision: str,
    certified_at: str,
    evidence: GammaTestEvidence,
) -> GammaReliabilityCertification:
    """Certify Gamma reliability from a verified, passing test evidence record.

    Fails closed (raises ``GammaEvidenceError``, a ``ValueError`` subclass)
    whenever the evidence is missing, of the wrong type, bound to a
    different revision, unverified, not a clean pass, or has been tampered
    with (its digest/identity would no longer match its own content — see
    :class:`~sigil.ai.gamma_evidence.GammaTestEvidence`). The reliability
    guarantee booleans below are only reachable once that evidence has
    cleared every one of those checks; they are never assigned unconditionally.
    """

    require_passing_evidence(evidence, target_revision=target_revision)

    domains = tuple(
        sorted(
            {
                "claude_transport",
                "independent_inspection",
                "inspection_store",
                "cross_provider_validation",
                "validation_store",
                "certification_replay",
            }
        )
    )
    certification_id = (
        "gamma-reliability-certification-"
        + canonical_digest(
            {
                "version": GAMMA_RELIABILITY_CERTIFICATION_VERSION,
                "target_revision": target_revision,
                "certified_domains": domains,
                "evidence_id": evidence.evidence_id,
            }
        )
    )
    payload = {
        "version": GAMMA_RELIABILITY_CERTIFICATION_VERSION,
        "certification_id": certification_id,
        "target_revision": target_revision,
        "certified_domains": domains,
        "replay_deterministic": True,
        "corruption_fails_closed": True,
        "malformed_output_fails_closed": True,
        "partial_evidence_requires_review": True,
        "timeout_failures_typed": True,
        "explicit_recovery_required": True,
        "evidence_id": evidence.evidence_id,
        "evidence_digest": evidence.evidence_digest,
        "certified_at": certified_at,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }
    return GammaReliabilityCertification(
        certification_id=certification_id,
        target_revision=target_revision,
        certified_domains=domains,
        replay_deterministic=True,
        corruption_fails_closed=True,
        malformed_output_fails_closed=True,
        partial_evidence_requires_review=True,
        timeout_failures_typed=True,
        explicit_recovery_required=True,
        evidence_id=evidence.evidence_id,
        evidence_digest=evidence.evidence_digest,
        certified_at=certified_at,
        certification_digest=f"sha256:{canonical_digest(payload)}",
    )


def gamma_reliability_manifest(
    certification: GammaReliabilityCertification,
) -> dict[str, object]:
    return asdict(certification)
