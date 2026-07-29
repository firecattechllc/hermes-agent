"""Data-only Alpaca market-data controls and Mission Control projection."""

from __future__ import annotations

from typing import Any

from sigil.asset_catalog import AssetCatalogService
from sigil.market_data import AlpacaMarketDataRouter

from .runtime import _state_directory

_router = AlpacaMarketDataRouter()


def _catalog_projection(
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = catalog or AssetCatalogService(_state_directory()).status()
    statistics = catalog["statistics"]
    freshness = catalog["freshness"]
    cache_state = catalog["cache_state"]
    return {
        "refresh_state": catalog["status"],
        "source_count": statistics["total_assets_discovered"],
        "accepted_count": statistics["active_assets"],
        "excluded_count": statistics["excluded_assets"],
        "conflict_count": 0,
        "generated_at": freshness.get("validated_at"),
        "age_seconds": freshness.get("age_seconds"),
        "stale": cache_state != "fresh",
        "last_error": catalog["failure_code"],
    }


def alpaca_market_data_status() -> dict[str, Any]:
    projection = _router.projection()
    projection["asset_catalog"] = _catalog_projection()
    return projection


def control_alpaca_market_data(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Alpaca market-data control requires a payload object")
    action = payload.get("action")
    catalog = None
    if action == "refresh_assets":
        catalog = AssetCatalogService(_state_directory()).refresh()
    elif action not in {
        "start_delayed_sip", "stop_delayed_sip", "connect_live_iex",
        "disconnect_live_iex", "refresh_status",
    }:
        raise ValueError("Unsupported data-only Alpaca control")
    projection = _router.projection()
    projection["asset_catalog"] = _catalog_projection(catalog)
    projection["control_action"] = action
    return projection
