"""Read-only, offline thesis audit and inspection helpers."""

from __future__ import annotations

from sigil.accounting.models import canonical_digest
from sigil.dossiers.models import ResearchDossier

from .engine import ThesisConstruction, build_thesis_package
from .models import InvestmentThesisInput, InvestmentThesisPackage
from .policy import InvestmentThesisPolicy


def verify_package_identity(package: InvestmentThesisPackage) -> bool:
    material = {
        field: getattr(package, field)
        for field in package.__dataclass_fields__
        if field != "package_identity"
    }
    return canonical_digest(material) == package.package_identity


def list_thesis_pillars(package: InvestmentThesisPackage):
    return package.thesis_pillars


def list_counter_thesis_pillars(package: InvestmentThesisPackage):
    return package.counter_thesis_pillars


def argument_to_claims(package: InvestmentThesisPackage, argument_id: str) -> tuple[str, ...]:
    argument = next((item for item in package.arguments if item.argument_id == argument_id), None)
    return (
        () if argument is None else argument.supporting_claim_ids + argument.contradicting_claim_ids
    )


def claim_to_arguments(package: InvestmentThesisPackage, claim_id: str):
    return tuple(
        item
        for item in package.arguments
        if claim_id in item.supporting_claim_ids + item.contradicting_claim_ids
    )


def list_assumptions(package: InvestmentThesisPackage):
    return package.assumptions


def list_unsupported_assumptions(package: InvestmentThesisPackage):
    return tuple(item for item in package.assumptions if item.verification_status == "unsupported")


def list_catalysts(package: InvestmentThesisPackage):
    return package.catalysts


def list_risks(package: InvestmentThesisPackage):
    return package.risks


def list_invalidation_conditions(package: InvestmentThesisPackage):
    return package.invalidation_conditions


def list_falsification_tests(package: InvestmentThesisPackage):
    return package.falsification_tests


def list_monitoring_indicators(package: InvestmentThesisPackage):
    return package.monitoring_indicators


def list_conflict_impacts(package: InvestmentThesisPackage):
    return package.conflicts


def list_gap_impacts(package: InvestmentThesisPackage):
    return package.evidence_gaps


def list_readiness_blockers(package: InvestmentThesisPackage) -> tuple[str, ...]:
    return package.readiness_blockers


def confidence_component_summary(package: InvestmentThesisPackage) -> tuple[tuple[str, str], ...]:
    return package.confidence_components


def inspect_conclusion_provenance(package: InvestmentThesisPackage) -> tuple[str, tuple[str, ...]]:
    return package.provenance.provenance_identity, package.provenance.claim_identities


def regenerate_package(
    dossier: ResearchDossier,
    thesis_input: InvestmentThesisInput,
    policy: InvestmentThesisPolicy,
    construction: ThesisConstruction,
    package: InvestmentThesisPackage,
) -> InvestmentThesisPackage:
    return build_thesis_package(
        dossier,
        thesis_input,
        policy,
        construction,
        constructed_at=package.constructed_at,
        thesis_horizon=package.thesis_horizon,
    )
