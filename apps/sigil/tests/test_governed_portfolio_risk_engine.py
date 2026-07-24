from __future__ import annotations

import socket
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sigil.accounting import CompletenessStatus
from sigil.integrations.providers import FinancialDataValidationError
from sigil.risk.engine import analyze_portfolio_risk, exposure_report, verify_report_identity
from sigil.risk.models import (
    PortfolioRiskInput,
    PortfolioRiskLimit,
    PortfolioRiskPolicy,
    PortfolioRiskProvenance,
    PortfolioStressScenario,
    ProposedTradeRiskInput,
    RiskBenchmarkObservation,
    RiskClassification,
    RiskLiquidityObservation,
    RiskPosition,
    RiskReturnObservation,
    StressShock,
)
from sigil.risk.pretrade import compare_proposed_trade
from sigil.risk.statistics import (
    beta_report,
    correlation_matrix,
    drawdown_report,
    tail_reports,
    volatility_report,
)

NOW = datetime(2026, 7, 24, 14, tzinfo=UTC)
DIGEST = "1" * 64


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in Step 13 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def returns(
    identity: str = "PORT", values: tuple[str, ...] = ("0.1", "-0.2", "0.05", "-0.1", "0.2")
):
    return tuple(
        RiskReturnObservation(
            identity,
            NOW - timedelta(days=len(values) - index),
            NOW - timedelta(days=len(values) - index - 1),
            value,
            f"source-{identity}",
            DIGEST,
            NOW,
        )
        for index, value in enumerate(values)
    )


def provenance(kind: str) -> PortfolioRiskProvenance:
    return PortfolioRiskProvenance(
        kind,
        f"{kind}-identity",
        DIGEST,
        NOW - timedelta(minutes=1),
        NOW,
        CompletenessStatus.COMPLETE,
        account_binding="account-1" if kind != "risk_policy" else None,
    )


def policy(*limits: PortfolioRiskLimit, **changes: object) -> PortfolioRiskPolicy:
    values: dict[str, object] = {"limits": limits}
    values.update(changes)
    return PortfolioRiskPolicy(**values)  # type: ignore[arg-type]


def risk_input(**changes: object) -> PortfolioRiskInput:
    selected_policy = changes.pop("policy", policy())
    values: dict[str, object] = {
        "account_binding": "account-1",
        "snapshot_identity": "2" * 64,
        "accounting_state_identity": "3" * 64,
        "valuation_identity": "4" * 64,
        "as_of": NOW,
        "cash_value": "300",
        "positions": (
            RiskPosition("AAA", "EQUITY", "2", "100", "200", NOW, False),
            RiskPosition("BBB", "ETF", "5", "100", "500", NOW, False),
        ),
        "portfolio_returns": returns(),
        "asset_returns": (
            ("AAA", returns("AAA")),
            ("BBB", returns("BBB", ("0.2", "-0.1", "0", "-0.05", "0.1"))),
        ),
        "benchmark_returns": tuple(
            RiskBenchmarkObservation(
                identity="SPY",
                period_start=item.period_start,
                period_end=item.period_end,
                exact_return=value,
                source_identity="source-SPY",
                source_digest=DIGEST,
                acquired_at=NOW,
            )
            for item, value in zip(returns(), ("0.05", "-0.1", "0.03", "-0.05", "0.1"))
        ),
        "classifications": (
            RiskClassification("AAA", "ISSUER-A", "TECH", "SOFTWARE"),
            RiskClassification("BBB", "ISSUER-B", "FUNDS", "ETF-INDEX"),
        ),
        "liquidity": (
            RiskLiquidityObservation("AAA", NOW, "1000", "100000", "liq", DIGEST, NOW),
            RiskLiquidityObservation("BBB", NOW, "2000", "200000", "liq", DIGEST, NOW),
        ),
        "policy": selected_policy,
        "provenance": tuple(
            provenance(kind)
            for kind in ("broker_snapshot", "accounting_state", "valuation", "risk_policy")
        ),
        "portfolio_complete": True,
        "history_complete": True,
        "acquisition_duration_seconds": "1",
    }
    values.update(changes)
    return PortfolioRiskInput(**values)  # type: ignore[arg-type]


def test_valid_policy_and_deterministic_identity() -> None:
    assert policy().policy_identity == policy().policy_identity


@pytest.mark.parametrize("value", ("-0.1", "1.1"))
def test_invalid_policy_percentages(value: str) -> None:
    with pytest.raises(FinancialDataValidationError):
        policy(participation_rate=value)


def test_contradictory_policy_limits() -> None:
    with pytest.raises(FinancialDataValidationError, match="contradictory"):
        policy(minimum_return_observations=10, maximum_return_series_length=5)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (("confidence_level", "0.96", "confidence"), ("horizon_days", 2, "horizon")),
)
def test_unsupported_policy_choices(field: str, value: object, message: str) -> None:
    with pytest.raises(FinancialDataValidationError, match=message):
        policy(**{field: value})


def test_duplicate_limit_identity_rejected() -> None:
    limit = PortfolioRiskLimit("same", "single_position_weight", "0.5", "gt", "blocking")
    with pytest.raises(FinancialDataValidationError, match="duplicate"):
        policy(limit, limit)


def test_input_ordering_and_duplicate_symbols() -> None:
    original = risk_input()
    reordered = replace(original, positions=tuple(reversed(original.positions)), input_identity="")
    assert tuple(item.symbol for item in reordered.positions) == ("AAA", "BBB")
    with pytest.raises(FinancialDataValidationError, match="duplicate"):
        replace(
            original, positions=(original.positions[0], original.positions[0]), input_identity=""
        )


def test_duplicate_overlapping_future_and_invalid_returns() -> None:
    one = returns()[0]
    with pytest.raises(FinancialDataValidationError, match="duplicate"):
        risk_input(portfolio_returns=(one, one))
    overlap = replace(
        one,
        period_start=one.period_start + timedelta(hours=1),
        period_end=one.period_end + timedelta(hours=1),
    )
    with pytest.raises(FinancialDataValidationError, match="overlapping"):
        risk_input(portfolio_returns=(one, overlap))
    with pytest.raises(FinancialDataValidationError, match="future"):
        risk_input(
            portfolio_returns=(replace(one, period_start=NOW, period_end=NOW + timedelta(days=1)),)
        )
    with pytest.raises(FinancialDataValidationError, match="negative one"):
        replace(one, exact_return="-1.01")


def test_secret_bearing_input_rejected() -> None:
    with pytest.raises(FinancialDataValidationError, match="secret"):
        policy(version="authorization")


def test_complete_exposure_concentration_and_hhi() -> None:
    report = exposure_report(risk_input())
    assert report.total_equity == "1000"
    assert report.total_position_market_value == "700"
    assert report.cash_percentage == "0.3"
    assert report.invested_percentage == "0.7"
    assert tuple(item.weight for item in report.positions) == ("0.2", "0.5")
    assert report.top_1_concentration == "0.5"
    assert report.top_3_concentration == "0.7"
    assert report.top_5_concentration == "0.7"
    assert report.concentration_hhi == "0.29"
    assert {
        (item.classification_type, item.classification, item.weight)
        for item in report.classifications
    } >= {
        ("issuer", "ISSUER-A", "0.2"),
        ("sector", "TECH", "0.2"),
        ("industry", "SOFTWARE", "0.2"),
        ("instrument", "ETF", "0.5"),
    }


def test_unknown_classification_partial_unpriced_and_stale() -> None:
    inputs = risk_input(
        positions=(
            RiskPosition("AAA", "EQUITY", "2", "100", "200", NOW, True),
            RiskPosition("BBB", "ETF", "5", None, None, None, False),
        ),
        classifications=(),
        portfolio_complete=False,
    )
    report = exposure_report(inputs)
    assert report.completeness is CompletenessStatus.PARTIAL
    assert report.total_equity is None
    assert report.unpriced_position_weight is None
    assert any(item.classification == "UNKNOWN" for item in report.classifications)
    assert report.stale_position_weight is None


def test_liquidity_values_missing_and_stale() -> None:
    complete = analyze_portfolio_risk(risk_input(), report_timestamp=NOW)
    assert complete.liquidity.positions[0].percentage_of_daily_dollar_volume == "0.002"
    assert complete.liquidity.positions[0].days_to_liquidate == "0.02"
    missing = analyze_portfolio_risk(risk_input(liquidity=()), report_timestamp=NOW)
    assert missing.liquidity.missing_liquidity_weight == "0.7"
    stale_observations = tuple(
        replace(item, observed_at=NOW - timedelta(days=3)) for item in risk_input().liquidity
    )
    stale = analyze_portfolio_risk(risk_input(liquidity=stale_observations), report_timestamp=NOW)
    assert stale.liquidity.stale_liquidity_weight == "0.7"


def test_volatility_formulas_and_insufficient_observations() -> None:
    report = volatility_report(
        returns(values=("0.1", "-0.1")), policy(minimum_return_observations=2)
    )
    assert report.arithmetic_mean_return == "0"
    assert report.population_volatility == "0.1"
    assert report.sample_volatility.startswith("0.141421")
    assert report.annualized_volatility is not None
    assert report.downside_deviation.startswith("0.070710")
    unavailable = volatility_report(returns(values=("0.1",)), policy())
    assert unavailable.unavailable_reason is not None
    assert (
        report.calculation_identity
        == volatility_report(
            returns(values=("0.1", "-0.1")), policy(minimum_return_observations=2)
        ).calculation_identity
    )


def test_drawdown_path_maximum_current_recovery_and_limited_history() -> None:
    report = drawdown_report(returns(values=("0.1", "-0.2", "0.3", "-0.1")), history_complete=True)
    assert len(report.wealth_path) == 5
    assert report.maximum_drawdown == "-0.2"
    assert report.recovery_timestamp is not None
    assert report.current_drawdown == "-0.1"
    assert report.longest_drawdown_duration_days == 2
    limited = drawdown_report(returns(), history_complete=False)
    assert not limited.lifetime_claim
    assert limited.completeness is CompletenessStatus.PARTIAL


def test_covariance_correlation_symmetry_zero_variance_and_no_forward_fill() -> None:
    matrix = correlation_matrix(risk_input().asset_returns, policy())
    assert matrix.symbols == ("AAA", "BBB")
    cross = next(item for item in matrix.pairs if item.left == "AAA" and item.right == "BBB")
    assert cross.covariance is not None
    assert cross.correlation is not None
    diagonal = next(item for item in matrix.pairs if item.left == item.right == "AAA")
    assert diagonal.correlation == "1"
    constant = returns("CCC", ("0.1", "0.1", "0.1"))
    zero = correlation_matrix((("CCC", constant),), policy())
    assert zero.pairs[0].correlation is None
    shifted = tuple(
        replace(
            item,
            period_start=item.period_start + timedelta(hours=1),
            period_end=item.period_end + timedelta(hours=1),
        )
        for item in constant
    )
    no_overlap = correlation_matrix((("A", constant), ("B", shifted)), policy())
    assert (
        next(
            item for item in no_overlap.pairs if item.left == "A" and item.right == "B"
        ).overlap_count
        == 0
    )


def test_beta_tracking_error_zero_variance_and_period_mismatch() -> None:
    inputs = risk_input()
    report = beta_report(inputs.portfolio_returns, inputs.benchmark_returns, inputs.policy)
    assert report is not None and report.beta is not None
    assert report.tracking_error is not None
    assert report.annualized_tracking_error is not None
    constant = tuple(replace(item, exact_return="0.1") for item in inputs.benchmark_returns)
    zero = beta_report(inputs.portfolio_returns, constant, inputs.policy)
    assert zero is not None and zero.beta is None
    shifted = tuple(
        replace(
            item,
            period_start=item.period_start + timedelta(hours=1),
            period_end=item.period_end + timedelta(hours=1),
        )
        for item in inputs.benchmark_returns
    )
    mismatch = beta_report(inputs.portfolio_returns, shifted, inputs.policy)
    assert mismatch is not None and mismatch.beta is None


@pytest.mark.parametrize("confidence", ("0.9", "0.95", "0.99"))
def test_historical_var_expected_shortfall_and_deterministic_percentile(confidence: str) -> None:
    observations = returns(
        values=("-0.3", "-0.2", "-0.1", "0", "0.1", "0.2", "0.3", "0.4", "0.5", "0.6")
    )
    selected = policy(confidence_level=confidence)
    var, expected = tail_reports(observations, Decimal(1000), DIGEST, selected)
    assert var.method == "historical"
    assert var.loss_percentage is not None and var.loss_amount is not None
    assert expected.mean_tail_loss_percentage is not None
    assert expected.worst_observed_loss == "0.3"
    assert (
        var.calculation_identity
        == tail_reports(observations, Decimal(1000), DIGEST, selected)[0].calculation_identity
    )


def test_var_and_expected_shortfall_insufficient() -> None:
    var, expected = tail_reports(returns(values=("0.1",)), Decimal(1000), DIGEST, policy())
    assert var.loss_percentage is None
    assert expected.mean_tail_loss_percentage is None


@pytest.mark.parametrize(
    "shock",
    (
        StressShock("portfolio", "ALL", "-0.1"),
        StressShock("symbol", "AAA", "-0.2"),
        StressShock("sector", "TECH", "-0.3"),
    ),
)
def test_stress_scenarios_and_deterministic_identity(shock: StressShock) -> None:
    scenario = PortfolioStressScenario(
        "stress-1", "stress", "v1", "fixture", (shock,), "operator", NOW
    )
    report = analyze_portfolio_risk(risk_input(), report_timestamp=NOW, scenarios=(scenario,))
    result = report.stress_results[0]
    assert result.absolute_loss is not None
    assert (
        result.result_identity
        == analyze_portfolio_risk(risk_input(), report_timestamp=NOW, scenarios=(scenario,))
        .stress_results[0]
        .result_identity
    )


def test_combined_stress_missing_classification_and_rejections() -> None:
    scenario = PortfolioStressScenario(
        "combined",
        "combined",
        "v1",
        "fixture",
        (StressShock("symbol", "AAA", "-0.1"), StressShock("sector", "TECH", "-0.1")),
        "operator",
        NOW,
    )
    result = analyze_portfolio_risk(
        risk_input(classifications=()), report_timestamp=NOW, scenarios=(scenario,)
    ).stress_results[0]
    assert result.missing_classifications == ("AAA", "BBB")
    assert result.completeness is CompletenessStatus.PARTIAL
    with pytest.raises(FinancialDataValidationError, match="duplicate"):
        replace(scenario, shocks=(scenario.shocks[0], scenario.shocks[0]), scenario_digest="")
    with pytest.raises(FinancialDataValidationError, match="total loss"):
        StressShock("portfolio", "ALL", "-1.1")


@pytest.mark.parametrize("severity", ("informational", "warning", "blocking"))
def test_limit_severity_visibility_and_determinism(severity: str) -> None:
    limit = PortfolioRiskLimit("concentration", "single_position_weight", "0.4", "gt", severity)
    report = analyze_portfolio_risk(risk_input(policy=policy(limit)), report_timestamp=NOW)
    assert len(report.violations) == 1
    assert report.violations[0].severity == severity
    assert report.pre_trade_eligible is (severity != "blocking")


def test_complete_report_identity_material_changes_and_audit() -> None:
    original = analyze_portfolio_risk(risk_input(), report_timestamp=NOW)
    assert original.overall_completeness is CompletenessStatus.COMPLETE
    assert verify_report_identity(original)
    changed_position = analyze_portfolio_risk(
        risk_input(
            positions=(
                RiskPosition("AAA", "EQUITY", "3", "100", "300", NOW, False),
                risk_input().positions[1],
            )
        ),
        report_timestamp=NOW,
    )
    changed_policy = analyze_portfolio_risk(
        risk_input(policy=policy(confidence_level="0.9")), report_timestamp=NOW
    )
    scenario = PortfolioStressScenario(
        "s", "s", "v1", "fixture", (StressShock("portfolio", "ALL", "-0.1"),), "operator", NOW
    )
    changed_scenario = analyze_portfolio_risk(
        risk_input(), report_timestamp=NOW, scenarios=(scenario,)
    )
    assert (
        len(
            {
                original.report_identity,
                changed_position.report_identity,
                changed_policy.report_identity,
                changed_scenario.report_identity,
            }
        )
        == 4
    )
    assert not original.pre_trade_eligible is False
    assert all(
        "approve" in item.lower() or "estimate" in item.lower() or "correlation" in item.lower()
        for item in original.limitations
    )


def proposal(side: str, **changes: object) -> ProposedTradeRiskInput:
    values: dict[str, object] = {
        "account_binding": "account-1",
        "symbol": "AAA",
        "instrument_type": "EQUITY",
        "side": side,
        "quantity": "1",
        "notional": None,
        "proposed_price": "100",
        "price_timestamp": NOW,
        "estimated_fees": "1",
        "proposal_identity": f"proposal-{side}",
        "simulation_timestamp": NOW,
    }
    values.update(changes)
    return ProposedTradeRiskInput(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(("side", "expected_cash"), (("BUY", "199"), ("SELL", "399")))
def test_valid_buy_sell_simulation_is_read_only_and_never_approval(
    side: str, expected_cash: str
) -> None:
    inputs = risk_input()
    before = inputs.input_identity
    result = compare_proposed_trade(inputs, proposal(side))
    assert result.post_trade_cash == expected_cash
    assert not result.trade_approved
    assert inputs.input_identity == before


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"quantity": "10"}, "insufficient_cash"),
        ({"side": "SELL", "quantity": "3"}, "sell_exceeds_holdings"),
        ({"account_binding": "other"}, "account_mismatch"),
        ({"price_timestamp": NOW - timedelta(hours=1)}, "stale_proposed_price"),
    ),
)
def test_proposed_trade_fail_closed(changes: dict[str, object], reason: str) -> None:
    side = str(changes.pop("side", "BUY"))
    result = compare_proposed_trade(risk_input(), proposal(side, **changes))
    assert not result.risk_eligible
    assert reason in result.ineligibility_reasons


def test_partial_portfolio_and_blocking_violation_fail_closed_warning_visible() -> None:
    partial = compare_proposed_trade(risk_input(portfolio_complete=False), proposal("BUY"))
    assert "partial_portfolio" in partial.ineligibility_reasons
    blocking = PortfolioRiskLimit("block", "single_position_weight", "0.3", "gt", "blocking")
    warning = PortfolioRiskLimit("warn", "single_position_weight", "0.3", "gt", "warning")
    result = compare_proposed_trade(risk_input(policy=policy(blocking, warning)), proposal("BUY"))
    assert not result.risk_eligible
    assert {item.severity for item in result.post_trade_violations} == {"blocking", "warning"}


@pytest.mark.parametrize("basis_points", range(1, 90))
def test_deterministic_regeneration_and_return_hash_sensitivity(
    basis_points: int,
) -> None:
    first_return = Decimal(basis_points) / Decimal(10000)
    observations = returns(values=(str(first_return), "-0.2", "0.05", "-0.1", "0.2"))
    inputs = risk_input(portfolio_returns=observations)
    report = analyze_portfolio_risk(inputs, report_timestamp=NOW)
    regenerated = analyze_portfolio_risk(inputs, report_timestamp=NOW)
    baseline = analyze_portfolio_risk(risk_input(), report_timestamp=NOW)
    assert report.report_identity == regenerated.report_identity
    assert report.report_identity != baseline.report_identity
