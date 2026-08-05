"""Side-effect-free orchestration for governed thesis packages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sigil.accounting.models import timestamp
from sigil.dossiers.models import ResearchCompletenessStatus, ResearchDossier
from sigil.integrations.providers.models import FinancialDataValidationError

from .models import (
    CounterThesisPillar,
    InvestmentCounterThesis,
    InvestmentThesis,
    InvestmentThesisInput,
    InvestmentThesisPackage,
    InvestmentThesisProvenance,
    InvestmentThesisUnavailableReason,
    ThesisArgument,
    ThesisAssumption,
    ThesisCatalyst,
    ThesisCausalChain,
    ThesisCompletenessClassification,
    ThesisConfidenceClassification,
    ThesisConflict,
    ThesisDependency,
    ThesisEvidenceGap,
    ThesisExpectedDevelopment,
    ThesisFalsificationTest,
    ThesisInvalidationCondition,
    ThesisMonitoringIndicator,
    ThesisPillar,
    ThesisPortfolioRelevance,
    ThesisReadinessClassification,
    ThesisRisk,
    ThesisRiskRelevance,
    ThesisValuationDependency,
)
from .policy import InvestmentThesisPolicy


@dataclass(frozen=True, slots=True)
class ThesisConstruction:
    investment_thesis: InvestmentThesis
    counter_thesis: InvestmentCounterThesis
    arguments: tuple[ThesisArgument, ...]
    thesis_pillars: tuple[ThesisPillar, ...]
    counter_thesis_pillars: tuple[CounterThesisPillar, ...]
    assumptions: tuple[ThesisAssumption, ...]
    dependencies: tuple[ThesisDependency, ...] = ()
    causal_chains: tuple[ThesisCausalChain, ...] = ()
    catalysts: tuple[ThesisCatalyst, ...] = ()
    risks: tuple[ThesisRisk, ...] = ()
    invalidation_conditions: tuple[ThesisInvalidationCondition, ...] = ()
    falsification_tests: tuple[ThesisFalsificationTest, ...] = ()
    monitoring_indicators: tuple[ThesisMonitoringIndicator, ...] = ()
    expected_developments: tuple[ThesisExpectedDevelopment, ...] = ()
    valuation_dependencies: tuple[ThesisValuationDependency, ...] = ()
    portfolio_relevance: ThesisPortfolioRelevance = field(default_factory=ThesisPortfolioRelevance)
    risk_relevance: ThesisRiskRelevance = field(default_factory=ThesisRiskRelevance)
    conflicts: tuple[ThesisConflict, ...] = ()
    evidence_gaps: tuple[ThesisEvidenceGap, ...] = ()


def _unique(items: tuple[object, ...], field: str) -> dict[str, object]:
    result = {getattr(item, field): item for item in items}
    if len(result) != len(items):
        raise FinancialDataValidationError(f"duplicate {field}")
    return result


def _validate_references(
    dossier: ResearchDossier,
    thesis_input: InvestmentThesisInput,
    construction: ThesisConstruction,
    policy: InvestmentThesisPolicy,
) -> None:
    if thesis_input.dossier_identity != dossier.dossier_identity:
        raise FinancialDataValidationError("dossier identity mismatch")
    if thesis_input.issuer_id != dossier.entity.issuer_id:
        raise FinancialDataValidationError("issuer mismatch")
    if dossier.security is None or thesis_input.security_id != dossier.security.security_id:
        raise FinancialDataValidationError("security mismatch")
    claims = {claim.claim_id: claim for claim in dossier.claims}
    conflicts = {item.conflict_id: item for item in dossier.conflicts}
    gaps = {item.gap_id: item for item in dossier.gaps}
    if not set(thesis_input.selected_claim_ids) <= set(claims):
        raise FinancialDataValidationError("unknown claim reference")
    if not set(thesis_input.selected_conflict_ids) <= set(conflicts):
        raise FinancialDataValidationError("unknown conflict reference")
    if not set(thesis_input.selected_gap_ids) <= set(gaps):
        raise FinancialDataValidationError("unknown gap reference")
    argument_by_id = _unique(construction.arguments, "argument_id")
    assumption_by_id = _unique(construction.assumptions, "assumption_id")
    invalidation_by_id = _unique(construction.invalidation_conditions, "condition_id")
    indicator_by_id = _unique(construction.monitoring_indicators, "indicator_id")
    all_claim_links: list[str] = []
    for argument in construction.arguments:
        if argument.subject_identity not in {dossier.entity.identity, dossier.security.identity}:
            raise FinancialDataValidationError("cross-issuer argument injection")
        linked = argument.supporting_claim_ids + argument.contradicting_claim_ids
        if not set(linked) <= set(claims):
            raise FinancialDataValidationError("argument references unknown claim")
        if not set(linked) <= set(thesis_input.selected_claim_ids):
            raise FinancialDataValidationError("argument claim was not selected")
        if len(linked) > policy.maximum_claims_per_argument:
            raise FinancialDataValidationError("maximum claims per argument exceeded")
        all_claim_links.extend(linked)
        if not set(argument.assumption_ids) <= set(assumption_by_id):
            raise FinancialDataValidationError("argument references unknown assumption")
    if len(all_claim_links) > policy.maximum_total_evidence_links:
        raise FinancialDataValidationError("maximum evidence links exceeded")
    for pillar in construction.thesis_pillars + construction.counter_thesis_pillars:
        if len(pillar.argument_ids) > policy.maximum_arguments_per_pillar:
            raise FinancialDataValidationError("maximum arguments per pillar exceeded")
        if not set(pillar.argument_ids) <= set(argument_by_id):
            raise FinancialDataValidationError("pillar references unknown argument")
        if not set(pillar.supporting_claim_ids + pillar.contradicting_claim_ids) <= set(
            claims
        ):
            raise FinancialDataValidationError("pillar references unknown claim")
        if invalidation_by_id and not set(pillar.invalidation_condition_ids) <= set(
            invalidation_by_id
        ):
            raise FinancialDataValidationError("pillar references unknown invalidation condition")
        if not set(pillar.monitoring_indicator_ids) <= set(indicator_by_id):
            raise FinancialDataValidationError("pillar references unknown monitoring indicator")
        if len(pillar.supporting_claim_ids) < policy.minimum_supporting_claims_per_pillar:
            raise FinancialDataValidationError("pillar has insufficient evidence")
    thesis_pillar_ids = {item.pillar_id for item in construction.thesis_pillars}
    counter_pillar_ids = {item.pillar_id for item in construction.counter_thesis_pillars}
    if set(construction.investment_thesis.pillar_ids) != thesis_pillar_ids:
        raise FinancialDataValidationError("investment thesis pillar mismatch")
    if set(construction.counter_thesis.pillar_ids) != counter_pillar_ids:
        raise FinancialDataValidationError("counter-thesis pillar mismatch")
    if thesis_pillar_ids & counter_pillar_ids:
        raise FinancialDataValidationError("counter-thesis must be independently constructed")
    if any(
        argument_by_id[argument_id].direction != "supports_counter_thesis"
        for pillar in construction.counter_thesis_pillars
        for argument_id in pillar.argument_ids
    ):
        raise FinancialDataValidationError(
            "counter-thesis must use independently directed arguments"
        )


def _classifications(
    dossier: ResearchDossier,
    construction: ThesisConstruction,
    policy: InvestmentThesisPolicy,
) -> tuple[
    ThesisConfidenceClassification,
    ThesisCompletenessClassification,
    ThesisReadinessClassification,
    tuple[InvestmentThesisUnavailableReason, ...],
    tuple[str, ...],
    tuple[tuple[str, str], ...],
]:
    reasons: set[InvestmentThesisUnavailableReason] = set()
    blockers: set[str] = set()
    unresolved_conflicts = [
        item
        for item in construction.conflicts
        if not item.resolved and item.impact in {"blocks_pillar", "blocks_readiness"}
    ]
    unresolved_gaps = [
        item
        for item in construction.evidence_gaps
        if not item.resolved and item.impact in {"blocks_pillar", "blocks_readiness"}
    ]
    unsupported = [
        item
        for item in construction.assumptions
        if item.materiality in {"material", "critical"}
        and item.verification_status == "unsupported"
    ]
    stale = any(argument.stale for argument in construction.arguments) or any(
        item.stale for item in construction.evidence_gaps
    )
    truncated = any(item.truncated for item in construction.evidence_gaps)
    checks = (
        (
            len(construction.counter_thesis_pillars) < policy.minimum_counter_thesis_pillars,
            InvestmentThesisUnavailableReason.MISSING_COUNTER_THESIS,
            "counter_thesis",
        ),
        (
            len(construction.invalidation_conditions) < policy.minimum_invalidation_conditions,
            InvestmentThesisUnavailableReason.MISSING_INVALIDATION,
            "invalidation_conditions",
        ),
        (
            len(construction.falsification_tests) < policy.minimum_falsification_tests,
            InvestmentThesisUnavailableReason.MISSING_FALSIFICATION,
            "falsification_tests",
        ),
        (
            bool(unsupported),
            InvestmentThesisUnavailableReason.UNSUPPORTED_ASSUMPTION,
            "unsupported_material_assumptions",
        ),
        (
            len(unresolved_conflicts) > policy.maximum_unresolved_material_conflicts,
            InvestmentThesisUnavailableReason.MATERIAL_CONFLICT,
            "material_conflicts",
        ),
        (
            len(unresolved_gaps) > policy.maximum_unresolved_material_gaps,
            InvestmentThesisUnavailableReason.MATERIAL_GAP,
            "material_gaps",
        ),
        (stale, InvestmentThesisUnavailableReason.STALE_EVIDENCE, "stale_evidence"),
        (truncated, InvestmentThesisUnavailableReason.TRUNCATED_EVIDENCE, "truncated_evidence"),
        (
            dossier.completeness
            in {
                ResearchCompletenessStatus.MATERIALLY_INCOMPLETE,
                ResearchCompletenessStatus.UNAVAILABLE,
            },
            InvestmentThesisUnavailableReason.INVALID_DOSSIER,
            "dossier_completeness",
        ),
    )
    for failed, reason, blocker in checks:
        if failed:
            reasons.add(reason)
            blockers.add(blocker)
    required_counts_met = (
        len(construction.assumptions) >= policy.minimum_assumptions
        and len(construction.risks) >= policy.minimum_risk_coverage
        and len(construction.monitoring_indicators) >= policy.minimum_monitoring_indicators
        and len(construction.catalysts) >= policy.minimum_catalyst_coverage
    )
    if not required_counts_met:
        blockers.add("required_section_coverage")
    if reasons:
        completeness = ThesisCompletenessClassification.MATERIALLY_INCOMPLETE
        readiness = ThesisReadinessClassification.BLOCKED
    elif not required_counts_met:
        completeness = ThesisCompletenessClassification.PARTIAL
        readiness = ThesisReadinessClassification.REQUIRES_RESEARCH
    elif any(
        item.completeness != ThesisCompletenessClassification.COMPLETE
        for item in construction.thesis_pillars + construction.counter_thesis_pillars
    ):
        completeness = ThesisCompletenessClassification.SUBSTANTIALLY_COMPLETE
        readiness = ThesisReadinessClassification.REQUIRES_RESEARCH
    else:
        completeness = ThesisCompletenessClassification.COMPLETE
        readiness = ThesisReadinessClassification.READY_FOR_REVIEW
    contradiction_count = sum(len(item.contradicting_claim_ids) for item in construction.arguments)
    source_identities = {
        reference.source_identity
        for claim in dossier.claims
        if claim.claim_id
        in {
            claim_id
            for argument in construction.arguments
            for claim_id in argument.supporting_claim_ids
        }
        for reference in claim.evidence_references
    }
    if not construction.arguments:
        confidence = ThesisConfidenceClassification.UNAVAILABLE
    elif readiness == ThesisReadinessClassification.BLOCKED or len(source_identities) < 1:
        confidence = ThesisConfidenceClassification.LOW
    elif contradiction_count or len(source_identities) < 2:
        confidence = ThesisConfidenceClassification.MODERATE
    else:
        confidence = ThesisConfidenceClassification.HIGH
    components = (
        ("contradicting_claim_count", str(contradiction_count)),
        ("source_diversity_count", str(len(source_identities))),
        ("unsupported_material_assumption_count", str(len(unsupported))),
        ("unresolved_material_conflict_count", str(len(unresolved_conflicts))),
        ("unresolved_material_gap_count", str(len(unresolved_gaps))),
        ("falsifiable", str(bool(construction.falsification_tests)).lower()),
        ("counter_thesis_pillar_count", str(len(construction.counter_thesis_pillars))),
    )
    return confidence, completeness, readiness, tuple(reasons), tuple(blockers), components


def build_thesis_package(
    dossier: ResearchDossier,
    thesis_input: InvestmentThesisInput,
    policy: InvestmentThesisPolicy,
    construction: ThesisConstruction,
    *,
    constructed_at: datetime,
    thesis_horizon: str,
    construction_started_at: datetime | None = None,
) -> InvestmentThesisPackage:
    """Construct one immutable interpretation; ready-for-review never authorizes trading."""
    timestamp(constructed_at, "constructed_at")
    if dossier.constructed_at > constructed_at + policy.allowed_future_clock_skew:
        raise FinancialDataValidationError("future dossier timestamp")
    if construction_started_at is not None:
        timestamp(construction_started_at, "construction_started_at")
        if (
            constructed_at < construction_started_at
            or constructed_at - construction_started_at > policy.maximum_construction_duration
        ):
            raise FinancialDataValidationError("thesis construction duration exceeds policy")
    if not thesis_horizon.strip() or len(thesis_horizon) > 200:
        raise FinancialDataValidationError("invalid thesis horizon")
    if dossier.security is None:
        raise FinancialDataValidationError("ambiguous thesis subject")
    if dossier.security.currency not in policy.allowed_currencies:
        raise FinancialDataValidationError("unsupported currency")
    if dossier.security.instrument_type not in policy.allowed_instruments:
        raise FinancialDataValidationError("unsupported instrument")
    _validate_references(dossier, thesis_input, construction, policy)
    confidence, completeness, readiness, reasons, blockers, components = _classifications(
        dossier, construction, policy
    )
    claim_by_id = {claim.claim_id: claim for claim in dossier.claims}
    used_claim_ids = sorted(
        {
            claim_id
            for argument in construction.arguments
            for claim_id in argument.supporting_claim_ids + argument.contradicting_claim_ids
        }
    )
    provenance = InvestmentThesisProvenance(
        dossier.dossier_identity,
        policy.policy_identity,
        tuple(claim_by_id[item].claim_identity for item in used_claim_ids),
        tuple(
            filter(
                None,
                (
                    thesis_input.input_identity,
                    thesis_input.risk_context_identity,
                    thesis_input.portfolio_context_identity,
                ),
            )
        ),
        constructed_at,
    )
    return InvestmentThesisPackage(
        "sigil-thesis-package-v1",
        policy.policy_identity,
        dossier.dossier_identity,
        dossier.entity.issuer_id,
        dossier.security.security_id,
        constructed_at,
        thesis_horizon,
        construction.investment_thesis,
        construction.counter_thesis,
        construction.arguments,
        construction.thesis_pillars,
        construction.counter_thesis_pillars,
        construction.assumptions,
        construction.dependencies,
        construction.causal_chains,
        construction.catalysts,
        construction.risks,
        construction.invalidation_conditions,
        construction.falsification_tests,
        construction.monitoring_indicators,
        construction.expected_developments,
        construction.valuation_dependencies,
        construction.portfolio_relevance,
        construction.risk_relevance,
        construction.conflicts,
        construction.evidence_gaps,
        confidence,
        completeness,
        readiness,
        reasons,
        provenance,
        blockers,
        components,
    )


def evaluate_invalidation_condition(
    condition: ThesisInvalidationCondition,
    observation: str | None,
    *,
    evaluated_at: datetime,
) -> ThesisInvalidationCondition:
    """Evaluate only a caller-supplied immutable decimal observation."""
    from dataclasses import replace
    from decimal import Decimal

    from sigil.accounting.models import canonical_digest, decimal_text

    timestamp(evaluated_at, "evaluated_at")
    evaluation_identity = canonical_digest((condition.condition_digest, observation, evaluated_at))
    if observation is None:
        return replace(
            condition,
            status="unavailable",
            evaluation_identity=evaluation_identity,
            condition_digest="",
        )
    if condition.operator is None or condition.threshold is None:
        raise FinancialDataValidationError("evidence-based condition requires caller evaluation")
    value = Decimal(decimal_text(observation, "observation", nonnegative=False))
    threshold = Decimal(condition.threshold)
    triggered = {
        "<": value < threshold,
        "<=": value <= threshold,
        "==": value == threshold,
        "!=": value != threshold,
        ">=": value >= threshold,
        ">": value > threshold,
    }[condition.operator]
    return replace(
        condition,
        status="triggered" if triggered else "clear",
        triggered_at=evaluated_at if triggered else None,
        evaluation_identity=evaluation_identity,
        condition_digest="",
    )


def evaluate_falsification_test(
    test: ThesisFalsificationTest, *, falsified: bool | None, unavailable_reason: str | None = None
) -> ThesisFalsificationTest:
    from dataclasses import replace

    if falsified is None:
        return replace(
            test,
            status="unavailable",
            unavailable_reason=unavailable_reason or "required_observation_missing",
            test_identity="",
        )
    return replace(
        test,
        status="falsified" if falsified else "passed",
        unavailable_reason=None,
        test_identity="",
    )
