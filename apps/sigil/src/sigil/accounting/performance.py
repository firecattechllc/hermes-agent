"""Deterministic portfolio return and governed period-close calculations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from .models import (
    AccountingPeriodClose,
    BenchmarkPerformance,
    CompletenessStatus,
    PortfolioAccountingPolicy,
    PortfolioAccountingState,
    PortfolioAccountingUnavailable,
    PortfolioLedgerDiscrepancy,
    PortfolioPerformancePeriod,
    PortfolioPerformanceReport,
    PortfolioValuation,
    decimal_text,
)


ZERO = Decimal(0)
ONE = Decimal(1)


def _text(value: Decimal, scale: int) -> str:
    quantum = Decimal(1).scaleb(-scale)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    result = decimal_text(format(rounded, "f"), "return", nonnegative=False)
    assert result is not None
    return result


class PortfolioPerformanceService:
    """Computes returns solely from caller-supplied valuations and dated flows."""

    def time_weighted_return(
        self,
        valuations: tuple[PortfolioValuation, ...],
        external_cash_flows: tuple[tuple[datetime, str], ...],
        policy: PortfolioAccountingPolicy,
    ) -> str:
        ordered = tuple(sorted(valuations, key=lambda item: item.valuation_timestamp))
        if len(ordered) < 2:
            raise PortfolioAccountingUnavailable("time-weighted return needs two valuations")
        if any(
            item.completeness_status is not CompletenessStatus.COMPLETE
            or item.total_equity is None
            for item in ordered
        ):
            raise PortfolioAccountingUnavailable("time-weighted return needs complete valuations")
        flows: dict[datetime, Decimal] = {}
        for at, amount in external_cash_flows:
            flows[at] = flows.get(at, ZERO) + Decimal(
                str(decimal_text(amount, "cash flow", nonnegative=False))
            )
        product = ONE
        for beginning, ending in zip(ordered, ordered[1:]):
            denominator = Decimal(str(beginning.total_equity))
            if denominator <= 0:
                raise PortfolioAccountingUnavailable("invalid time-weighted denominator")
            flow = sum(
                (
                    amount
                    for at, amount in flows.items()
                    if beginning.valuation_timestamp < at <= ending.valuation_timestamp
                ),
                ZERO,
            )
            product *= (Decimal(str(ending.total_equity)) - flow) / denominator
        return _text(product - ONE, policy.return_scale)

    def money_weighted_return(
        self,
        beginning: PortfolioValuation,
        ending: PortfolioValuation,
        external_cash_flows: tuple[tuple[datetime, str], ...],
        policy: PortfolioAccountingPolicy,
    ) -> tuple[str | None, str | None]:
        if (
            beginning.total_equity is None
            or ending.total_equity is None
            or beginning.completeness_status is not CompletenessStatus.COMPLETE
            or ending.completeness_status is not CompletenessStatus.COMPLETE
            or ending.valuation_timestamp <= beginning.valuation_timestamp
        ):
            return None, "incomplete_or_invalid_valuations"
        cash_flows: list[tuple[Decimal, Decimal]] = [(ZERO, -Decimal(beginning.total_equity))]
        duration = Decimal(
            (ending.valuation_timestamp - beginning.valuation_timestamp).total_seconds()
        )
        for at, amount in sorted(external_cash_flows):
            if beginning.valuation_timestamp < at <= ending.valuation_timestamp:
                fraction = Decimal(
                    (at - beginning.valuation_timestamp).total_seconds()
                ) / duration
                cash_flows.append((fraction, -Decimal(amount)))
        cash_flows.append((ONE, Decimal(ending.total_equity)))
        lower = Decimal(policy.money_weighted_lower_bound)
        upper = Decimal(policy.money_weighted_upper_bound)
        tolerance = Decimal(policy.money_weighted_tolerance)

        def npv(rate: Decimal) -> Decimal:
            if rate <= -ONE:
                raise PortfolioAccountingUnavailable("money-weighted domain is invalid")
            with localcontext() as context:
                context.prec = 50
                return sum(
                    (amount / ((ONE + rate) ** fraction) for fraction, amount in cash_flows),
                    ZERO,
                )

        low_value = npv(lower)
        high_value = npv(upper)
        if low_value == ZERO:
            return _text(lower, policy.return_scale), None
        if high_value == ZERO:
            return _text(upper, policy.return_scale), None
        if (low_value > 0) == (high_value > 0):
            return None, "no_root_in_bounded_domain"
        midpoint = ZERO
        for _ in range(policy.money_weighted_max_iterations):
            midpoint = (lower + upper) / 2
            value = npv(midpoint)
            if abs(value) <= tolerance or upper - lower <= tolerance:
                return _text(midpoint, policy.return_scale), None
            if (value > 0) == (low_value > 0):
                lower, low_value = midpoint, value
            else:
                upper = midpoint
        return None, "convergence_iteration_limit"

    def report(
        self,
        *,
        state: PortfolioAccountingState,
        beginning: PortfolioValuation,
        ending: PortfolioValuation,
        external_cash_flows: tuple[tuple[datetime, str], ...],
        policy: PortfolioAccountingPolicy,
        realized_gain_loss: str,
        unrealized_gain_loss: str,
        benchmark: tuple[str, PortfolioValuation, PortfolioValuation] | None = None,
        lifetime_claim: bool = False,
    ) -> PortfolioPerformanceReport:
        twr = self.time_weighted_return((beginning, ending), external_cash_flows, policy)
        mwr, mwr_reason = self.money_weighted_return(
            beginning, ending, external_cash_flows, policy
        )
        contributions = sum(
            (Decimal(amount) for _, amount in external_cash_flows if Decimal(amount) > 0),
            ZERO,
        )
        withdrawals = -sum(
            (Decimal(amount) for _, amount in external_cash_flows if Decimal(amount) < 0),
            ZERO,
        )
        beginning_equity = Decimal(str(beginning.total_equity))
        ending_equity = Decimal(str(ending.total_equity))
        net_flow = contributions - withdrawals
        period = PortfolioPerformancePeriod(
            beginning.valuation_timestamp,
            ending.valuation_timestamp,
            str(beginning.total_equity),
            str(ending.total_equity),
            _text(contributions, policy.return_scale),
            _text(withdrawals, policy.return_scale),
            _text(net_flow, policy.return_scale),
            _text(ending_equity - beginning_equity - net_flow, policy.return_scale),
            str(decimal_text(realized_gain_loss, "realized gain", nonnegative=False)),
            str(decimal_text(unrealized_gain_loss, "unrealized gain", nonnegative=False)),
            state.cumulative_dividends,
            state.cumulative_interest,
            state.cumulative_fees,
            twr,
            mwr,
            mwr_reason,
        )
        benchmark_result = None
        if benchmark is not None:
            identity, benchmark_begin, benchmark_end = benchmark
            benchmark_return = self.time_weighted_return(
                (benchmark_begin, benchmark_end), (), policy
            )
            benchmark_result = BenchmarkPerformance(
                identity,
                benchmark_begin.valuation_timestamp,
                benchmark_end.valuation_timestamp,
                benchmark_return,
                _text(Decimal(twr) - Decimal(benchmark_return), policy.return_scale),
                CompletenessStatus.COMPLETE,
                False,
            )
        return PortfolioPerformanceReport(
            state.account_binding,
            period,
            benchmark_result,
            state.history_complete,
            lifetime_claim,
            CompletenessStatus.COMPLETE,
        )

    def close_period(
        self,
        *,
        state: PortfolioAccountingState,
        opening_state_digest: str,
        valuation: PortfolioValuation,
        report: PortfolioPerformanceReport,
        discrepancies: tuple[PortfolioLedgerDiscrepancy, ...],
        first_sequence: int,
        approval_identity: str,
        closed_at: datetime,
    ) -> AccountingPeriodClose:
        if not state.history_complete:
            raise PortfolioAccountingUnavailable("period close requires complete history")
        if state.unresolved_activity_count or any(item.material for item in discrepancies):
            raise PortfolioAccountingUnavailable("period close is blocked by discrepancies")
        if valuation.completeness_status is not CompletenessStatus.COMPLETE:
            raise PortfolioAccountingUnavailable("period close requires a complete valuation")
        return AccountingPeriodClose(
            state.account_binding,
            report.period.period_start,
            report.period.period_end,
            first_sequence,
            state.last_processed_sequence,
            state.ledger_chain_head,
            opening_state_digest,
            state.state_digest,
            valuation.valuation_identity,
            report.report_digest,
            0,
            CompletenessStatus.COMPLETE,
            approval_identity,
            closed_at,
        )
