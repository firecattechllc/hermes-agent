"""Strictly read-only governed adapter for Alpaca stock market data."""

from __future__ import annotations

from datetime import datetime
import json
import re
from urllib.parse import urlencode

from .models import (
    FinancialDataProviderHealth,
    FinancialDataProviderMetadata,
    FinancialDataRequest,
    FinancialDataResponse,
    FinancialDataValidationError,
)
from .transport import (
    BoundedMemoryCache,
    BoundedRateLimiter,
    CredentialPlacement,
    CredentialResolver,
    GovernedHTTPSTransport,
)


ALPACA_PROVIDER_ID = "alpaca_market_data"
ALPACA_ADAPTER_VERSION = "sigil-alpaca-market-data-v1"
ALPACA_API_KEY_ID_ENVIRONMENT_VARIABLE = "SIGIL_ALPACA_API_KEY_ID"
ALPACA_API_SECRET_KEY_ENVIRONMENT_VARIABLE = "SIGIL_ALPACA_API_SECRET_KEY"
ALPACA_ALLOWED_HOSTS = ("data.alpaca.markets",)
ALPACA_SUPPORTED_OPERATIONS = (
    "historical_bars",
    "latest_bar",
    "latest_quote",
    "latest_trade",
)
ALPACA_TIMEFRAMES = ("1Day", "1Hour", "1Min", "1Month", "1Week", "15Min", "5Min")
ALPACA_ADJUSTMENTS = ("all", "dividend", "raw", "split")
ALPACA_FEEDS = ("iex", "otc", "sip")
ALPACA_MAX_HISTORICAL_LIMIT = 10_000
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{0,8}(?:[.-][A-Z0-9]{1,5})?$", re.ASCII)
_HISTORICAL_QUERY_NAMES = frozenset({"adjustment", "end", "feed", "limit", "start", "timeframe"})
_LATEST_QUERY_NAMES = frozenset({"feed"})
_LATEST_PATHS = {
    "latest_bar": "bars/latest",
    "latest_quote": "quotes/latest",
    "latest_trade": "trades/latest",
}


def normalize_alpaca_symbol(value: str) -> str:
    """Return a bounded uppercase stock symbol or fail closed."""

    if not isinstance(value, str):
        raise FinancialDataValidationError("symbol must be text")
    normalized = value.upper()
    if _SYMBOL_RE.fullmatch(normalized) is None:
        raise FinancialDataValidationError(
            "symbol must be 1 to 15 ASCII letters/digits with at most one dot or hyphen"
        )
    return normalized


def _parse_timestamp(value: str, name: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise FinancialDataValidationError(f"{name} must be a bounded ISO-8601 timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise FinancialDataValidationError(
            f"{name} must be an ISO-8601 timestamp with an explicit timezone"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FinancialDataValidationError(
            f"{name} must be an ISO-8601 timestamp with an explicit timezone"
        )
    return parsed


def alpaca_request(
    *,
    operation: str,
    symbol: str,
    purpose: str,
    timeframe: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    adjustment: str | None = None,
    feed: str | None = None,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 2_000_000,
    cache_ttl_seconds: float = 0.0,
    correlation_id: str | None = None,
) -> FinancialDataRequest:
    """Build a governed Alpaca request without accepting endpoints or credentials."""

    if operation not in ALPACA_SUPPORTED_OPERATIONS:
        raise FinancialDataValidationError("unsupported Alpaca market-data operation")
    if feed is not None and feed not in ALPACA_FEEDS:
        raise FinancialDataValidationError("unsupported Alpaca market-data feed")
    if operation != "historical_bars" and any(
        item is not None for item in (timeframe, start, end, limit, adjustment)
    ):
        raise FinancialDataValidationError("latest operations reject historical parameters")
    query: list[tuple[str, str]] = []
    if feed is not None:
        query.append(("feed", feed))
    if operation == "historical_bars":
        if timeframe not in ALPACA_TIMEFRAMES:
            raise FinancialDataValidationError("historical bars require an allowlisted timeframe")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise FinancialDataValidationError("historical bars require an integer limit")
        if not 1 <= limit <= ALPACA_MAX_HISTORICAL_LIMIT:
            raise FinancialDataValidationError(
                f"historical limit must be between 1 and {ALPACA_MAX_HISTORICAL_LIMIT}"
            )
        if adjustment is not None and adjustment not in ALPACA_ADJUSTMENTS:
            raise FinancialDataValidationError("unsupported Alpaca adjustment")
        query.extend((("timeframe", timeframe), ("limit", str(limit))))
        start_value = _parse_timestamp(start, "start") if start is not None else None
        end_value = _parse_timestamp(end, "end") if end is not None else None
        if start_value is not None:
            query.append(("start", start))
        if end_value is not None:
            query.append(("end", end))
        if start_value is not None and end_value is not None and start_value >= end_value:
            raise FinancialDataValidationError("historical start must precede end")
        if adjustment is not None:
            query.append(("adjustment", adjustment))
    return FinancialDataRequest(
        provider_id=ALPACA_PROVIDER_ID,
        operation=operation,
        resource_id=normalize_alpaca_symbol(symbol),
        purpose=purpose,
        query_parameters=tuple(query),
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        cache_ttl_seconds=cache_ttl_seconds,
        correlation_id=correlation_id,
    )


class _AlpacaCredentialPlacement(CredentialPlacement):
    """Expand an ephemeral pair into Alpaca's two authentication headers."""

    def __init__(self) -> None:
        super().__init__(kind="header", name="APCA-API-KEY-ID")

    def apply(
        self, headers: dict[str, str], query: list[tuple[str, str]], credential: str
    ) -> None:
        try:
            key_id, secret_key = json.loads(credential)
        except (TypeError, ValueError):
            raise FinancialDataValidationError("Alpaca credential binding is invalid") from None
        if not all(isinstance(item, str) and item.strip() for item in (key_id, secret_key)):
            raise FinancialDataValidationError("Alpaca credential binding is invalid")
        headers["APCA-API-KEY-ID"] = key_id
        headers["APCA-API-SECRET-KEY"] = secret_key


class AlpacaMarketDataProvider:
    """Governed GET-only adapter for four Alpaca stock market-data operations."""

    metadata = FinancialDataProviderMetadata(
        provider_id=ALPACA_PROVIDER_ID,
        display_name="Alpaca Market Data",
        adapter_version=ALPACA_ADAPTER_VERSION,
        supported_operations=ALPACA_SUPPORTED_OPERATIONS,
        allowed_hosts=ALPACA_ALLOWED_HOSTS,
        credential_required=True,
    )

    def __init__(
        self,
        *,
        key_id_resolver: CredentialResolver,
        secret_key_resolver: CredentialResolver,
        transport: GovernedHTTPSTransport | None = None,
        cache: BoundedMemoryCache | None = None,
        rate_limiter: BoundedRateLimiter | None = None,
    ) -> None:
        self._key_id_resolver = key_id_resolver
        self._secret_key_resolver = secret_key_resolver
        self._transport = transport or GovernedHTTPSTransport()
        self._cache = cache
        self._limiter = rate_limiter or BoundedRateLimiter(allowance=100, window_seconds=60.0)

    def endpoint_for(self, request: FinancialDataRequest) -> str:
        self._validate_request(request)
        if request.operation == "historical_bars":
            path = f"/v2/stocks/{request.resource_id}/bars"
        else:
            path = f"/v2/stocks/{request.resource_id}/{_LATEST_PATHS[request.operation]}"
        query = urlencode(request.query_parameters)
        return f"https://data.alpaca.markets{path}" + (f"?{query}" if query else "")

    def acquire(self, request: FinancialDataRequest) -> FinancialDataResponse:
        self._validate_request(request)
        if self._cache is not None and request.cache_ttl_seconds > 0:
            try:
                cached = self._cache.get(request.request_id)
            except Exception:
                cached = None
            if cached is not None:
                return cached
        key_id = self._key_id_resolver.resolve(
            ALPACA_PROVIDER_ID, ALPACA_API_KEY_ID_ENVIRONMENT_VARIABLE
        )
        secret_key = self._secret_key_resolver.resolve(
            ALPACA_PROVIDER_ID, ALPACA_API_SECRET_KEY_ENVIRONMENT_VARIABLE
        )
        binding = json.dumps((key_id, secret_key), separators=(",", ":"))
        self._limiter.acquire()
        response = self._transport.fetch_json(
            request=request,
            url=self.endpoint_for(request),
            allowed_hosts=ALPACA_ALLOWED_HOSTS,
            headers={"Accept": "application/json"},
            provider_version=ALPACA_ADAPTER_VERSION,
            normalizer=lambda value: self._normalize_response(request.operation, value),
            credential=binding,
            credential_placement=_AlpacaCredentialPlacement(),
        )
        if self._cache is not None:
            try:
                self._cache.put(request.request_id, response, request.cache_ttl_seconds)
            except Exception:
                pass
        return response

    def health(self) -> FinancialDataProviderHealth:
        key_available = self._key_id_resolver.available(
            ALPACA_PROVIDER_ID, ALPACA_API_KEY_ID_ENVIRONMENT_VARIABLE
        )
        secret_available = self._secret_key_resolver.available(
            ALPACA_PROVIDER_ID, ALPACA_API_SECRET_KEY_ENVIRONMENT_VARIABLE
        )
        available = key_available and secret_available
        return FinancialDataProviderHealth(
            provider_id=ALPACA_PROVIDER_ID,
            configured=available,
            credential_required=True,
            credential_available=available,
            supported_operations=ALPACA_SUPPORTED_OPERATIONS,
            allowed_hosts=ALPACA_ALLOWED_HOSTS,
            cache_enabled=self._cache is not None,
            rate_limit_remaining=self._limiter.remaining(),
            locally_available=available,
        )

    @staticmethod
    def _validate_request(request: FinancialDataRequest) -> None:
        if not isinstance(request, FinancialDataRequest):
            raise FinancialDataValidationError("Alpaca acquisition requires FinancialDataRequest")
        if request.provider_id != ALPACA_PROVIDER_ID:
            raise FinancialDataValidationError("request provider_id does not match Alpaca adapter")
        if request.operation not in ALPACA_SUPPORTED_OPERATIONS:
            raise FinancialDataValidationError("unsupported Alpaca market-data operation")
        if normalize_alpaca_symbol(request.resource_id) != request.resource_id:
            raise FinancialDataValidationError("Alpaca request symbol must be normalized")
        parameters = dict(request.query_parameters)
        allowed = (
            _HISTORICAL_QUERY_NAMES
            if request.operation == "historical_bars"
            else _LATEST_QUERY_NAMES
        )
        if any(name not in allowed for name in parameters):
            raise FinancialDataValidationError("unallowlisted Alpaca query parameter")
        rebuilt = alpaca_request(
            operation=request.operation,
            symbol=request.resource_id,
            purpose=request.purpose,
            timeframe=parameters.get("timeframe"),
            start=parameters.get("start"),
            end=parameters.get("end"),
            limit=int(parameters["limit"]) if parameters.get("limit", "").isdigit() else None,
            adjustment=parameters.get("adjustment"),
            feed=parameters.get("feed"),
            timeout_seconds=request.timeout_seconds,
            max_response_bytes=request.max_response_bytes,
            cache_ttl_seconds=request.cache_ttl_seconds,
            correlation_id=request.correlation_id,
        )
        if rebuilt != request:
            raise FinancialDataValidationError("Alpaca request is not canonical")

    @staticmethod
    def _normalize_response(operation: str, value: object) -> object:
        if not isinstance(value, dict):
            raise FinancialDataValidationError("Alpaca response root must be an object")
        expected = {
            "latest_bar": "bar",
            "latest_quote": "quote",
            "latest_trade": "trade",
            "historical_bars": "bars",
        }[operation]
        payload = value.get(expected)
        if operation == "historical_bars":
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise FinancialDataValidationError("Alpaca historical bars payload is malformed")
        elif not isinstance(payload, dict):
            raise FinancialDataValidationError(f"Alpaca {expected} payload is malformed")
        return value
