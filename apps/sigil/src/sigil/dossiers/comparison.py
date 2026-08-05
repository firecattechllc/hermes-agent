"""Deterministic comparison of immutable dossiers for the same security."""

from __future__ import annotations

from dataclasses import dataclass

from sigil.accounting.models import canonical_digest
from sigil.integrations.providers.models import FinancialDataValidationError

from .models import ResearchDossier


@dataclass(frozen=True, slots=True)
class ResearchDossierComparison:
    old_dossier_identity: str
    new_dossier_identity: str
    added_claims: tuple[str, ...]
    removed_claims: tuple[str, ...]
    changed_claims: tuple[str, ...]
    added_conflicts: tuple[str, ...]
    resolved_conflicts: tuple[str, ...]
    added_gaps: tuple[str, ...]
    closed_gaps: tuple[str, ...]
    changed_financial_observations: tuple[str, ...]
    changed_risk_factors: tuple[str, ...]
    changed_conclusions: tuple[str, ...]
    completeness_change: str
    comparison_identity: str = ""

    def __post_init__(self) -> None:
        material = {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "comparison_identity"
        }
        object.__setattr__(self, "comparison_identity", canonical_digest(material))


def _changed(old: tuple[object, ...], new: tuple[object, ...], key: str) -> tuple[str, ...]:
    old_map = {getattr(item, key): item for item in old}
    new_map = {getattr(item, key): item for item in new}
    return tuple(
        sorted(identity for identity in old_map.keys() & new_map.keys() if old_map[identity] != new_map[identity])
    )


def compare_dossiers(old: ResearchDossier, new: ResearchDossier) -> ResearchDossierComparison:
    if old.entity.identity != new.entity.identity:
        raise FinancialDataValidationError("comparison requires the same entity")
    old_security = old.security.identity if old.security else None
    new_security = new.security.identity if new.security else None
    if old_security != new_security:
        raise FinancialDataValidationError("comparison requires the same security")
    old_claims = {item.claim_id: item for item in old.claims}
    new_claims = {item.claim_id: item for item in new.claims}
    old_conflicts = {item.conflict_id: item for item in old.conflicts}
    new_conflicts = {item.conflict_id: item for item in new.conflicts}
    old_gaps = {item.gap_id: item for item in old.gaps}
    new_gaps = {item.gap_id: item for item in new.gaps}
    old_financials = old.financial_history.observations if old.financial_history else ()
    new_financials = new.financial_history.observations if new.financial_history else ()
    ranks = {
        "unavailable": 0,
        "materially_incomplete": 1,
        "partial": 2,
        "substantially_complete": 3,
        "complete": 4,
    }
    delta = ranks[new.completeness.value] - ranks[old.completeness.value]
    return ResearchDossierComparison(
        old.dossier_identity,
        new.dossier_identity,
        tuple(sorted(new_claims.keys() - old_claims.keys())),
        tuple(sorted(old_claims.keys() - new_claims.keys())),
        tuple(
            sorted(
                key
                for key in old_claims.keys() & new_claims.keys()
                if old_claims[key].claim_identity != new_claims[key].claim_identity
            )
        ),
        tuple(sorted(new_conflicts.keys() - old_conflicts.keys())),
        tuple(
            sorted(
                key
                for key in old_conflicts.keys() & new_conflicts.keys()
                if old_conflicts[key].resolution_status != "resolved"
                and new_conflicts[key].resolution_status == "resolved"
            )
        ),
        tuple(sorted(new_gaps.keys() - old_gaps.keys())),
        tuple(
            sorted(
                key
                for key in old_gaps.keys() & new_gaps.keys()
                if not old_gaps[key].resolved and new_gaps[key].resolved
            )
        ),
        _changed(old_financials, new_financials, "observation_id"),
        _changed(old.risk_factors, new.risk_factors, "risk_id"),
        _changed(old.conclusions, new.conclusions, "conclusion_id"),
        "improved" if delta > 0 else "deteriorated" if delta < 0 else "unchanged",
    )
