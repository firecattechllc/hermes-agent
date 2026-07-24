"""Read-only orchestration for governed portfolio risk estimates."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from sigil.accounting.models import CompletenessStatus
from sigil.integrations.providers.models import FinancialDataValidationError

from .models import (
    ClassificationConcentration,
    PortfolioExposureReport,
    PortfolioLiquidityReport,
    PortfolioRiskInput,
    PortfolioRiskLimit,
    PortfolioRiskLimitViolation,
    PortfolioRiskReport,
    PortfolioStressResult,
    PortfolioStressScenario,
    PositionConcentration,
    PositionLiquidity,
    exact,
)
from .statistics import (
    beta_report,
    correlation_matrix,
    drawdown_report,
    tail_reports,
    text,
    volatility_report,
)

UNKNOWN = "UNKNOWN"
ZERO = Decimal(0)


def _divide(numerator: Decimal, denominator: Decimal | None) -> str | None:
    return text(numerator / denominator) if denominator not in {None, ZERO} else None


def exposure_report(inputs: PortfolioRiskInput) -> PortfolioExposureReport:
    priced = tuple(item for item in inputs.positions if item.market_value is not None)
    position_value = sum((exact(item.market_value, "market value") for item in priced), ZERO)
    cash = exact(inputs.cash_value, "cash", nonnegative=False)
    complete = inputs.portfolio_complete and len(priced) == len(inputs.positions)
    equity = position_value + cash if complete else None
    denominator = equity if equity is not None and equity > 0 else None
    positions = tuple(
        PositionConcentration(
            item.symbol,
            item.market_value,
            _divide(exact(item.market_value, "market value"), denominator)
            if item.market_value is not None
            else None,
            item.market_value is not None,
            item.stale,
        )
        for item in inputs.positions
    )
    classification_map = {item.symbol: item for item in inputs.classifications}
    groups: dict[tuple[str, str], tuple[Decimal, list[str]]] = {}
    for item in priced:
        classification = classification_map.get(item.symbol)
        values = {
            "issuer": classification.issuer if classification else None,
            "sector": classification.sector if classification else None,
            "industry": classification.industry if classification else None,
            "instrument": item.instrument_type,
        }
        for kind, value in values.items():
            key = (kind, value or UNKNOWN)
            aggregate, symbols = groups.get(key, (ZERO, []))
            groups[key] = (
                aggregate + exact(item.market_value, "market value"),
                [*symbols, item.symbol],
            )
    classifications = tuple(
        ClassificationConcentration(
            kind, value, text(amount), _divide(amount, denominator) or "0", tuple(symbols)
        )
        for (kind, value), (amount, symbols) in sorted(groups.items())
    )
    weights = sorted(
        (exact(item.weight, "weight") for item in positions if item.weight is not None),
        reverse=True,
    )
    top = lambda count: text(sum(weights[:count], ZERO)) if denominator is not None else None
    hhi = (
        text(sum((weight * weight for weight in weights), ZERO))
        if complete and denominator
        else None
    )
    stale_value = sum(
        (
            exact(item.market_value, "value")
            for item in inputs.positions
            if item.stale and item.market_value
        ),
        ZERO,
    )
    # Unpriced weight is unknowable; report zero only when no positions are unpriced.
    has_unpriced = len(priced) != len(inputs.positions)
    limitations = []
    if has_unpriced:
        limitations.append(
            "Unpriced positions remain missing; their portfolio weight is unavailable."
        )
    if any(item.stale for item in inputs.positions):
        limitations.append("Stale prices are retained as stale and are not treated as fresh.")
    if not inputs.portfolio_complete:
        limitations.append("The partial portfolio is not completely measured.")
    return PortfolioExposureReport(
        text(equity) if equity is not None else None,
        text(position_value),
        text(cash),
        text(position_value),
        text(position_value),
        _divide(position_value, denominator),
        _divide(cash, denominator),
        positions,
        classifications,
        top(1),
        top(3),
        top(5),
        hhi,
        _divide(stale_value, denominator),
        None if has_unpriced else "0",
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
        tuple(limitations),
    )


def liquidity_report(
    inputs: PortfolioRiskInput, exposure: PortfolioExposureReport
) -> PortfolioLiquidityReport:
    observations = {item.symbol: item for item in inputs.liquidity}
    equity = exact(exposure.total_equity, "equity") if exposure.total_equity else None
    participation = exact(inputs.policy.participation_rate, "participation")
    rows: list[PositionLiquidity] = []
    missing_value = ZERO
    stale_value = ZERO
    breaching_value = ZERO
    liquidity_limits = tuple(
        item
        for item in inputs.policy.limits
        if item.metric in {"average_daily_dollar_volume", "days_to_liquidate"}
    )
    for position in inputs.positions:
        value = exact(position.market_value, "market value") if position.market_value else None
        observation = observations.get(position.symbol)
        missing = observation is None
        stale = (
            observation is not None
            and inputs.as_of - observation.observed_at > inputs.policy.maximum_liquidity_age
        )
        percentage = days = None
        if value is not None and observation is not None:
            daily = exact(observation.average_daily_dollar_volume, "daily dollar volume")
            percentage = _divide(value, daily)
            days = _divide(value, daily * participation)
            if any(
                (
                    limit.metric == "average_daily_dollar_volume"
                    and daily < exact(limit.threshold, "threshold")
                )
                or (
                    limit.metric == "days_to_liquidate"
                    and days is not None
                    and exact(days, "days") > exact(limit.threshold, "threshold")
                )
                for limit in liquidity_limits
            ):
                breaching_value += value
        if value is not None and missing:
            missing_value += value
        if value is not None and stale:
            stale_value += value
        rows.append(
            PositionLiquidity(
                position.symbol, position.market_value, percentage, days, missing, stale
            )
        )
    complete = exposure.completeness is CompletenessStatus.COMPLETE and not any(
        item.missing or item.stale for item in rows
    )
    limitations = ("Average daily volume is not guaranteed executable liquidity.",)
    return PortfolioLiquidityReport(
        tuple(rows),
        _divide(missing_value, equity),
        _divide(stale_value, equity),
        _divide(breaching_value, equity),
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
        limitations,
    )


def stress_result(
    inputs: PortfolioRiskInput,
    exposure: PortfolioExposureReport,
    scenario: PortfolioStressScenario,
) -> PortfolioStressResult:
    classifications = {item.symbol: item for item in inputs.classifications}
    shocked: list[tuple[str, str | None]] = []
    affected: set[str] = set()
    missing: set[str] = set()
    unknown: set[str] = set()
    for position in inputs.positions:
        if position.market_value is None:
            shocked.append((position.symbol, None))
            unknown.add(position.symbol)
            continue
        factor = Decimal(1)
        classification = classifications.get(position.symbol)
        for shock in scenario.shocks:
            applies = shock.target_type == "portfolio"
            if shock.target_type == "symbol":
                applies = position.symbol == shock.target
            elif shock.target_type == "instrument":
                applies = position.instrument_type == shock.target
            elif shock.target_type in {"issuer", "sector", "industry"}:
                value = getattr(classification, shock.target_type) if classification else None
                if value is None:
                    missing.add(position.symbol)
                applies = value == shock.target
            if applies:
                factor *= Decimal(1) + exact(shock.percentage, "shock")
                affected.add(position.symbol)
        shocked.append((position.symbol, text(exact(position.market_value, "value") * factor)))
    cash = exact(inputs.cash_value, "cash", nonnegative=False)
    for shock in scenario.shocks:
        if shock.target_type == "cash":
            cash *= Decimal(1) + exact(shock.percentage, "shock")
    resulting = (
        cash
        + sum((exact(value, "shocked value") for _, value in shocked if value is not None), ZERO)
        if not unknown
        else None
    )
    initial = exact(exposure.total_equity, "equity") if exposure.total_equity else None
    loss = initial - resulting if initial is not None and resulting is not None else None
    complete = (
        initial is not None
        and resulting is not None
        and (not scenario.require_complete_classification or not missing)
    )
    return PortfolioStressResult(
        scenario.scenario_digest,
        text(initial) if initial is not None else None,
        tuple(shocked),
        text(cash),
        text(resulting) if resulting is not None else None,
        text(loss) if loss is not None else None,
        text(loss / initial) if loss is not None and initial else None,
        tuple(sorted(affected)),
        tuple(sorted(unknown)),
        tuple(sorted(missing)),
        CompletenessStatus.COMPLETE if complete else CompletenessStatus.PARTIAL,
    )


def _violates(limit: PortfolioRiskLimit, observed: Decimal) -> bool:
    threshold = exact(limit.threshold, "threshold")
    return {
        "gt": observed > threshold,
        "gte": observed >= threshold,
        "lt": observed < threshold,
        "lte": observed <= threshold,
        "abs_gt": abs(observed) > threshold,
    }[limit.operator]


def _metrics(
    exposure: PortfolioExposureReport,
    liquidity: PortfolioLiquidityReport,
    volatility: object,
    drawdown: object,
    beta: object,
    var: object,
    expected_shortfall: object,
    stress: tuple[PortfolioStressResult, ...],
) -> dict[str, list[tuple[str, str, tuple[str, ...]]]]:
    result: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}

    def add(metric: str, value: str | None, identity: str, subjects: tuple[str, ...] = ()) -> None:
        if value is not None:
            result.setdefault(metric, []).append((value, identity, subjects))

    for item in exposure.positions:
        add("single_position_weight", item.weight, exposure.calculation_identity, (item.symbol,))
    for item in exposure.classifications:
        mapping = {
            "issuer": "issuer_concentration",
            "sector": "sector_concentration",
            "industry": "industry_concentration",
            "instrument": "etf_concentration"
            if item.classification == "ETF"
            else "instrument_concentration",
        }
        metric = mapping[item.classification_type]
        if metric in {
            "issuer_concentration",
            "sector_concentration",
            "industry_concentration",
            "etf_concentration",
        }:
            add(metric, item.weight, exposure.calculation_identity, (item.classification,))
    add("top_3_concentration", exposure.top_3_concentration, exposure.calculation_identity)
    add("top_5_concentration", exposure.top_5_concentration, exposure.calculation_identity)
    add("cash_percentage", exposure.cash_percentage, exposure.calculation_identity)
    add("invested_percentage", exposure.invested_percentage, exposure.calculation_identity)
    add("stale_position_weight", exposure.stale_position_weight, exposure.calculation_identity)
    add(
        "unpriced_position_weight", exposure.unpriced_position_weight, exposure.calculation_identity
    )
    add("historical_volatility", volatility.annualized_volatility, volatility.calculation_identity)
    add("downside_volatility", volatility.downside_deviation, volatility.calculation_identity)
    add("maximum_drawdown", drawdown.maximum_drawdown, drawdown.calculation_identity)
    if beta:
        add("tracking_error", beta.annualized_tracking_error, beta.calculation_identity)
        add("absolute_beta", beta.beta, beta.calculation_identity)
    add("value_at_risk", var.loss_percentage, var.calculation_identity)
    add(
        "expected_shortfall",
        expected_shortfall.mean_tail_loss_percentage,
        expected_shortfall.calculation_identity,
    )
    for item in stress:
        add("stress_loss", item.percentage_loss, item.result_identity, (item.scenario_identity,))
    add("position_count", str(len(exposure.positions)), exposure.calculation_identity)
    return result


def evaluate_limits(
    inputs: PortfolioRiskInput,
    metrics: dict[str, list[tuple[str, str, tuple[str, ...]]]],
) -> tuple[PortfolioRiskLimitViolation, ...]:
    violations: list[PortfolioRiskLimitViolation] = []
    for limit in inputs.policy.limits:
        for value, calculation, subjects in metrics.get(limit.metric, []):
            if limit.subject is not None and limit.subject not in subjects:
                continue
            if _violates(limit, exact(value, "observed value")):
                violations.append(
                    PortfolioRiskLimitViolation(
                        limit.limit_id,
                        limit.metric,
                        value,
                        limit.threshold,
                        limit.operator,
                        limit.severity,
                        subjects,
                        inputs.input_identity,
                        calculation,
                        f"{limit.metric}_{limit.operator}_threshold",
                    )
                )
    return tuple(sorted(violations, key=lambda item: (item.limit_id, item.subjects)))


def analyze_portfolio_risk(
    inputs: PortfolioRiskInput,
    *,
    report_timestamp: datetime,
    scenarios: tuple[PortfolioStressScenario, ...] = (),
) -> PortfolioRiskReport:
    if len(scenarios) > inputs.policy.maximum_scenario_count:
        raise FinancialDataValidationError("stress scenario count exceeds policy")
    if report_timestamp != inputs.as_of:
        raise FinancialDataValidationError("report timestamp must equal exact input as_of")
    exposure = exposure_report(inputs)
    liquidity = liquidity_report(inputs, exposure)
    volatility = volatility_report(inputs.portfolio_returns, inputs.policy)
    drawdown = drawdown_report(inputs.portfolio_returns, history_complete=inputs.history_complete)
    correlation = correlation_matrix(inputs.asset_returns, inputs.policy)
    beta = beta_report(inputs.portfolio_returns, inputs.benchmark_returns, inputs.policy)
    equity = exact(exposure.total_equity, "equity") if exposure.total_equity else None
    var, expected_shortfall = tail_reports(
        inputs.portfolio_returns, equity, inputs.valuation_identity, inputs.policy
    )
    stress = tuple(
        stress_result(inputs, exposure, item)
        for item in sorted(scenarios, key=lambda x: x.scenario_id)
    )
    violations = evaluate_limits(
        inputs,
        _metrics(exposure, liquidity, volatility, drawdown, beta, var, expected_shortfall, stress),
    )
    stale: list[str] = []
    incomplete: list[str] = []
    unavailable: list[str] = []
    if (
        inputs.as_of
        - next(
            item.source_timestamp
            for item in inputs.provenance
            if item.source_kind == "broker_snapshot"
        )
        > inputs.policy.maximum_portfolio_age
    ):
        stale.append("portfolio_snapshot")
    valuation_provenance = next(
        item for item in inputs.provenance if item.source_kind == "valuation"
    )
    if inputs.as_of - valuation_provenance.source_timestamp > inputs.policy.maximum_valuation_age:
        stale.append("valuation")
    stale.extend(f"price:{item.symbol}" for item in inputs.positions if item.stale)
    if not inputs.portfolio_complete:
        incomplete.append("portfolio")
    if not inputs.history_complete:
        incomplete.append("return_history")
    for name, report in (
        ("volatility", volatility),
        ("drawdown", drawdown),
        ("value_at_risk", var),
        ("expected_shortfall", expected_shortfall),
    ):
        if report.unavailable_reason is not None:
            unavailable.append(name)
    if beta is not None and beta.unavailable_reason is not None:
        unavailable.append("beta")
    overall_complete = (
        not stale
        and not incomplete
        and not unavailable
        and all(
            item.completeness is CompletenessStatus.COMPLETE
            for item in (
                exposure,
                liquidity,
                volatility,
                drawdown,
                correlation,
                var,
                expected_shortfall,
                *stress,
            )
        )
    )
    blocking = any(item.severity == "blocking" for item in violations)
    eligible = overall_complete and not blocking
    limitations = (
        "Risk metrics are deterministic estimates, not external facts or guaranteed losses.",
        "Risk-limit passage is advisory and does not approve, place, schedule, or authorize a trade.",
        "Correlation alone does not establish diversification.",
    )
    return PortfolioRiskReport(
        "sigil-risk-report-v1",
        inputs.policy.policy_identity,
        inputs.account_binding,
        inputs.snapshot_identity,
        inputs.accounting_state_identity,
        inputs.valuation_identity,
        report_timestamp,
        inputs.acquisition_duration_seconds,
        exposure,
        liquidity,
        volatility,
        drawdown,
        correlation,
        beta,
        var,
        expected_shortfall,
        stress,
        violations,
        tuple(sorted(incomplete)),
        tuple(sorted(unavailable)),
        tuple(sorted(set(stale))),
        CompletenessStatus.COMPLETE if overall_complete else CompletenessStatus.PARTIAL,
        eligible,
        inputs.provenance,
        limitations,
    )


def verify_report_identity(report: PortfolioRiskReport) -> bool:
    return replace(report, report_identity="").report_identity == report.report_identity


def provenance_summary(report: PortfolioRiskReport) -> tuple[tuple[str, str], ...]:
    return tuple((item.source_kind, item.source_identity) for item in report.provenance)


def list_limit_violations(report: PortfolioRiskReport) -> tuple[PortfolioRiskLimitViolation, ...]:
    return report.violations


def lookup_stress_result(
    report: PortfolioRiskReport, scenario_identity: str
) -> PortfolioStressResult | None:
    return next(
        (item for item in report.stress_results if item.scenario_identity == scenario_identity),
        None,
    )
