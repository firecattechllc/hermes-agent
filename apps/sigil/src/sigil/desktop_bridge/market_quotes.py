"""Bounded read-only quote projection for visible market-universe results."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sigil.market_data.alpaca.client import (
    AlpacaConfig,
    AlpacaHttpClient,
    AlpacaProviderError,
)

from .providers import load_credentials

MAX_VISIBLE_QUOTES = 20
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.\\-]{1,16}$")


def _timestamp_age(value: object, now: datetime) -> int | None:
    if not isinstance(value, str) or not value:
        return None

    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)

    return max(0, int((now - observed.astimezone(UTC)).total_seconds()))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    return float(value)


def _unavailable(symbols: tuple[str, ...], reason: str) -> dict[str, Any]:
    return {
        "feed": "unavailable",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "quotes": [
            {
                "symbol": symbol,
                "price": None,
                "change": None,
                "change_percent": None,
                "observed_at": None,
                "age_seconds": None,
                "freshness": "unavailable",
                "source": "Price unavailable",
                "reason": reason,
            }
            for symbol in symbols
        ],
        "broker_submission_available": False,
        "execution_authorized": False,
        "data_only": True,
    }


def market_universe_quotes(
    payload: object,
    *,
    client: AlpacaHttpClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch read-only IEX snapshots for only the visible search results."""

    if not isinstance(payload, dict):
        raise ValueError("market universe quotes require a payload object")

    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list):
        raise ValueError("symbols must be a list")

    symbols: list[str] = []

    for raw_symbol in raw_symbols:
        if not isinstance(raw_symbol, str):
            raise ValueError("every symbol must be a string")

        symbol = raw_symbol.strip().upper()
        if not SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("symbol is invalid")

        if symbol not in symbols:
            symbols.append(symbol)

    if not 1 <= len(symbols) <= MAX_VISIBLE_QUOTES:
        raise ValueError("between 1 and 20 unique symbols are required")

    bounded_symbols = tuple(symbols)
    observed_now = now or datetime.now(UTC)

    try:
        if client is None:
            credentials = load_credentials()
            config = AlpacaConfig(
                key_id=credentials.get("SIGIL_ALPACA_API_KEY_ID"),
                secret_key=credentials.get("SIGIL_ALPACA_API_SECRET_KEY"),
            )
            provider = AlpacaHttpClient(config)
        else:
            provider = client

        payload_data = provider.stock_snapshots(bounded_symbols, feed="iex")
    except AlpacaProviderError as error:
        return _unavailable(bounded_symbols, error.code)

    if not isinstance(payload_data, dict):
        return _unavailable(bounded_symbols, "malformed_snapshot_response")

    rows: list[dict[str, Any]] = []

    for symbol in bounded_symbols:
        snapshot = payload_data.get(symbol)
        if not isinstance(snapshot, dict):
            rows.append(_unavailable((symbol,), "snapshot_missing")["quotes"][0])
            continue

        latest_trade = snapshot.get("latestTrade")
        daily_bar = snapshot.get("dailyBar")
        previous_bar = snapshot.get("prevDailyBar")

        latest_trade = latest_trade if isinstance(latest_trade, dict) else {}
        daily_bar = daily_bar if isinstance(daily_bar, dict) else {}
        previous_bar = previous_bar if isinstance(previous_bar, dict) else {}

        price = _number(latest_trade.get("p"))
        if price is None:
            price = _number(daily_bar.get("c"))

        previous_close = _number(previous_bar.get("c"))
        change = (
            price - previous_close
            if price is not None and previous_close not in {None, 0}
            else None
        )
        change_percent = (
            change / previous_close * 100
            if change is not None and previous_close not in {None, 0}
            else None
        )

        timestamp = latest_trade.get("t") or daily_bar.get("t")
        age_seconds = _timestamp_age(timestamp, observed_now)

        if price is None:
            freshness = "unavailable"
            source = "Price unavailable"
        elif age_seconds is None:
            freshness = "unknown"
            source = "IEX timestamp unavailable"
        elif age_seconds <= 60:
            freshness = "live"
            source = "Live IEX"
        elif age_seconds <= 900:
            freshness = "delayed"
            source = "IEX delayed"
        else:
            freshness = "stale"
            source = "Stale IEX"

        rows.append(
            {
                "symbol": symbol,
                "price": price,
                "change": change,
                "change_percent": change_percent,
                "previous_close": previous_close,
                "observed_at": timestamp if isinstance(timestamp, str) else None,
                "age_seconds": age_seconds,
                "freshness": freshness,
                "source": source,
                "reason": None,
            }
        )

    return {
        "feed": "iex",
        "generated_at": observed_now.isoformat().replace("+00:00", "Z"),
        "quotes": rows,
        "broker_submission_available": False,
        "execution_authorized": False,
        "data_only": True,
    }
