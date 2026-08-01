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

MAX_CREDENTIAL_BYTES = 32_768
MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_SECONDS = 10.0
ALLOWED_KEYS = frozenset(
    {
        "SIGIL_ALPACA_API_KEY_ID",
        "SIGIL_ALPACA_API_SECRET_KEY",
        "SIGIL_PUBLIC_API_SECRET",
        "SIGIL_BROKER_SUBMISSION_ENABLED",
    }
)
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
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


def alpaca_credentials(path: Path | None = None) -> tuple[str | None, str | None]:
    """Resolve Alpaca credentials without returning them across the bridge."""

    canonical_pairs = (
        (os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")),
        (
            os.environ.get("SIGIL_ALPACA_API_KEY_ID"),
            os.environ.get("SIGIL_ALPACA_API_SECRET_KEY"),
        ),
    )
    for key, secret in canonical_pairs:
        if key and secret:
            return key, secret
    try:
        credentials = load_credentials() if path is None else load_credentials(path)
    except RuntimeError:
        credentials = {}
    file_key = credentials.get("SIGIL_ALPACA_API_KEY_ID")
    file_secret = credentials.get("SIGIL_ALPACA_API_SECRET_KEY")
    if file_key and file_secret:
        return file_key, file_secret
    return os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")


def _alpaca_probe(
    url: str,
    headers: dict[str, str],
    *,
    opener: Callable[[Request, float], object] | None,
) -> tuple[dict[str, object], object | None]:
    """Perform one GET probe and retain only sanitized health metadata."""

    request = Request(url, headers={**headers, "Accept": "application/json"}, method="GET")
    open_request = opener or (
        lambda req, timeout: build_opener(_NoRedirect()).open(req, timeout=timeout)
    )
    try:
        response = open_request(request, TIMEOUT_SECONDS)
        status = int(response.getcode())  # type: ignore[attr-defined]
        raw = response.read(MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        if len(raw) > MAX_RESPONSE_BYTES:
            return {"successful": False, "http_status": status, "error_category": "response_too_large"}, None
        payload = json.loads(raw) if raw else {}
    except HTTPError as error:
        if error.code in {401, 403}:
            category = "authentication_failed"
        elif error.code == 429:
            category = "rate_limited"
        elif error.code >= 500:
            category = "provider_error"
        else:
            category = "provider_request_rejected"
        return {"successful": False, "http_status": int(error.code), "error_category": category}, None
    except (URLError, TimeoutError, OSError):
        return {"successful": False, "http_status": None, "error_category": "provider_unavailable"}, None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"successful": False, "http_status": None, "error_category": "malformed_response"}, None
    if not 200 <= status < 300:
        return {"successful": False, "http_status": status, "error_category": "provider_request_rejected"}, None
    return {"successful": True, "http_status": status, "error_category": None}, payload


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
        return {
            "status": "not_configured",
            "message": "Local credentials are not configured.",
            "symbols": [],
            "health": {
                "credentials_configured": False,
                "account": {"successful": False, "http_status": None, "error_category": "not_configured"},
                "latest_quote": {"successful": False, "http_status": None, "error_category": "not_configured"},
                "historical_bars": {"successful": False, "http_status": None, "error_category": "not_configured"},
                "feed": "iex",
            },
        }
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    account, _account_payload = _alpaca_probe(
        f"{ALPACA_PAPER_BASE_URL}/v2/account", headers, opener=opener
    )
    quote, _quote_payload = _alpaca_probe(
        f"{ALPACA_DATA_BASE_URL}/v2/stocks/AAPL/quotes/latest?feed=iex",
        headers,
        opener=opener,
    )
    history, _history_payload = _alpaca_probe(
        f"{ALPACA_DATA_BASE_URL}/v2/stocks/AAPL/bars?timeframe=1Day&limit=60&feed=iex&adjustment=all",
        headers,
        opener=opener,
    )
    health = {
        "credentials_configured": True,
        "account": account,
        "latest_quote": quote,
        "historical_bars": history,
        "feed": "iex",
    }
    market_data_ready = bool(quote["successful"] and history["successful"])
    account_ready = bool(account["successful"])
    try:
        from sigil.asset_catalog import AssetCatalogService

        from .runtime import _state_directory

        catalog = AssetCatalogService(_state_directory()).status()
        statistics = catalog["statistics"]
        cache_state = catalog["cache_state"]
        total = statistics["total_assets_discovered"]
        return {
            "status": (
                "connected"
                if cache_state == "fresh" and market_data_ready and account_ready
                else "degraded"
            ),
            "message": (
                "Account authentication and read-only market data are ready."
                if market_data_ready and account_ready
                else "Account and market-data health are reported separately; one or more probes failed."
            ),
            "symbols": [],
            "health": health,
            "universe": {
                "scope": "Full Alpaca asset catalog discovered" if total else "Catalog unavailable",
                "total": total,
                "available": statistics["proposal_eligible_assets"],
                "unavailable": statistics["excluded_assets"],
                "catalog_source": "Alpaca Paper Trading Assets API",
                "catalog_freshness": cache_state,
                "iex_status": "live partial-market IEX",
                "broader_us_status": "15-minute delayed SIP",
                "criteria": "Governed proposal eligibility with explicit exclusions",
                "whole_market_coverage": False,
                "catalog_access": cache_state,
                "coverage_limitation": (
                    "Full catalog discovery does not imply full market-data coverage; "
                    "IEX is partial-market and OTC data is unavailable."
                ),
                "refresh_policy": (
                    "Governed read-only refresh with a 24-hour freshness policy."
                ),
            },
        }
    except RuntimeError as error:
        return {
            "status": "degraded",
            "message": str(error),
            "symbols": [],
            "health": health,
            "universe": {
                "scope": "Catalog unavailable",
                "total": 0,
                "available": 0,
                "unavailable": 0,
                "catalog_source": "Alpaca Paper Trading Assets API",
                "catalog_freshness": "unavailable",
                "iex_status": "live partial-market IEX",
                "broader_us_status": "15-minute delayed SIP",
                "criteria": "Catalog-dependent research suspended safely",
                "whole_market_coverage": False,
                "catalog_access": "unavailable",
                "coverage_limitation": (
                    "No validated catalog or usable cache is available."
                ),
                "refresh_policy": (
                    "No demonstration fallback; refresh is explicit and read-only."
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
