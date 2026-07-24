"""Deterministic, non-directional thesis package comparison."""

from __future__ import annotations

from sigil.integrations.providers.models import FinancialDataValidationError

from .models import InvestmentThesisComparison, InvestmentThesisPackage


def _changes(
    before: tuple[object, ...],
    after: tuple[object, ...],
    id_field: str,
    digest_field: str,
    prefix: str,
):
    old = {getattr(item, id_field): getattr(item, digest_field) for item in before}
    new = {getattr(item, id_field): getattr(item, digest_field) for item in after}
    return (
        (f"added_{prefix}", tuple(sorted(new.keys() - old.keys()))),
        (f"removed_{prefix}", tuple(sorted(old.keys() - new.keys()))),
        (
            f"changed_{prefix}",
            tuple(sorted(key for key in old.keys() & new.keys() if old[key] != new[key])),
        ),
    )


def compare_thesis_packages(
    before: InvestmentThesisPackage, after: InvestmentThesisPackage
) -> InvestmentThesisComparison:
    if before.issuer_id != after.issuer_id or before.security_id != after.security_id:
        raise FinancialDataValidationError("comparison requires the same resolved entity")
    changes = []
    for values in (
        _changes(before.arguments, after.arguments, "argument_id", "argument_digest", "arguments"),
        _changes(
            before.thesis_pillars,
            after.thesis_pillars,
            "pillar_id",
            "pillar_identity",
            "thesis_pillars",
        ),
        _changes(
            before.counter_thesis_pillars,
            after.counter_thesis_pillars,
            "pillar_id",
            "pillar_identity",
            "counter_thesis_pillars",
        ),
        _changes(
            before.assumptions,
            after.assumptions,
            "assumption_id",
            "assumption_identity",
            "assumptions",
        ),
        _changes(
            before.catalysts, after.catalysts, "catalyst_id", "catalyst_identity", "catalysts"
        ),
        _changes(before.risks, after.risks, "risk_id", "risk_identity", "risks"),
        _changes(
            before.invalidation_conditions,
            after.invalidation_conditions,
            "condition_id",
            "condition_digest",
            "invalidation_conditions",
        ),
        _changes(
            before.falsification_tests,
            after.falsification_tests,
            "test_id",
            "test_identity",
            "falsification_tests",
        ),
        _changes(
            before.monitoring_indicators,
            after.monitoring_indicators,
            "indicator_id",
            "indicator_identity",
            "monitoring_indicators",
        ),
        _changes(
            before.expected_developments,
            after.expected_developments,
            "development_id",
            "development_identity",
            "expected_developments",
        ),
        _changes(
            before.valuation_dependencies,
            after.valuation_dependencies,
            "dependency_id",
            "dependency_identity",
            "valuation_dependencies",
        ),
    ):
        changes.extend(item for item in values if item[1])
    triggered = tuple(
        sorted(
            item.condition_id
            for item in after.invalidation_conditions
            if item.status == "triggered"
            and next(
                (
                    old.status
                    for old in before.invalidation_conditions
                    if old.condition_id == item.condition_id
                ),
                None,
            )
            != "triggered"
        )
    )
    cleared = tuple(
        sorted(
            item.condition_id
            for item in after.invalidation_conditions
            if item.status == "clear"
            and next(
                (
                    old.status
                    for old in before.invalidation_conditions
                    if old.condition_id == item.condition_id
                ),
                None,
            )
            == "triggered"
        )
    )
    if triggered:
        changes.append(("triggered_invalidation_conditions", triggered))
    if cleared:
        changes.append(("cleared_invalidation_conditions", cleared))
    if before.portfolio_relevance != after.portfolio_relevance:
        changes.append(
            ("changed_portfolio_relevance", (after.portfolio_relevance.relevance_identity,))
        )
    if before.risk_relevance != after.risk_relevance:
        changes.append(("changed_risk_relevance", (after.risk_relevance.relevance_identity,)))
    stale_before = tuple(sorted(item.argument_id for item in before.arguments if item.stale))
    stale_after = tuple(sorted(item.argument_id for item in after.arguments if item.stale))
    if stale_before != stale_after:
        changes.append(("evidence_freshness_change", stale_after))
    return InvestmentThesisComparison(
        before.package_identity,
        after.package_identity,
        tuple(changes),
        None
        if before.confidence == after.confidence
        else (before.confidence.value, after.confidence.value),
        None
        if before.completeness == after.completeness
        else (before.completeness.value, after.completeness.value),
        None
        if before.readiness == after.readiness
        else (before.readiness.value, after.readiness.value),
        None
        if before.dossier_identity == after.dossier_identity
        else (before.dossier_identity, after.dossier_identity),
    )
