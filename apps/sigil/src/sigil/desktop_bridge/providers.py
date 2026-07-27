"""Closed, read-only provider snapshot for Sigil Desktop.

Credentials are loaded only inside this Python process. Returned data contains
provider health, masked account facts, and bounded market/account values only.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .universe import US_LISTED_SCREENING_UNIVERSE

MAX_CREDENTIAL_BYTES = 32_768
MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_SECONDS = 10.0
SYMBOLS = tuple(item["symbol"] for item in US_LISTED_SCREENING_UNIVERSE)
ALLOWED_KEYS = frozenset(
    {
        "SIGIL_ALPACA_API_KEY_ID",
        "SIGIL_ALPACA_API_SECRET_KEY",
        "SIGIL_PUBLIC_API_SECRET",
        "SIGIL_BROKER_SUBMISSION_ENABLED",
    }
)
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _credential_path() -> Path:
    configured = os.environ.get("SIGIL_PROVIDER_CREDENTIAL_FILE")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / "Desktop" / "Sigil-provider-credentials.txt"
    )


def load_credentials(path: Path | None = None) -> dict[str, str]:
    """Load an exact allowlist from a private regular file."""

    target = path or _credential_path()
    if not target.is_absolute() or target.is_symlink() or not target.is_file():
        raise RuntimeError("provider credential file is unavailable")
    stat = target.stat()
    if stat.st_size > MAX_CREDENTIAL_BYTES or stat.st_mode & 0o077:
        raise RuntimeError("provider credential file permissions are unsafe")
    result: dict[str, str] = {}
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name not in ALLOWED_KEYS:
            continue
        normalized = value.strip().strip("\"'")
        if not normalized or len(normalized) > 16_384:
            raise RuntimeError("provider credential value is invalid")
        result[name] = normalized
    return result


def _json_request(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: dict[str, object] | None = None,
    opener: Callable[[Request, float], object] | None = None,
) -> object:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = Request(url, headers=headers, data=data, method=method)
    open_request = opener or (lambda req, timeout: build_opener(_NoRedirect()).open(req, timeout=timeout))
    try:
        response = open_request(request, TIMEOUT_SECONDS)
        status = int(response.getcode())  # type: ignore[attr-defined]
        content_type = response.headers.get("Content-Type", "")  # type: ignore[attr-defined]
        raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
    except HTTPError as error:
        raise RuntimeError(f"provider returned HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("provider connection failed") from None
    if not 200 <= status < 300 or len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("provider response failed validation")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise RuntimeError("provider response is not JSON")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("provider returned malformed JSON") from None


def _alpaca(credentials: dict[str, str], opener: Callable[[Request, float], object] | None) -> dict[str, Any]:
    key = credentials.get("SIGIL_ALPACA_API_KEY_ID")
    secret = credentials.get("SIGIL_ALPACA_API_SECRET_KEY")
    if not key or not secret:
        return {"status": "not_configured", "message": "Local credentials are not configured.", "symbols": []}
    symbols: list[dict[str, str]] = []
    universe_by_symbol = {
        item["symbol"]: item for item in US_LISTED_SCREENING_UNIVERSE
    }
    try:
        payload = _json_request(
            "https://data.alpaca.markets/v2/stocks/snapshots"
            f"?symbols={','.join(SYMBOLS)}&feed=iex",
            method="GET",
            headers={
                "Accept": "application/json",
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            },
            opener=opener,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("Alpaca response shape is invalid")
        snapshots = payload.get("snapshots", payload)
        if not isinstance(snapshots, dict):
            raise RuntimeError("Alpaca response shape is invalid")
        for symbol in SYMBOLS:
            snapshot = snapshots.get(symbol)
            if not isinstance(snapshot, dict):
                continue
            trade = snapshot.get("latestTrade")
            daily = snapshot.get("dailyBar")
            previous = snapshot.get("prevDailyBar")
            if not isinstance(trade, dict):
                continue
            price = trade.get("p")
            observed_at = trade.get("t")
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                continue
            daily_close = daily.get("c") if isinstance(daily, dict) else None
            previous_close = (
                previous.get("c") if isinstance(previous, dict) else None
            )
            change = None
            if (
                isinstance(daily_close, (int, float))
                and not isinstance(daily_close, bool)
                and isinstance(previous_close, (int, float))
                and not isinstance(previous_close, bool)
                and previous_close != 0
            ):
                change = ((float(daily_close) / float(previous_close)) - 1) * 100
            symbols.append(
                {
                    "symbol": symbol,
                    "name": str(universe_by_symbol[symbol]["name"]),
                    "sector": str(universe_by_symbol[symbol]["sector"]),
                    "price": f"{float(price):.2f}",
                    "observed_at": str(observed_at or "unavailable"),
                    "daily_change_percent": (
                        f"{change:.2f}" if change is not None else "unavailable"
                    ),
                    "screen_status": (
                        "available" if change is not None else "quote-only"
                    ),
                    "source": "Alpaca IEX snapshot",
                }
            )
        symbols.sort(
            key=lambda item: (
                item["daily_change_percent"] == "unavailable",
                -float(item["daily_change_percent"])
                if item["daily_change_percent"] != "unavailable"
                else 0,
                item["symbol"],
            )
        )
        available = len(symbols)
        status = "connected" if available == len(SYMBOLS) else "degraded"
        return {
            "status": status,
            "message": (
                "Bounded U.S.-listed screen refreshed from read-only IEX snapshots."
                if available
                else "No current screening rows were available."
            ),
            "symbols": symbols,
            "universe": {
                "scope": "12 explicitly defined U.S.-listed demonstration equities",
                "total": len(SYMBOLS),
                "available": available,
                "unavailable": len(SYMBOLS) - available,
                "catalog_source": "Sigil bounded demonstration universe",
                "catalog_freshness": "Static local definition; provider catalog unverified",
                "iex_status": "real-time",
                "broader_us_status": "15-minute delayed historical data available; catalog unverified",
                "criteria": "Latest IEX quote availability; ranked by daily close change when available",
                "whole_market_coverage": False,
                "catalog_access": "unavailable_current_credentials",
                "coverage_limitation": (
                    "Current Alpaca credentials authorize IEX market data but "
                    "not the Alpaca trading asset catalog; full U.S. listing "
                    "enumeration is unavailable."
                ),
                "refresh_policy": (
                    "One batched read-only snapshot request every 30 seconds; "
                    "errors retain explicit unavailable coverage."
                ),
            },
        }
    except RuntimeError as error:
        return {
            "status": "degraded",
            "message": str(error),
            "symbols": symbols,
            "universe": {
                "scope": "12 explicitly defined U.S.-listed demonstration equities",
                "total": len(SYMBOLS),
                "available": len(symbols),
                "unavailable": len(SYMBOLS) - len(symbols),
                "catalog_source": "Sigil bounded demonstration universe",
                "catalog_freshness": "Static local definition; provider catalog unverified",
                "iex_status": "real-time when provider is available",
                "broader_us_status": "15-minute delayed historical data available; catalog unverified",
                "criteria": "Provider data unavailable; no values invented",
                "whole_market_coverage": False,
                "catalog_access": "unavailable_current_credentials",
                "coverage_limitation": (
                    "Current Alpaca credentials do not expose an enumerable "
                    "full U.S. asset catalog."
                ),
                "refresh_policy": (
                    "One batched read-only snapshot request every 30 seconds; "
                    "no automatic mutation or broker fallback."
                ),
            },
        }


def _account_id(account: object) -> str | None:
    if not isinstance(account, dict):
        return None
    for key in ("accountId", "account_id", "id"):
        value = account.get(key)
        if isinstance(value, str) and ACCOUNT_PATTERN.fullmatch(value):
            return value
    return None


def _masked(value: str) -> str:
    return f"•••• {value[-4:]}" if len(value) >= 4 else "••••"


def _public(credentials: dict[str, str], opener: Callable[[Request, float], object] | None) -> dict[str, Any]:
    secret = credentials.get("SIGIL_PUBLIC_API_SECRET")
    if not secret:
        return {"status": "not_configured", "message": "Local credentials are not configured.", "accounts": []}
    try:
        token_payload = _json_request(
            "https://api.public.com/userapiauthservice/personal/access-tokens",
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            body={"validityInMinutes": 15, "secret": secret},
            opener=opener,
        )
        token = token_payload.get("accessToken") if isinstance(token_payload, dict) else None
        if not isinstance(token, str) or not token:
            raise RuntimeError("Public authentication response is invalid")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
        accounts_payload = _json_request(
            "https://api.public.com/userapigateway/trading/account",
            method="GET",
            headers=headers,
            opener=opener,
        )
        raw_accounts = accounts_payload.get("accounts") if isinstance(accounts_payload, dict) else None
        if not isinstance(raw_accounts, list):
            raise RuntimeError("Public account response is invalid")
        accounts: list[dict[str, Any]] = []
        for raw_account in raw_accounts[:10]:
            account_id = _account_id(raw_account)
            if not account_id:
                continue
            portfolio = _json_request(
                f"https://api.public.com/userapigateway/trading/{quote(account_id, safe='')}/portfolio/v2",
                method="GET",
                headers=headers,
                opener=opener,
            )
            if not isinstance(portfolio, dict):
                raise RuntimeError("Public portfolio response is invalid")
            buying_power = portfolio.get("buyingPower")
            positions = portfolio.get("positions")
            raw_positions = positions if isinstance(positions, list) else []
            accounts.append(
                {
                    "masked_account_id": _masked(account_id),
                    "cash": str(
                        buying_power.get("cashOnlyBuyingPower", "unavailable")
                        if isinstance(buying_power, dict)
                        else "unavailable"
                    ),
                    "portfolio_value": str(
                        portfolio.get("equity")
                        or portfolio.get("totalValue")
                        or portfolio.get("portfolioValue")
                        or "unavailable"
                    ),
                    "positions": [
                        {
                            "symbol": str(item.get("instrument", {}).get("symbol", "—")),
                            "quantity": str(item.get("quantity", "—")),
                        }
                        for item in raw_positions[:50]
                        if isinstance(item, dict)
                    ],
                }
            )
        return {"status": "connected", "message": "Read-only account access is current.", "accounts": accounts}
    except RuntimeError as error:
        return {"status": "degraded", "message": str(error), "accounts": []}


def provider_snapshot(
    *, opener: Callable[[Request, float], object] | None = None, path: Path | None = None
) -> dict[str, Any]:
    checked_at = _timestamp()
    try:
        credentials = load_credentials(path)
    except RuntimeError as error:
        unavailable = {"status": "not_configured", "message": str(error)}
        return {
            "checked_at": checked_at,
            "broker_submission_available": False,
            "credentials_exposed": False,
            "alpaca": {**unavailable, "symbols": []},
            "public": {**unavailable, "accounts": []},
        }
    return {
        "checked_at": checked_at,
        "broker_submission_available": False,
        "credentials_exposed": False,
        "alpaca": _alpaca(credentials, opener),
        "public": _public(credentials, opener),
    }
