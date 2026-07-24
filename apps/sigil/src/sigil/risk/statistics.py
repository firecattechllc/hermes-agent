"""Decimal-only deterministic portfolio risk statistics."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, localcontext

from sigil.accounting.models import CompletenessStatus, canonical_digest, decimal_text

from .models import (
    DrawdownPeriod,
    PairwiseRiskStatistic,
    PortfolioBetaReport,
    PortfolioCorrelationMatrix,
    PortfolioDrawdownReport,
    PortfolioExpectedShortfallReport,
    PortfolioRiskPolicy,
    PortfolioRiskUnavailableReason,
    PortfolioValueAtRiskReport,
    PortfolioVolatilityReport,
    RiskBenchmarkObservation,
    RiskReturnObservation,
    exact,
)

ZERO = Decimal(0)
ONE = Decimal(1)


def text(value: Decimal) -> str:
    return decimal_text(value, "derived value", nonnegative=False) or "0"


def sqrt(value: Decimal) -> Decimal:
    """Return Decimal square root under a fixed 34-digit local context."""
    with localcontext() as context:
        context.prec = 34
        return +value.sqrt(context)


def mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def variance(values: tuple[Decimal, ...], *, sample: bool) -> Decimal:
    center = mean(values)
    denominator = len(values) - 1 if sample else len(values)
    return sum(((item - center) ** 2 for item in values), ZERO) / Decimal(denominator)


def volatility_report(
    observations: tuple[RiskReturnObservation, ...], policy: PortfolioRiskPolicy
) -> PortfolioVolatilityReport:
    count = len(observations)
    complete = all(item.completeness is CompletenessStatus.COMPLETE for item in observations)
    if count < policy.minimum_return_observations:
        return PortfolioVolatilityReport(
            None,
            None,
            None,
            None,
            None,
            count,
            observations[0].period_start if observations else None,
            observations[-1].period_end if observations else None,
            0,
            CompletenessStatus.PARTIAL,
            PortfolioRiskUnavailableReason.INSUFFICIENT_OBSERVATIONS,
        )
    values = tuple(exact(item.exact_return, "return") for item in observations)
    population = sqrt(variance(values, sample=False))
    sample = sqrt(variance(values, sample=True))
    annualized = (
        sample * sqrt(Decimal(policy.observations_per_year))
        if policy.observations_per_year is not None
        else None
    )
    minimum = exact(policy.minimum_acceptable_return, "minimum acceptable return")
    downside = tuple(min(item - minimum, ZERO) for item in values)
    downside_deviation = sqrt(sum((item**2 for item in downside), ZERO) / Decimal(count))
    return PortfolioVolatilityReport(
        text(mean(values)),
        text(population),
        text(sample),
        text(annualized) if annualized is not None else None,
        text(downside_deviation),
        count,
        observations[0].period_start,
        observations[-1].period_end,
        0,
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
    )


def drawdown_report(
    observations: tuple[RiskReturnObservation, ...], *, history_complete: bool
) -> PortfolioDrawdownReport:
    if not observations:
        return PortfolioDrawdownReport(
            (),
            None,
            None,
            None,
            None,
            None,
            None,
            (),
            None,
            None,
            False,
            CompletenessStatus.PARTIAL,
            PortfolioRiskUnavailableReason.INSUFFICIENT_OBSERVATIONS,
        )
    wealth = ONE
    peak = ONE
    peak_at = observations[0].period_start
    path: list[tuple[datetime, str]] = [(peak_at, text(wealth))]
    active_peak: datetime | None = None
    active_trough: datetime | None = None
    active_min = ZERO
    periods: list[DrawdownPeriod] = []
    maximum = ZERO
    max_start: datetime | None = None
    max_trough: datetime | None = None
    max_recovery: datetime | None = None
    for item in observations:
        wealth *= ONE + exact(item.exact_return, "return")
        path.append((item.period_end, text(wealth)))
        if wealth >= peak:
            if active_peak is not None and active_trough is not None:
                periods.append(
                    DrawdownPeriod(
                        active_peak,
                        active_trough,
                        item.period_end,
                        text(active_min),
                        (item.period_end - active_peak).days,
                    )
                )
                if active_min == maximum:
                    max_recovery = item.period_end
            peak = wealth
            peak_at = item.period_end
            active_peak = None
            active_trough = None
            active_min = ZERO
        else:
            current = wealth / peak - ONE
            if active_peak is None:
                active_peak = peak_at
            if current < active_min:
                active_min = current
                active_trough = item.period_end
            if current < maximum:
                maximum = current
                max_start = peak_at
                max_trough = item.period_end
                max_recovery = None
    if active_peak is not None and active_trough is not None:
        periods.append(
            DrawdownPeriod(
                active_peak,
                active_trough,
                None,
                text(active_min),
                (observations[-1].period_end - active_peak).days,
            )
        )
    current = wealth / peak - ONE
    longest = max((item.duration_days for item in periods), default=0)
    complete = history_complete and all(
        item.completeness is CompletenessStatus.COMPLETE for item in observations
    )
    return PortfolioDrawdownReport(
        tuple(path),
        text(maximum),
        max_start,
        max_trough,
        max_recovery,
        text(current),
        longest,
        tuple(periods),
        observations[0].period_start,
        observations[-1].period_end,
        complete,
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
    )


def _aligned(
    left: tuple[RiskReturnObservation, ...],
    right: tuple[RiskReturnObservation, ...],
) -> tuple[tuple[Decimal, Decimal, datetime, datetime], ...]:
    right_map = {
        (item.period_start, item.period_end): exact(item.exact_return, "return") for item in right
    }
    return tuple(
        (
            exact(item.exact_return, "return"),
            right_map[(item.period_start, item.period_end)],
            item.period_start,
            item.period_end,
        )
        for item in left
        if (item.period_start, item.period_end) in right_map
    )


def pairwise(
    left_name: str,
    right_name: str,
    left: tuple[RiskReturnObservation, ...],
    right: tuple[RiskReturnObservation, ...],
    minimum: int,
) -> PairwiseRiskStatistic:
    overlap = _aligned(left, right)
    start = overlap[0][2] if overlap else None
    end = overlap[-1][3] if overlap else None
    if len(overlap) < minimum:
        return PairwiseRiskStatistic(
            left_name,
            right_name,
            None,
            None,
            len(overlap),
            start,
            end,
            CompletenessStatus.PARTIAL,
            PortfolioRiskUnavailableReason.INSUFFICIENT_OVERLAP,
        )
    left_values = tuple(item[0] for item in overlap)
    right_values = tuple(item[1] for item in overlap)
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    covariance = sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left_values, right_values)),
        ZERO,
    ) / Decimal(len(overlap) - 1)
    left_variance = variance(left_values, sample=True)
    right_variance = variance(right_values, sample=True)
    if left_variance == 0 or right_variance == 0:
        return PairwiseRiskStatistic(
            left_name,
            right_name,
            text(covariance),
            None,
            len(overlap),
            start,
            end,
            CompletenessStatus.PARTIAL,
            PortfolioRiskUnavailableReason.ZERO_VARIANCE,
        )
    correlation = covariance / (sqrt(left_variance) * sqrt(right_variance))
    return PairwiseRiskStatistic(
        left_name,
        right_name,
        text(covariance),
        text(correlation),
        len(overlap),
        start,
        end,
        CompletenessStatus.COMPLETE,
    )


def correlation_matrix(
    series: tuple[tuple[str, tuple[RiskReturnObservation, ...]], ...],
    policy: PortfolioRiskPolicy,
) -> PortfolioCorrelationMatrix:
    symbols = tuple(item[0] for item in series)
    pairs: list[PairwiseRiskStatistic] = []
    for left_index, (left_name, left) in enumerate(series):
        for right_name, right in series[left_index:]:
            pairs.append(
                pairwise(left_name, right_name, left, right, policy.minimum_common_observations)
            )
    complete = all(item.completeness is CompletenessStatus.COMPLETE for item in pairs)
    return PortfolioCorrelationMatrix(
        symbols,
        tuple(pairs),
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
    )


def beta_report(
    portfolio: tuple[RiskReturnObservation, ...],
    benchmark: tuple[RiskBenchmarkObservation, ...],
    policy: PortfolioRiskPolicy,
) -> PortfolioBetaReport | None:
    if not benchmark:
        return None
    benchmark_id = benchmark[0].identity
    aligned = _aligned(portfolio, benchmark)
    start = aligned[0][2] if aligned else None
    end = aligned[-1][3] if aligned else None
    minimum = max(policy.minimum_return_observations, policy.minimum_benchmark_observations)
    complete_inputs = all(
        item.completeness is CompletenessStatus.COMPLETE for item in (*portfolio, *benchmark)
    )
    if len(aligned) < minimum or not complete_inputs:
        reason = (
            PortfolioRiskUnavailableReason.INCOMPLETE_HISTORY
            if not complete_inputs
            else PortfolioRiskUnavailableReason.INSUFFICIENT_OVERLAP
        )
        return PortfolioBetaReport(
            benchmark_id,
            None,
            None,
            None,
            None,
            None,
            len(aligned),
            start,
            end,
            CompletenessStatus.PARTIAL,
            reason,
        )
    left = tuple(item[0] for item in aligned)
    right = tuple(item[1] for item in aligned)
    left_mean, right_mean = mean(left), mean(right)
    covariance = sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left, right)), ZERO
    ) / Decimal(len(aligned) - 1)
    benchmark_variance = variance(right, sample=True)
    if benchmark_variance == 0:
        return PortfolioBetaReport(
            benchmark_id,
            text(covariance),
            "0",
            None,
            None,
            None,
            len(aligned),
            start,
            end,
            CompletenessStatus.PARTIAL,
            PortfolioRiskUnavailableReason.ZERO_VARIANCE,
        )
    active = tuple(x - y for x, y in zip(left, right))
    tracking = sqrt(variance(active, sample=True))
    annualized = (
        tracking * sqrt(Decimal(policy.observations_per_year))
        if policy.observations_per_year is not None
        else None
    )
    return PortfolioBetaReport(
        benchmark_id,
        text(covariance),
        text(benchmark_variance),
        text(covariance / benchmark_variance),
        text(tracking),
        text(annualized) if annualized is not None else None,
        len(aligned),
        start,
        end,
        CompletenessStatus.COMPLETE,
    )


def tail_reports(
    observations: tuple[RiskReturnObservation, ...],
    equity: Decimal | None,
    valuation_identity: str,
    policy: PortfolioRiskPolicy,
) -> tuple[PortfolioValueAtRiskReport, PortfolioExpectedShortfallReport]:
    observation_identity = canonical_digest(observations)
    count = len(observations)
    complete = all(item.completeness is CompletenessStatus.COMPLETE for item in observations)
    unavailable = count < policy.minimum_return_observations or not complete or equity is None
    if unavailable:
        reason = (
            PortfolioRiskUnavailableReason.UNAVAILABLE_VALUATION
            if equity is None
            else PortfolioRiskUnavailableReason.INCOMPLETE_HISTORY
            if not complete
            else PortfolioRiskUnavailableReason.INSUFFICIENT_OBSERVATIONS
        )
        var = PortfolioValueAtRiskReport(
            policy.confidence_level,
            1,
            "historical",
            count,
            None,
            None,
            None,
            valuation_identity,
            observation_identity,
            CompletenessStatus.PARTIAL,
            ("Historical VaR is an estimate, not a maximum possible loss.",),
            reason,
        )
        es = PortfolioExpectedShortfallReport(
            policy.confidence_level,
            None,
            0,
            None,
            None,
            None,
            observations[0].period_start if observations else None,
            observations[-1].period_end if observations else None,
            CompletenessStatus.PARTIAL,
            reason,
        )
        return var, es
    returns = sorted(exact(item.exact_return, "return") for item in observations)
    # Nearest-rank lower-tail boundary: ceil(n * (1-confidence)), bounded to one.
    tail_probability = ONE - exact(policy.confidence_level, "confidence")
    rank = max(
        1, int((Decimal(count) * tail_probability).to_integral_value(rounding="ROUND_CEILING"))
    )
    index = rank - 1
    threshold_return = returns[index]
    loss_percentage = max(-threshold_return, ZERO)
    loss_amount = equity * loss_percentage
    tail = tuple(item for item in returns if item <= threshold_return)
    if len(tail) < policy.minimum_tail_observations:
        reason = PortfolioRiskUnavailableReason.INSUFFICIENT_OBSERVATIONS
        es = PortfolioExpectedShortfallReport(
            policy.confidence_level,
            text(loss_percentage),
            len(tail),
            None,
            None,
            text(max(-returns[0], ZERO)),
            observations[0].period_start,
            observations[-1].period_end,
            CompletenessStatus.PARTIAL,
            reason,
        )
    else:
        tail_loss = mean(tuple(max(-item, ZERO) for item in tail))
        es = PortfolioExpectedShortfallReport(
            policy.confidence_level,
            text(loss_percentage),
            len(tail),
            text(tail_loss),
            text(equity * tail_loss),
            text(max(-returns[0], ZERO)),
            observations[0].period_start,
            observations[-1].period_end,
            CompletenessStatus.COMPLETE,
        )
    var = PortfolioValueAtRiskReport(
        policy.confidence_level,
        1,
        "historical",
        count,
        index,
        text(loss_percentage),
        text(loss_amount),
        valuation_identity,
        observation_identity,
        CompletenessStatus.COMPLETE,
        ("Historical VaR is an estimate, not a maximum possible loss.",),
    )
    return var, es
