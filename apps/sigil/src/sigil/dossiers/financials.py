"""Exact-decimal deterministic financial derivations."""

from __future__ import annotations

from decimal import Decimal, localcontext

from sigil.integrations.providers.models import FinancialDataValidationError

from .models import (
    FinancialDerivedValue,
    FinancialPeriodObservation,
    ResearchDossierUnavailableReason,
)


def _compatible(*items: FinancialPeriodObservation) -> None:
    first = items[0]
    if any(item.period_kind != first.period_kind for item in items[1:]):
        raise FinancialDataValidationError("annual/quarterly period mismatch")
    if any(item.currency != first.currency for item in items[1:]):
        raise FinancialDataValidationError("incompatible currencies")
    if any(item.units != first.units for item in items[1:]):
        raise FinancialDataValidationError("incompatible units")
    if any(item.balance_type != first.balance_type for item in items[1:]):
        raise FinancialDataValidationError("instant/duration mismatch")


def unavailable(metric: str, reason: ResearchDossierUnavailableReason, formula: str):
    return FinancialDerivedValue(metric, None, (), formula, reason)


def ratio(
    metric: str,
    numerator: FinancialPeriodObservation,
    denominator: FinancialPeriodObservation,
    formula: str,
) -> FinancialDerivedValue:
    _compatible(numerator, denominator)
    if (numerator.period_start, numerator.period_end) != (
        denominator.period_start,
        denominator.period_end,
    ):
        raise FinancialDataValidationError("incompatible periods")
    bottom = Decimal(denominator.value)
    if bottom == 0:
        return unavailable(metric, ResearchDossierUnavailableReason.INVALID_DENOMINATOR, formula)
    with localcontext() as context:
        context.prec = 28
        value = Decimal(numerator.value) / bottom
    return FinancialDerivedValue(
        metric,
        str(value),
        (numerator.observation_id, denominator.observation_id),
        formula,
    )


def growth(
    current: FinancialPeriodObservation, prior: FinancialPeriodObservation
) -> FinancialDerivedValue:
    _compatible(current, prior)
    bottom = Decimal(prior.value)
    if bottom == 0:
        return unavailable(
            "revenue_growth",
            ResearchDossierUnavailableReason.INVALID_DENOMINATOR,
            "(current-prior)/prior",
        )
    with localcontext() as context:
        context.prec = 28
        value = (Decimal(current.value) - bottom) / bottom
    return FinancialDerivedValue(
        "revenue_growth",
        str(value),
        (current.observation_id, prior.observation_id),
        "(current-prior)/prior",
    )


def cagr(observations: tuple[FinancialPeriodObservation, ...]) -> FinancialDerivedValue:
    ordered = tuple(sorted(observations, key=lambda item: item.period_end))
    if len(ordered) < 2:
        return unavailable(
            "revenue_cagr",
            ResearchDossierUnavailableReason.INSUFFICIENT_FINANCIAL_HISTORY,
            "(end/start)^(1/years)-1",
        )
    _compatible(*ordered)
    years = ordered[-1].fiscal_year - ordered[0].fiscal_year
    start = Decimal(ordered[0].value)
    end = Decimal(ordered[-1].value)
    if years <= 0 or start <= 0 or end < 0:
        return unavailable(
            "revenue_cagr",
            ResearchDossierUnavailableReason.INVALID_DENOMINATOR,
            "(end/start)^(1/years)-1",
        )
    with localcontext() as context:
        context.prec = 28
        value = context.power(end / start, Decimal(1) / Decimal(years)) - Decimal(1)
    return FinancialDerivedValue(
        "revenue_cagr",
        str(value),
        tuple(item.observation_id for item in ordered),
        "(end/start)^(1/years)-1",
    )


def subtract(
    metric: str,
    left: FinancialPeriodObservation,
    right: FinancialPeriodObservation,
    formula: str,
) -> FinancialDerivedValue:
    _compatible(left, right)
    if (left.period_start, left.period_end) != (right.period_start, right.period_end):
        raise FinancialDataValidationError("incompatible periods")
    value = Decimal(left.value) - Decimal(right.value)
    return FinancialDerivedValue(
        metric, str(value), (left.observation_id, right.observation_id), formula
    )


def classify_trend(values: tuple[FinancialDerivedValue, ...]) -> str:
    available = [Decimal(item.value) for item in values if item.value is not None]
    if len(available) < 2:
        return "insufficient_evidence"
    if available[-1] > available[-2]:
        return "improving"
    if available[-1] < available[-2]:
        return "deteriorating"
    return "stable"
