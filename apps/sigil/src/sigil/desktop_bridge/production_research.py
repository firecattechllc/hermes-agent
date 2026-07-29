"""Structured bridge and runtime orchestration for v2.1 production research."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sigil.asset_catalog import AssetCatalogService
from sigil.autonomous_paper import CandidateResearch
from sigil.production_research import (
    AlpacaProductionDataClient,
    ProductionDataError,
    ProductionResearchService,
    ProductionResearchStore,
)
from sigil.production_research.models import decimal, parse_time

from .autonomous_paper import _service as _execution_service
from .runtime import _state_directory


def _service() -> ProductionResearchService:
    return ProductionResearchService(ProductionResearchStore(_state_directory()))


def production_research_status() -> dict[str, Any]:
    return _service().status()


def production_research_collection(kind: str, payload: object = None) -> dict[str, Any]:
    values = payload if isinstance(payload, dict) else {}
    return _service().recent(
        kind,
        offset=values.get("offset", 0),
        limit=values.get("limit", 50),
    )


def production_research_detail(kind: str, payload: object = None) -> dict[str, Any]:
    values = payload if isinstance(payload, dict) else {}
    identity = values.get("identity")
    if not isinstance(identity, str) or not identity:
        raise ValueError("detail identity is required")
    result = _service().detail(kind, identity)
    if result is None:
        raise ValueError("production research detail was not found")
    return result


def shadow_mode_enable() -> dict[str, Any]:
    return _service().set_shadow_mode(True)


def shadow_mode_disable() -> dict[str, Any]:
    return _service().set_shadow_mode(False)


def promotion_readiness() -> dict[str, Any]:
    return _service().promotion_readiness()


def request_paper_promotion() -> dict[str, Any]:
    return _service().request_promotion()


def reconcile_positions() -> dict[str, Any]:
    """Refresh paper orders and positions without mutating broker state."""
    return _execution_service().reconcile()


def emergency_paper_liquidation() -> dict[str, Any]:
    """Submit governed exits for Sigil-owned positions only."""
    execution = _execution_service()
    execution.reconcile()
    positions = execution.recent("positions", limit=100)["items"]
    managed = set(execution.status()["managed_position_symbols"])
    symbols = tuple(
        sorted(str(item["symbol"]) for item in positions if item.get("symbol") in managed)
    )
    if not symbols:
        return execution.status()
    now = datetime.now().astimezone()
    evidence = AlpacaProductionDataClient.from_environment().collect_batch(symbols, now=now)
    prices = {
        item.symbol: item.bid
        for item in evidence
        if item.status.value == "complete" and item.bid is not None
    }
    if set(prices) != set(symbols):
        raise ValueError(
            "emergency paper liquidation requires fresh validated prices for every managed position"
        )
    return execution.monitor_positions(prices, now=now, emergency=True)


def run_production_batch(
    symbols: list[str],
    *,
    cursor: int,
    batch_number: int,
    total_eligible: int,
    next_cycle_at: str | None,
    now: datetime,
) -> dict[str, Any]:
    catalog_state, snapshot, _metadata = AssetCatalogService(_state_directory()).store.load(now=now)
    if snapshot is None or catalog_state != "fresh":
        raise RuntimeError("production research requires a fresh governed catalog")
    selected = set(symbols)
    assets = [asset for asset in snapshot.normalized_assets if asset.symbol in selected]
    data_client = AlpacaProductionDataClient.from_environment()
    provider_failure = None
    try:
        evidence = data_client.collect_batch(tuple(symbols), now=now)
        provider_available = True
    except ProductionDataError as error:
        provider_failure = error.code
        evidence = tuple(
            _service().unavailable_evidence(symbol, now, error.code) for symbol in symbols
        )
        provider_available = False
    execution = _execution_service()
    execution_status = execution.status()
    market_state_known = False
    try:
        market_state_known = isinstance(execution.client.clock().get("is_open"), bool)
    except Exception:  # noqa: BLE001 - a failed clock read must fail closed
        market_state_known = False
    result = _service().process_batch(
        assets,
        evidence,
        cursor=cursor,
        batch_number=batch_number,
        total_eligible=total_eligible,
        now=now,
        next_cycle_at=next_cycle_at,
        catalog_fresh=True,
        portfolio_fresh=not execution_status["degraded_conditions"],
        market_state_known=market_state_known,
        paused=bool(execution_status["paused"]),
        # Shadow research remains active while the paper execution kill switch
        # blocks only promotion and order mutation.
        kill_switch=False,
        audit_available=True,
        reconciliation_complete=(
            "reconciliation_required" not in execution_status["degraded_conditions"]
        ),
        provider_status=provider_failure or "available",
        market_data_freshness=("unavailable" if provider_failure else "fresh"),
    )
    result["broker_submission_attempted"] = False
    # Forward monitoring is independent from whether the current batch produced
    # a candidate. Matching shadow positions advance using the same validated,
    # contemporaneous evidence and never contact the trading API.
    _service().monitor_shadow(evidence, now=now)
    if (
        not result["shadow_mode"]
        and result["paper_promotion_approved"]
        and execution_status["activated"]
        and execution_status["broker_submission"]
    ):
        candidate_records = _service().recent("candidates", limit=25)["items"]
        evidence_by_symbol = {item.symbol: item for item in evidence}
        asset_by_symbol = {item.symbol: item for item in assets}
        candidates = []
        for record in candidate_records:
            symbol = record["symbol"]
            item = evidence_by_symbol.get(symbol)
            asset_item = asset_by_symbol.get(symbol)
            if item is None or asset_item is None or item.bid is None or item.ask is None:
                continue
            quote_age = int((now - parse_time(item.observed_at, "quote timestamp")).total_seconds())
            bars_age = int(
                (now - parse_time(item.daily_bars[-1].timestamp, "bar timestamp")).total_seconds()
            )
            candidates.append(
                CandidateResearch(
                    symbol=symbol,
                    asset_class=asset_item.asset_class,
                    exchange=asset_item.exchange,
                    tradable=asset_item.tradable,
                    fractionable=asset_item.fractionable,
                    status=asset_item.status,
                    name=asset_item.name,
                    quote_bid=item.bid,
                    quote_ask=item.ask,
                    quote_age_seconds=max(0, quote_age),
                    bars_age_seconds=max(0, bars_age),
                    average_dollar_volume=decimal(
                        record["average_dollar_volume"],
                        "average dollar volume",
                    ),
                    strategy_score=decimal(record["normalized_score"], "strategy score"),
                    confidence=decimal(record["confidence"], "confidence"),
                    expected_setup_positive=True,
                    evidence_complete=True,
                )
            )
        if candidates:
            execution_result = execution.evaluate_batch(
                candidates,
                cursor=cursor,
                batch_number=batch_number,
                total_eligible=total_eligible,
                catalog_fresh=True,
                portfolio_fresh=not execution_status["degraded_conditions"],
                runtime_healthy=True,
                audit_available=True,
                next_cycle_at=next_cycle_at,
                submit=True,
            )
            result["broker_submission_attempted"] = (
                execution_result["last_order_intent"] is not None
            )
    if execution_status["activated"] and execution_status["broker_submission"]:
        position_symbols = tuple(
            sorted(
                str(item["symbol"])
                for item in execution.recent("positions", limit=100)["items"]
                if item.get("symbol")
            )
        )
        if position_symbols:
            try:
                position_evidence = data_client.collect_batch(position_symbols, now=now)
            except ProductionDataError:
                result["degraded_conditions"] = sorted(
                    {
                        *result["degraded_conditions"],
                        "position_market_data_unavailable",
                    }
                )
            else:
                position_prices = {
                    item.symbol: item.bid
                    for item in position_evidence
                    if item.status.value == "complete" and item.bid is not None
                }
                execution.monitor_positions(position_prices, now=now)
    if not provider_available:
        result["degraded_conditions"] = sorted(
            {
                *result["degraded_conditions"],
                f"market_data_{provider_failure}",
            }
        )
    return result
