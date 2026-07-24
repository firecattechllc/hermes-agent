"""Side-effect-free orchestration and read-only inspection for research dossiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sigil.accounting.models import canonical_digest, timestamp
from sigil.integrations.providers.models import FinancialDataValidationError

from .completeness import evaluate_completeness
from .models import (
    BusinessProfile,
    FinancialHistory,
    ResearchConclusion,
    ResearchDossier,
    ResearchDossierProvenance,
    ResearchEntityIdentity,
    ResearchEvidenceClaim,
    ResearchEvidenceConflict,
    ResearchEvidenceGap,
    ResearchQuestion,
    ResearchSecurityIdentity,
    validate_identity_binding,
)
from .policy import ResearchDossierPolicy


@dataclass(frozen=True, slots=True)
class DossierInput:
    entity: ResearchEntityIdentity
    security: ResearchSecurityIdentity | None
    claims: tuple[ResearchEvidenceClaim, ...]
    section_claims: tuple[tuple[str, tuple[str, ...]], ...]
    conflicts: tuple[ResearchEvidenceConflict, ...] = ()
    gaps: tuple[ResearchEvidenceGap, ...] = ()
    business_profile: BusinessProfile | None = None
    financial_history: FinancialHistory | None = None
    filing_count: int = 0


def _questions(
    gaps: tuple[ResearchEvidenceGap, ...],
    conflicts: tuple[ResearchEvidenceConflict, ...],
    now: datetime,
) -> tuple[ResearchQuestion, ...]:
    result = [
        ResearchQuestion(
            f"gap-{gap.gap_id}",
            gap.section,
            f"What verified evidence resolves {gap.reason}?",
            gap.reason,
            gap.materiality,
            gap.required_evidence_type or "verified_evidence",
            now,
            related_gap_ids=(gap.gap_id,),
        )
        for gap in gaps
        if not gap.resolved
    ]
    result.extend(
        ResearchQuestion(
            f"conflict-{conflict.conflict_id}",
            "financial_history"
            if "financial" in conflict.category or "period" in conflict.category
            else "identity",
            f"What governed evidence resolves conflict in {conflict.affected_field}?",
            "conflicting_evidence",
            conflict.materiality,
            "verified_source",
            now,
            related_conflict_ids=(conflict.conflict_id,),
        )
        for conflict in conflicts
        if conflict.resolution_status != "resolved"
    )
    return tuple(sorted(result, key=lambda item: item.question_identity))


def build_dossier(
    dossier_input: DossierInput,
    policy: ResearchDossierPolicy,
    *,
    constructed_at: datetime,
    construction_started_at: datetime | None = None,
    conclusions: tuple[ResearchConclusion, ...] = (),
) -> ResearchDossier:
    """Build one immutable report without network, storage, graph, broker, or ledger access."""
    timestamp(constructed_at, "constructed_at")
    validate_identity_binding(dossier_input.entity, dossier_input.security)
    if construction_started_at is not None:
        timestamp(construction_started_at, "construction_started_at")
        if constructed_at < construction_started_at:
            raise FinancialDataValidationError("construction timestamps are invalid")
        if constructed_at - construction_started_at > policy.maximum_construction_duration:
            raise FinancialDataValidationError("dossier construction duration exceeds policy")
    claims = tuple(sorted(dossier_input.claims, key=lambda item: item.claim_identity))
    if len(claims) > policy.maximum_total_evidence:
        raise FinancialDataValidationError("maximum total evidence exceeded")
    if len({item.claim_identity for item in claims}) != len(claims):
        raise FinancialDataValidationError("duplicate claim identity")
    claim_by_id = {claim.claim_id: claim for claim in claims}
    sections: dict[str, tuple[ResearchEvidenceClaim, ...]] = {}
    for section, claim_ids in dossier_input.section_claims:
        if section in sections:
            raise FinancialDataValidationError("duplicate section identifier")
        if section not in policy.required_sections + policy.optional_sections:
            raise FinancialDataValidationError("unsupported section identifier")
        if len(claim_ids) > policy.maximum_evidence_per_section:
            raise FinancialDataValidationError("maximum section evidence exceeded")
        try:
            sections[section] = tuple(claim_by_id[claim_id] for claim_id in claim_ids)
        except KeyError as exc:
            raise FinancialDataValidationError("section references unknown claim") from exc
    entity_ids = {
        reference.entity_id
        for claim in claims
        for reference in claim.evidence_references
    }
    if entity_ids - {dossier_input.entity.issuer_id}:
        raise FinancialDataValidationError("cross-issuer evidence injection")
    if dossier_input.security is not None:
        security_ids = {
            reference.security_id
            for claim in claims
            for reference in claim.evidence_references
            if reference.security_id is not None
        }
        if security_ids - {dossier_input.security.security_id}:
            raise FinancialDataValidationError("mismatched security evidence")
    evidence = [
        reference for claim in claims for reference in claim.evidence_references
    ]
    if any(
        reference.source_timestamp > constructed_at + policy.allowed_future_clock_skew
        or reference.acquired_at > constructed_at + policy.allowed_future_clock_skew
        for reference in evidence
    ):
        raise FinancialDataValidationError("future evidence exceeds policy tolerance")
    by_evidence_id: dict[str, object] = {}
    for reference in evidence:
        prior = by_evidence_id.setdefault(reference.evidence_id, reference)
        if prior != reference:
            raise FinancialDataValidationError("conflicting duplicate source record")
    financial_periods = (
        len({item.period_end for item in dossier_input.financial_history.observations})
        if dossier_input.financial_history
        else 0
    )
    completeness, high_confidence = evaluate_completeness(
        policy,
        sections,
        dossier_input.conflicts,
        dossier_input.gaps,
        identity_resolved=dossier_input.entity.resolved,
        financial_period_count=financial_periods,
        filing_count=dossier_input.filing_count,
    )
    provenance = ResearchDossierProvenance(
        policy.policy_identity,
        tuple(sorted(by_evidence_id)),
        tuple(item.claim_identity for item in claims),
        constructed_at,
    )
    return ResearchDossier(
        dossier_input.entity,
        dossier_input.security,
        policy.policy_identity,
        constructed_at,
        claims,
        dossier_input.conflicts,
        dossier_input.gaps,
        _questions(dossier_input.gaps, dossier_input.conflicts, constructed_at),
        conclusions,
        completeness,
        high_confidence,
        provenance,
        business_profile=dossier_input.business_profile,
        financial_history=dossier_input.financial_history,
    )


def verify_dossier_identity(dossier: ResearchDossier) -> bool:
    material = {
        key: value
        for key, value in dossier.__dict__.items()
        if key != "dossier_identity"
    } if hasattr(dossier, "__dict__") else {
        key: getattr(dossier, key)
        for key in dossier.__dataclass_fields__
        if key != "dossier_identity"
    }
    return canonical_digest(material) == dossier.dossier_identity


def list_sections(dossier: ResearchDossier) -> tuple[str, ...]:
    return tuple(
        name
        for name in (
            "identity",
            "business_profile",
            "financial_history",
            "management",
            "governance",
            "filings",
            "risk_factors",
            "valuation",
            "sentiment",
            "portfolio_relevance",
            "risk_relevance",
        )
        if name == "identity" or getattr(dossier, name, None)
    )


def claim_to_evidence(dossier: ResearchDossier, claim_id: str):
    return next(
        (claim.evidence_references for claim in dossier.claims if claim.claim_id == claim_id), ()
    )


def evidence_to_claims(dossier: ResearchDossier, evidence_id: str):
    return tuple(
        claim
        for claim in dossier.claims
        if any(ref.evidence_id == evidence_id for ref in claim.evidence_references)
    )


def evidence_coverage(dossier: ResearchDossier) -> tuple[tuple[str, int], ...]:
    counts: dict[str, set[str]] = {}
    for claim in dossier.claims:
        counts.setdefault(claim.claim_type, set()).update(
            reference.evidence_id for reference in claim.evidence_references
        )
    return tuple(sorted((section, len(items)) for section, items in counts.items()))


def list_conflicts(dossier: ResearchDossier):
    return dossier.conflicts


def list_gaps(dossier: ResearchDossier):
    return dossier.gaps


def list_stale_evidence(dossier: ResearchDossier):
    return tuple(
        reference
        for claim in dossier.claims
        if claim.freshness.value == "stale"
        for reference in claim.evidence_references
    )


def list_questions(dossier: ResearchDossier):
    return dossier.questions


def inspect_conclusion(dossier: ResearchDossier, conclusion_id: str):
    conclusion = next(
        (item for item in dossier.conclusions if item.conclusion_id == conclusion_id), None
    )
    if conclusion is None:
        return None
    claims = tuple(
        claim for claim in dossier.claims if claim.claim_id in conclusion.supporting_claim_ids
    )
    return conclusion, claims
