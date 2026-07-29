"""Read-only production projection of the governed Alpaca paper catalog."""

from __future__ import annotations

from typing import Any

from sigil.asset_catalog import AssetCatalogService
from sigil.market_universe import UniverseValidationError

from .runtime import _state_directory

MAX_QUERY_LIMIT = 100


def _service() -> AssetCatalogService:
    return AssetCatalogService(_state_directory())


def market_universe_status() -> dict[str, object]:
    status = _service().status()
    statistics = status["statistics"]
    freshness = status["freshness"]
    cache_state = status["cache_state"]
    return {
        "schema_version": 1,
        "policy_version": "sigil-alpaca-paper-catalog-v1",
        "snapshot_id": status["revision"],
        "generated_at": freshness.get("validated_at"),
        "source_record_count": statistics["total_assets_discovered"],
        "active_count": statistics["active_assets"],
        "master_count": statistics["active_assets"],
        "broker_tradable_count": statistics["tradable_assets"],
        "actively_researched_count": 0,
        "proposal_eligible_count": statistics["proposal_eligible_assets"],
        "fractionable_count": statistics["fractionable_assets"],
        "conflicted_count": 0,
        "excluded_count": statistics["excluded_assets"],
        "target_minimum": 0,
        "target_maximum": 0,
        "target_capacity_validated": cache_state in {"fresh", "stale_usable"},
        "catalog_source": status["source"],
        "catalog_scope": (
            "Full Alpaca asset catalog discovered"
            if statistics["total_assets_discovered"]
            else "Catalog unavailable"
        ),
        "capacity_certification": "actual provider counts; no expected total",
        "coverage_limitation": (
            "Market-data coverage is partial under IEX; OTC and unsupported "
            "exchanges remain discovered but are excluded from proposal eligibility."
        ),
        "cache_state": cache_state,
        "cache_age_seconds": freshness.get("age_seconds"),
        "integrity": status["integrity"],
        "exchange_counts": statistics["exchange_counts"],
        "exclusion_reason_counts": statistics["exclusion_reason_counts"],
        "broker_submission_available": False,
        "execution_authorized": False,
        "environment": "paper",
    }


def market_universe_search(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UniverseValidationError(
            "market universe query requires a payload object"
        )
    allowed = {
        "query",
        "universe",
        "asset_class",
        "lifecycle_status",
        "monitoring_tier",
        "limit",
        "offset",
    }
    if set(payload) - allowed:
        raise UniverseValidationError(
            "market universe query contains unsupported filters"
        )
    universe = str(payload.get("universe", "master"))
    if universe not in {
        "master",
        "broker_tradable",
        "actively_researched",
        "proposal_eligible",
        "excluded",
    }:
        raise UniverseValidationError("universe filter is invalid")
    limit = payload.get("limit", 50)
    offset = payload.get("offset", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise UniverseValidationError("limit must be between 1 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise UniverseValidationError("offset must be non-negative")
    state, snapshot, _metadata = _service().store.load()
    if snapshot is None:
        return {
            "query": str(payload.get("query", "")).strip(),
            "universe": universe,
            "total": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
            "results": [],
            "status": state,
            "broker_submission_available": False,
            "execution_authorized": False,
        }
    needle = str(payload.get("query", "")).strip().casefold()
    rows = []
    for asset in snapshot.normalized_assets:
        membership = {
            "master": True,
            "broker_tradable": asset.tradable,
            "actively_researched": False,
            "proposal_eligible": asset.proposal_eligible,
            "excluded": not asset.proposal_eligible,
        }
        if not membership[universe]:
            continue
        if needle and needle not in f"{asset.symbol} {asset.name} {asset.exchange}".casefold():
            continue
        rows.append(
            {
                "instrument_id": asset.asset_id,
                "symbol": asset.symbol,
                "name": asset.name,
                "exchange": asset.exchange,
                "asset_class": asset.asset_class,
                "lifecycle_status": asset.status,
                "reconciliation_status": (
                    "validated" if asset.proposal_eligible else "excluded"
                ),
                "monitoring_tier": (
                    "proposal_eligible"
                    if asset.proposal_eligible
                    else "excluded"
                ),
                "aliases": [asset.symbol],
                "sector": None,
                "broker_tradable": asset.tradable,
                "actively_researched": False,
                "proposal_eligible": asset.proposal_eligible,
                "exclusion_reasons": list(asset.exclusion_reasons),
            }
        )
    return {
        "query": str(payload.get("query", "")).strip(),
        "universe": universe,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(rows),
        "results": rows[offset : offset + limit],
        "status": state,
        "broker_submission_available": False,
        "execution_authorized": False,
    }
