"""Transparent dossier completeness and high-confidence eligibility."""

from __future__ import annotations

from .models import (
    ResearchCompletenessStatus,
    ResearchEvidenceClaim,
    ResearchEvidenceConflict,
    ResearchEvidenceGap,
)
from .policy import ResearchDossierPolicy


def evaluate_completeness(
    policy: ResearchDossierPolicy,
    section_claims: dict[str, tuple[ResearchEvidenceClaim, ...]],
    conflicts: tuple[ResearchEvidenceConflict, ...],
    gaps: tuple[ResearchEvidenceGap, ...],
    *,
    identity_resolved: bool,
    financial_period_count: int,
    filing_count: int,
) -> tuple[ResearchCompletenessStatus, bool]:
    if not identity_resolved:
        return ResearchCompletenessStatus.UNAVAILABLE, False
    present = {
        section
        for section, claims in section_claims.items()
        if len(claims) >= policy.minimum_evidence_per_required_section
    }
    missing = set(policy.required_sections) - present
    material_gaps = [gap for gap in gaps if gap.materiality == "material" and not gap.resolved]
    material_conflicts = [
        conflict
        for conflict in conflicts
        if conflict.materiality == "material" and conflict.resolution_status != "resolved"
    ]
    stale_or_truncated = any(gap.stale or gap.truncated for gap in material_gaps)
    sufficient_financials = financial_period_count >= policy.minimum_financial_history_periods
    sufficient_filings = filing_count >= policy.minimum_filing_coverage
    if missing and len(missing) == len(policy.required_sections):
        status = ResearchCompletenessStatus.MATERIALLY_INCOMPLETE
    elif missing or not sufficient_financials or not sufficient_filings or material_gaps:
        status = ResearchCompletenessStatus.PARTIAL
    elif material_conflicts:
        status = ResearchCompletenessStatus.SUBSTANTIALLY_COMPLETE
    else:
        status = ResearchCompletenessStatus.COMPLETE
    high_confidence = (
        not missing
        and sufficient_financials
        and sufficient_filings
        and len(material_conflicts) <= policy.maximum_conflicts_for_high_confidence
        and len(material_gaps) <= policy.maximum_unresolved_material_gaps
        and not stale_or_truncated
    )
    return status, high_confidence
