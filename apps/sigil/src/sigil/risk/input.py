"""Exact Step 11/12 to Step 13 input construction."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sigil.accounting.models import (
    CompletenessStatus,
    PortfolioAccountingState,
    PortfolioValuation,
)
from sigil.integrations.providers.models import FinancialDataValidationError
from sigil.portfolio.models import BrokeragePortfolioSnapshot

from .models import (
    PortfolioRiskInput,
    PortfolioRiskPolicy,
    PortfolioRiskProvenance,
    RiskBenchmarkObservation,
    RiskClassification,
    RiskLiquidityObservation,
    RiskPosition,
    RiskReturnObservation,
    exact,
)


def build_risk_input(
    snapshot: BrokeragePortfolioSnapshot,
    accounting_state: PortfolioAccountingState,
    valuation: PortfolioValuation,
    *,
    policy: PortfolioRiskPolicy,
    as_of: datetime,
    portfolio_returns: tuple[RiskReturnObservation, ...] = (),
    asset_returns: tuple[tuple[str, tuple[RiskReturnObservation, ...]], ...] = (),
    benchmark_returns: tuple[RiskBenchmarkObservation, ...] = (),
    classifications: tuple[RiskClassification, ...] = (),
    liquidity: tuple[RiskLiquidityObservation, ...] = (),
    extra_provenance: tuple[PortfolioRiskProvenance, ...] = (),
) -> PortfolioRiskInput:
    if (
        len({snapshot.account_binding, accounting_state.account_binding, valuation.account_binding})
        != 1
    ):
        raise FinancialDataValidationError("risk account binding mismatch")
    if valuation.portfolio_snapshot_identity != snapshot.snapshot_id:
        raise FinancialDataValidationError("risk snapshot and valuation mismatch")
    if policy.currency != snapshot.account.currency:
        raise FinancialDataValidationError("risk currency mismatch")
    state_symbols = dict(accounting_state.position_quantities)
    valuation_symbols = {item.symbol for item in valuation.positions}
    snapshot_symbols = {item.symbol for item in snapshot.positions}
    if state_symbols.keys() != valuation_symbols or valuation_symbols != snapshot_symbols:
        raise FinancialDataValidationError("risk input symbols mismatch")
    instruments = {item.symbol: item.instrument_type for item in snapshot.positions}
    positions: list[RiskPosition] = []
    tolerance = exact(policy.valuation_tolerance, "valuation_tolerance")
    for item in valuation.positions:
        quantity = exact(item.quantity, "quantity")
        if quantity != exact(state_symbols[item.symbol], "accounting quantity"):
            raise FinancialDataValidationError("valuation quantity mismatch")
        if item.price is not None and item.market_value is not None:
            expected = quantity * exact(item.price, "price")
            if abs(expected - exact(item.market_value, "market_value")) > tolerance:
                raise FinancialDataValidationError("valuation market value does not reconcile")
        positions.append(
            RiskPosition(
                item.symbol,
                instruments[item.symbol],
                item.quantity,
                item.price,
                item.market_value,
                item.price_timestamp,
                item.stale,
                policy.currency,
            )
        )
    if (
        snapshot.acquired_completed_at - snapshot.acquired_started_at
        > policy.maximum_acquisition_duration
    ):
        raise FinancialDataValidationError("snapshot acquisition duration exceeds policy")
    provenance = [
        PortfolioRiskProvenance(
            "broker_snapshot",
            snapshot.snapshot_id,
            snapshot.snapshot_id,
            snapshot.account.provider_timestamp,
            snapshot.acquired_completed_at,
            CompletenessStatus.COMPLETE if snapshot.complete else CompletenessStatus.PARTIAL,
            not snapshot.complete,
            account_binding=snapshot.account_binding,
        ),
        PortfolioRiskProvenance(
            "accounting_state",
            accounting_state.state_digest,
            accounting_state.state_digest,
            valuation.source_timestamp,
            valuation.acquired_at,
            CompletenessStatus.COMPLETE
            if accounting_state.history_complete
            else CompletenessStatus.PARTIAL,
            not accounting_state.history_complete,
            account_binding=accounting_state.account_binding,
        ),
        PortfolioRiskProvenance(
            "valuation",
            valuation.valuation_identity,
            valuation.valuation_identity,
            valuation.valuation_timestamp,
            valuation.acquired_at,
            valuation.completeness_status,
            valuation.completeness_status is CompletenessStatus.TRUNCATED,
            account_binding=valuation.account_binding,
        ),
        PortfolioRiskProvenance(
            "risk_policy",
            policy.policy_identity,
            policy.policy_identity,
            as_of,
            as_of,
            CompletenessStatus.COMPLETE,
        ),
        *extra_provenance,
    ]
    acquisition_seconds = Decimal(
        str((snapshot.acquired_completed_at - snapshot.acquired_started_at).total_seconds())
    )
    return PortfolioRiskInput(
        snapshot.account_binding,
        snapshot.snapshot_id,
        accounting_state.state_digest,
        valuation.valuation_identity,
        as_of,
        valuation.cash_value,
        tuple(positions),
        portfolio_returns,
        asset_returns,
        benchmark_returns,
        classifications,
        liquidity,
        policy,
        tuple(provenance),
        snapshot.complete and valuation.completeness_status is CompletenessStatus.COMPLETE,
        accounting_state.history_complete,
        str(acquisition_seconds),
    )
