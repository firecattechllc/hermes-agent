"""Data-only Alpaca market-data controls and Mission Control projection."""

from __future__ import annotations

from typing import Any

from sigil.market_data import AlpacaMarketDataRouter
from sigil.market_data.alpaca import AlpacaProviderError

_router = AlpacaMarketDataRouter()


def alpaca_market_data_status() -> dict[str, Any]:
    return _router.projection()


def control_alpaca_market_data(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Alpaca market-data control requires a payload object")
    action = payload.get("action")
    if action == "refresh_assets":
        try:
            _router.refresh_assets()
        except AlpacaProviderError:
            pass
    elif action not in {
        "start_delayed_sip", "stop_delayed_sip", "connect_live_iex",
        "disconnect_live_iex", "refresh_status",
    }:
        raise ValueError("Unsupported data-only Alpaca control")
    projection = _router.projection()
    projection["control_action"] = action
    return projection
