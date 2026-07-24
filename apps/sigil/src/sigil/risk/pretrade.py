"""Pure read-only proposed-trade portfolio risk simulation."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from sigil.accounting.models import CompletenessStatus

from .engine import analyze_portfolio_risk
from .models import (
    PortfolioRiskInput,
    ProposedTradeRiskComparison,
    ProposedTradeRiskInput,
    RiskPosition,
    exact,
)
from .statistics import text


def compare_proposed_trade(
    inputs: PortfolioRiskInput,
    proposal: ProposedTradeRiskInput,
    *,
    scenarios: tuple = (),
) -> ProposedTradeRiskComparison:
    reasons: list[str] = []
    if proposal.account_binding != inputs.account_binding:
        reasons.append("account_mismatch")
    if not inputs.portfolio_complete:
        reasons.append("partial_portfolio")
    valuation_source = next(item for item in inputs.provenance if item.source_kind == "valuation")
    if inputs.as_of - valuation_source.source_timestamp > inputs.policy.maximum_valuation_age:
        reasons.append("stale_valuation")
    if proposal.simulation_timestamp - proposal.price_timestamp > inputs.policy.maximum_price_age:
        reasons.append("stale_proposed_price")
    pre = analyze_portfolio_risk(inputs, report_timestamp=inputs.as_of, scenarios=scenarios)
    price = exact(proposal.proposed_price, "proposed price")
    quantity = (
        exact(proposal.quantity, "quantity")
        if proposal.quantity is not None
        else exact(proposal.notional, "notional") / price
    )
    fees = exact(proposal.estimated_fees, "fees")
    positions = {item.symbol: item for item in inputs.positions}
    existing = positions.get(proposal.symbol)
    existing_quantity = exact(existing.quantity, "quantity") if existing else Decimal(0)
    signed_quantity = quantity if proposal.side == "BUY" else -quantity
    resulting_quantity = existing_quantity + signed_quantity
    if resulting_quantity < 0:
        reasons.extend(("sell_exceeds_holdings", "short_sale_forbidden"))
    cash = exact(inputs.cash_value, "cash", nonnegative=False)
    cash_change = -(quantity * price + fees) if proposal.side == "BUY" else quantity * price - fees
    post_cash = cash + cash_change
    if post_cash < 0:
        reasons.extend(("insufficient_cash", "margin_forbidden"))
    if reasons:
        return ProposedTradeRiskComparison(
            proposal.proposal_identity,
            pre.report_identity,
            None,
            text(cash),
            None,
            (),
            (),
            (),
            (),
            (),
            (),
            False,
            False,
            tuple(sorted(set(reasons))),
        )
    if resulting_quantity == 0:
        positions.pop(proposal.symbol, None)
    else:
        positions[proposal.symbol] = RiskPosition(
            proposal.symbol,
            proposal.instrument_type,
            text(resulting_quantity),
            proposal.proposed_price,
            text(resulting_quantity * price),
            proposal.price_timestamp,
            False,
        )
    projected_input = replace(
        inputs,
        cash_value=text(post_cash),
        positions=tuple(positions.values()),
        as_of=proposal.simulation_timestamp,
        input_identity="",
        provenance=tuple(
            replace(
                item,
                source_timestamp=proposal.simulation_timestamp,
                acquired_at=proposal.simulation_timestamp,
            )
            if item.source_kind in {"broker_snapshot", "valuation", "risk_policy"}
            else item
            for item in inputs.provenance
        ),
    )
    post = analyze_portfolio_risk(
        projected_input, report_timestamp=proposal.simulation_timestamp, scenarios=scenarios
    )
    pre_map = {item.limit_id: item for item in pre.violations}
    post_map = {item.limit_id: item for item in post.violations}
    new = tuple(sorted(post_map.keys() - pre_map.keys()))
    resolved = tuple(sorted(pre_map.keys() - post_map.keys()))
    worsened = tuple(
        sorted(
            key
            for key in pre_map.keys() & post_map.keys()
            if abs(exact(post_map[key].observed_value, "observed"))
            > abs(exact(pre_map[key].observed_value, "observed"))
        )
    )
    improved = tuple(
        sorted(
            key
            for key in pre_map.keys() & post_map.keys()
            if abs(exact(post_map[key].observed_value, "observed"))
            < abs(exact(pre_map[key].observed_value, "observed"))
        )
    )
    blocking = tuple(item.limit_id for item in post.violations if item.severity == "blocking")
    if blocking:
        reasons.append("blocking_risk_limit")
    if post.overall_completeness is not CompletenessStatus.COMPLETE:
        reasons.append("calculation_unavailable")
    return ProposedTradeRiskComparison(
        proposal.proposal_identity,
        pre.report_identity,
        post.report_identity,
        text(cash),
        text(post_cash),
        tuple(positions.values()),
        post.violations,
        new,
        resolved,
        worsened,
        improved,
        not reasons,
        False,
        tuple(sorted(set(reasons))),
    )
