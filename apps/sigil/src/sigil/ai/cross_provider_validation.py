"""Deterministic advisory validation across governed provider outputs."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum

from .registry import canonical_digest

CROSS_PROVIDER_VALIDATION_VERSION = 1
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class CrossProviderValidationState(str, Enum):
    CONSISTENT = "consistent"
    REVIEW_REQUIRED = "review_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class ProviderClaim:
    claim_id: str
    provider_id: str
    model_id: str
    subject: str
    normalized_value: str
    evidence_references: tuple[str, ...]
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.claim_id or not self.provider_id or not self.model_id:
            raise ValueError("provider claim identities cannot be blank")
        if not self.subject.strip() or not self.normalized_value.strip():
            raise ValueError("provider claim content cannot be blank")
        if not self.evidence_references or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_references
        ):
            raise ValueError("provider claims require SHA-256 evidence references")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("provider claim confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class CrossProviderComparison:
    subject: str
    gemma_claim_id: str | None
    claude_claim_id: str | None
    state: str
    shared_evidence: tuple[str, ...]
    gemma_value: str | None
    claude_value: str | None


@dataclass(frozen=True, slots=True)
class CrossProviderValidationReport:
    validation_id: str
    target_revision: str
    target_digest: str
    gemma_provider_id: str
    claude_provider_id: str
    comparisons: tuple[CrossProviderComparison, ...]
    agreement_count: int
    disagreement_count: int
    missing_coverage_count: int
    state: CrossProviderValidationState
    human_review_required: bool
    validated_at: str
    validation_digest: str
    promotion_authorized: bool = False
    release_authority: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.validation_id or not self.target_revision:
            raise ValueError("validation identities cannot be blank")
        if _SHA256.fullmatch(self.target_digest) is None:
            raise ValueError("validation target digest must be SHA-256")
        if _SHA256.fullmatch(self.validation_digest) is None:
            raise ValueError("validation digest must be SHA-256")
        if min(
            self.agreement_count,
            self.disagreement_count,
            self.missing_coverage_count,
        ) < 0:
            raise ValueError("validation counts cannot be negative")
        if not self.validated_at:
            raise ValueError("validation timestamp cannot be blank")
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
            raise ValueError("cross-provider validation cannot receive authority")


def validate_cross_provider_claims(
    *,
    target_revision: str,
    target_digest: str,
    gemma_claims: tuple[ProviderClaim, ...],
    claude_claims: tuple[ProviderClaim, ...],
    validated_at: str,
) -> CrossProviderValidationReport:
    if not target_revision:
        raise ValueError("target revision cannot be blank")
    if _SHA256.fullmatch(target_digest) is None:
        raise ValueError("target digest must be SHA-256")
    if not gemma_claims or not claude_claims:
        raise ValueError("cross-provider validation requires both providers")
    if any(claim.provider_id != "local-gemma" for claim in gemma_claims):
        raise ValueError("Gemma claims must come from local-gemma")
    if any(claim.provider_id != "hermes-claude" for claim in claude_claims):
        raise ValueError("Claude claims must come from hermes-claude")

    gemma_by_subject = _index_claims(gemma_claims)
    claude_by_subject = _index_claims(claude_claims)
    comparisons = []

    for subject in sorted(set(gemma_by_subject) | set(claude_by_subject)):
        gemma = gemma_by_subject.get(subject)
        claude = claude_by_subject.get(subject)
        if gemma is None or claude is None:
            state = "missing_coverage"
            shared_evidence = ()
        else:
            shared_evidence = tuple(
                sorted(set(gemma.evidence_references) & set(claude.evidence_references))
            )
            if not shared_evidence:
                state = "insufficient_shared_evidence"
            elif gemma.normalized_value == claude.normalized_value:
                state = "agreement"
            else:
                state = "disagreement"
        comparisons.append(
            CrossProviderComparison(
                subject=subject,
                gemma_claim_id=None if gemma is None else gemma.claim_id,
                claude_claim_id=None if claude is None else claude.claim_id,
                state=state,
                shared_evidence=shared_evidence,
                gemma_value=None if gemma is None else gemma.normalized_value,
                claude_value=None if claude is None else claude.normalized_value,
            )
        )

    agreement_count = sum(item.state == "agreement" for item in comparisons)
    disagreement_count = sum(item.state == "disagreement" for item in comparisons)
    missing_count = sum(
        item.state in {"missing_coverage", "insufficient_shared_evidence"}
        for item in comparisons
    )

    if disagreement_count:
        report_state = CrossProviderValidationState.REVIEW_REQUIRED
    elif missing_count:
        report_state = CrossProviderValidationState.INSUFFICIENT_EVIDENCE
    else:
        report_state = CrossProviderValidationState.CONSISTENT

    human_review_required = report_state != CrossProviderValidationState.CONSISTENT
    validation_id = (
        "cross-provider-validation-"
        + canonical_digest(
            {
                "version": CROSS_PROVIDER_VALIDATION_VERSION,
                "target_revision": target_revision,
                "target_digest": target_digest,
                "gemma_claim_ids": tuple(item.claim_id for item in gemma_claims),
                "claude_claim_ids": tuple(item.claim_id for item in claude_claims),
            }
        )
    )
    payload = {
        "version": CROSS_PROVIDER_VALIDATION_VERSION,
        "validation_id": validation_id,
        "target_revision": target_revision,
        "target_digest": target_digest,
        "gemma_provider_id": "local-gemma",
        "claude_provider_id": "hermes-claude",
        "comparisons": [asdict(item) for item in comparisons],
        "agreement_count": agreement_count,
        "disagreement_count": disagreement_count,
        "missing_coverage_count": missing_count,
        "state": report_state.value,
        "human_review_required": human_review_required,
        "validated_at": validated_at,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }
    return CrossProviderValidationReport(
        validation_id=validation_id,
        target_revision=target_revision,
        target_digest=target_digest,
        gemma_provider_id="local-gemma",
        claude_provider_id="hermes-claude",
        comparisons=tuple(comparisons),
        agreement_count=agreement_count,
        disagreement_count=disagreement_count,
        missing_coverage_count=missing_count,
        state=report_state,
        human_review_required=human_review_required,
        validated_at=validated_at,
        validation_digest=f"sha256:{canonical_digest(payload)}",
    )


def _index_claims(claims: tuple[ProviderClaim, ...]) -> dict[str, ProviderClaim]:
    result = {}
    for claim in claims:
        if claim.subject in result:
            raise ValueError("provider claims cannot duplicate a subject")
        result[claim.subject] = claim
    return result
