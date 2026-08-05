from __future__ import annotations

from datetime import datetime, timezone
import socket
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from sigil.integrations.providers import (
    ALPACA_ALLOWED_HOSTS,
    ALPACA_PROVIDER_ID,
    ALPACA_SUPPORTED_OPERATIONS,
    AlpacaMarketDataProvider,
    BoundedMemoryCache,
    BoundedRateLimiter,
    FinancialDataAuthenticationError,
    FinancialDataProviderRegistry,
    FinancialDataRateLimitError,
    FinancialDataRequest,
    FinancialDataValidationError,
    GovernedHTTPSTransport,
    MappingCredentialResolver,
    alpaca_request,
    normalize_alpaca_symbol,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
KEY_ID = "unit-test-key-id-placeholder"
SECRET_KEY = "unit-test-secret-placeholder"


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in Step 9A tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeResponse:
    def __init__(
        self,
        payload: bytes = b'{"bar":{"c":101.25,"t":"2026-07-22T20:00:00Z"}}',
        *,
        status: int = 200,
        content_type: str = "application/json",
    ) -> None:
        self._payload = payload
        self._status = status
        self.headers = {"Content-Type": content_type}

    def getcode(self) -> int:
        return self._status

    def read(self, size: int) -> bytes:
        return self._payload[:size]


class RecordingOpener:
    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = outcomes or [FakeResponse()]
        self.requests: list[object] = []

    def __call__(self, request: object, timeout: float) -> object:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_provider(
    opener: RecordingOpener | None = None,
    *,
    key_id: str | None = KEY_ID,
    secret_key: str | None = SECRET_KEY,
    cache: BoundedMemoryCache | None = None,
    limiter: BoundedRateLimiter | None = None,
) -> AlpacaMarketDataProvider:
    return AlpacaMarketDataProvider(
        key_id_resolver=MappingCredentialResolver(
            {ALPACA_PROVIDER_ID: key_id} if key_id is not None else {}
        ),
        secret_key_resolver=MappingCredentialResolver(
            {ALPACA_PROVIDER_ID: secret_key} if secret_key is not None else {}
        ),
        transport=GovernedHTTPSTransport(
            opener=opener or RecordingOpener(),
            wall_clock=lambda: NOW,
            sleeper=lambda _: None,
        ),
        cache=cache,
        rate_limiter=limiter,
    )


def latest_request(**kwargs: object) -> FinancialDataRequest:
    values: dict[str, object] = {
        "operation": "latest_bar",
        "symbol": "aapl",
        "purpose": "offline unit test",
    }
    values.update(kwargs)
    return alpaca_request(**values)  # type: ignore[arg-type]


def test_metadata_is_exact_and_exposes_only_read_operations() -> None:
    metadata = make_provider().metadata
    assert metadata.provider_id == ALPACA_PROVIDER_ID
    assert metadata.allowed_hosts == ALPACA_ALLOWED_HOSTS == ("data.alpaca.markets",)
    assert metadata.supported_operations == ALPACA_SUPPORTED_OPERATIONS
    assert metadata.supported_operations == tuple(sorted(metadata.supported_operations))
    forbidden = ("order", "trade_order", "account", "position", "brokerage")
    assert not any(item in metadata.supported_operations for item in forbidden)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("aapl", "AAPL"), ("brk.b", "BRK.B"), ("bf-b", "BF-B"), ("abc1", "ABC1")],
)
def test_symbol_normalization(value: str, expected: str) -> None:
    assert normalize_alpaca_symbol(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", " AAPL", "AAPL ", "AA/PL", "AAPL?x=1", "AAPL#x", "A.B-C", ".A", "A" * 16, "Ä"],
)
def test_symbol_rejections(value: str) -> None:
    with pytest.raises(FinancialDataValidationError):
        normalize_alpaca_symbol(value)


def test_request_identity_is_deterministic_and_contains_no_credentials() -> None:
    first = latest_request(symbol="aapl")
    second = latest_request(symbol="AAPL")
    assert first == second
    assert first.request_id == second.request_id
    combined = repr(first) + first.request_id + first.canonical_bytes().decode()
    assert KEY_ID not in combined
    assert SECRET_KEY not in combined


@pytest.mark.parametrize(
    ("operation", "payload", "expected"),
    [
        ("latest_bar", b'{"bar":{}}', "https://data.alpaca.markets/v2/stocks/AAPL/bars/latest"),
        (
            "latest_quote",
            b'{"quote":{}}',
            "https://data.alpaca.markets/v2/stocks/AAPL/quotes/latest",
        ),
        (
            "latest_trade",
            b'{"trade":{}}',
            "https://data.alpaca.markets/v2/stocks/AAPL/trades/latest",
        ),
        (
            "historical_bars",
            b'{"bars":[]}',
            "https://data.alpaca.markets/v2/stocks/AAPL/bars?limit=25&timeframe=1Day",
        ),
    ],
)
def test_exact_endpoint_construction(operation: str, payload: bytes, expected: str) -> None:
    provider = make_provider(RecordingOpener([FakeResponse(payload)]))
    kwargs = {"timeframe": "1Day", "limit": 25} if operation == "historical_bars" else {}
    request = alpaca_request(
        operation=operation, symbol="AAPL", purpose="endpoint test", **kwargs
    )
    assert provider.endpoint_for(request) == expected


@pytest.mark.parametrize("name", ["timeframe", "start", "end", "limit", "adjustment"])
def test_latest_operations_reject_historical_parameters(name: str) -> None:
    values = {
        "timeframe": "1Day",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
        "limit": 1,
        "adjustment": "raw",
    }
    with pytest.raises(FinancialDataValidationError):
        latest_request(**{name: values[name]})


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"timeframe": "2Min", "limit": 1},
        {"timeframe": "1Day", "limit": 0},
        {"timeframe": "1Day", "limit": 10_001},
        {"timeframe": "1Day", "limit": True},
        {"timeframe": "1Day", "limit": 1, "start": "2026-01-01"},
        {
            "timeframe": "1Day",
            "limit": 1,
            "start": "2026-01-02T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
        },
    ],
)
def test_historical_bounds_and_timestamps_fail_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError):
        alpaca_request(
            operation="historical_bars", symbol="AAPL", purpose="test", **kwargs
        )


@pytest.mark.parametrize(
    "kwargs", [{"feed": "unknown"}, {"timeframe": "1Day", "limit": 1, "adjustment": "bad"}]
)
def test_unsupported_feed_and_adjustment_fail_closed(kwargs: dict[str, object]) -> None:
    operation = "historical_bars" if "timeframe" in kwargs else "latest_bar"
    with pytest.raises(FinancialDataValidationError):
        alpaca_request(operation=operation, symbol="AAPL", purpose="test", **kwargs)


@pytest.mark.parametrize(("key_id", "secret_key"), [(None, SECRET_KEY), (KEY_ID, None)])
def test_both_credentials_are_required_before_network(
    key_id: str | None, secret_key: str | None
) -> None:
    opener = RecordingOpener()
    with pytest.raises(FinancialDataAuthenticationError):
        make_provider(opener, key_id=key_id, secret_key=secret_key).acquire(latest_request())
    assert opener.requests == []


def test_credentials_are_injected_only_into_exact_headers_and_never_surfaced() -> None:
    opener = RecordingOpener()
    response = make_provider(opener).acquire(latest_request())
    outbound = opener.requests[0]
    assert outbound.get_header("Apca-api-key-id") == KEY_ID  # type: ignore[attr-defined]
    assert outbound.get_header("Apca-api-secret-key") == SECRET_KEY  # type: ignore[attr-defined]
    exposed = repr(response) + repr(response.provenance) + response.provenance.endpoint_identity
    assert KEY_ID not in exposed
    assert SECRET_KEY not in exposed
    assert response.provenance.query_parameter_names == ()


def test_noncanonical_requests_and_wrong_provider_fail_closed() -> None:
    provider = make_provider()
    with pytest.raises(FinancialDataValidationError):
        provider.endpoint_for(FinancialDataRequest("sec_edgar", "latest_bar", "AAPL", "test"))
    with pytest.raises(FinancialDataValidationError):
        provider.endpoint_for(
            FinancialDataRequest(
                ALPACA_PROVIDER_ID,
                "latest_bar",
                "AAPL",
                "test",
                query_parameters=(("evil", "value"),),
            )
        )


def test_transport_host_and_redirect_guards_apply_to_alpaca() -> None:
    request = latest_request()
    with pytest.raises(FinancialDataValidationError):
        make_provider()._transport.fetch_json(  # type: ignore[attr-defined]
            request=request,
            url="https://broker-api.alpaca.markets/v2/account",
            allowed_hosts=ALPACA_ALLOWED_HOSTS,
            headers={},
            provider_version="test",
            normalizer=lambda value: value,
        )
    redirect = HTTPError(
        "https://data.alpaca.markets/x",
        302,
        "redirect",
        {"Location": "https://attacker.invalid"},
        None,
    )
    with pytest.raises(Exception, match="HTTP 302"):
        make_provider(RecordingOpener([redirect])).acquire(latest_request())


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, FinancialDataAuthenticationError), (403, FinancialDataAuthenticationError)],
)
def test_authentication_errors_map_safely(status: int, error: type[Exception]) -> None:
    with pytest.raises(error) as caught:
        make_provider(RecordingOpener([FakeResponse(status=status)])).acquire(latest_request())
    assert KEY_ID not in str(caught.value)
    assert SECRET_KEY not in str(caught.value)


def test_rate_limit_maps_and_local_limiter_is_injected() -> None:
    with pytest.raises(FinancialDataRateLimitError):
        make_provider(RecordingOpener([FakeResponse(status=429)])).acquire(latest_request())
    limiter = BoundedRateLimiter(1, 60, monotonic=lambda: 0.0)
    provider = make_provider(
        RecordingOpener([FakeResponse(), FakeResponse()]), limiter=limiter
    )
    provider.acquire(latest_request())
    with pytest.raises(FinancialDataRateLimitError):
        provider.acquire(latest_request(operation="latest_quote"))


def test_cache_hit_is_deterministic_and_excludes_credentials() -> None:
    clock = [0.0]
    cache = BoundedMemoryCache(
        max_entries=2, max_total_bytes=10_000, monotonic=lambda: clock[0]
    )
    opener = RecordingOpener()
    provider = make_provider(opener, cache=cache)
    request = latest_request(cache_ttl_seconds=10)
    first = provider.acquire(request)
    second = provider.acquire(request)
    assert first.provenance.cache_status == "miss"
    assert second.provenance.cache_status == "hit"
    assert len(opener.requests) == 1
    assert KEY_ID not in request.request_id
    assert SECRET_KEY not in request.request_id


@pytest.mark.parametrize(
    ("key_id", "secret_key", "available"),
    [(None, None, False), (KEY_ID, None, False), (None, SECRET_KEY, False), (KEY_ID, SECRET_KEY, True)],
)
def test_health_is_local_and_requires_complete_credentials(
    key_id: str | None, secret_key: str | None, available: bool
) -> None:
    opener = RecordingOpener()
    health = make_provider(opener, key_id=key_id, secret_key=secret_key).health()
    assert opener.requests == []
    assert health.credential_required is True
    assert health.credential_available is available
    assert health.locally_available is available
    assert health.allowed_hosts == ALPACA_ALLOWED_HOSTS


def test_registry_resolves_alpaca_exactly() -> None:
    provider = make_provider()
    registry = FinancialDataProviderRegistry((provider,))
    assert registry.resolve(ALPACA_PROVIDER_ID) is provider
    with pytest.raises(Exception, match="unknown"):
        registry.resolve("ALPACA_MARKET_DATA")


@pytest.mark.parametrize(
    ("operation", "payload"),
    [
        ("latest_bar", b"[]"),
        ("latest_bar", b'{"quote":{}}'),
        ("latest_quote", b'{"quote":[]}'),
        ("latest_trade", b'{"trade":null}'),
        ("historical_bars", b'{"bars":{}}'),
        ("historical_bars", b'{"bars":[1]}'),
    ],
)
def test_response_shape_validation(operation: str, payload: bytes) -> None:
    kwargs = {"timeframe": "1Day", "limit": 1} if operation == "historical_bars" else {}
    request = alpaca_request(operation=operation, symbol="AAPL", purpose="test", **kwargs)
    with pytest.raises(FinancialDataValidationError):
        make_provider(RecordingOpener([FakeResponse(payload)])).acquire(request)


def test_historical_query_is_exact_and_payload_numbers_are_unchanged() -> None:
    opener = RecordingOpener([FakeResponse(b'{"bars":[{"c":101.25}],"next_page_token":null}')])
    request = alpaca_request(
        operation="historical_bars",
        symbol="AAPL",
        purpose="test",
        timeframe="5Min",
        start="2026-01-01T00:00:00Z",
        end="2026-01-02T00:00:00+00:00",
        limit=25,
        adjustment="split",
        feed="iex",
    )
    response = make_provider(opener).acquire(request)
    query = parse_qs(urlsplit(opener.requests[0].full_url).query)  # type: ignore[attr-defined]
    assert query == {
        "adjustment": ["split"],
        "end": ["2026-01-02T00:00:00+00:00"],
        "feed": ["iex"],
        "limit": ["25"],
        "start": ["2026-01-01T00:00:00Z"],
        "timeframe": ["5Min"],
    }
    assert response.normalized_payload["bars"][0]["c"] == 101.25  # type: ignore[index]
