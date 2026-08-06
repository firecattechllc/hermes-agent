from __future__ import annotations

import socket
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from sigil.integrations.providers import (
    BoundedMemoryCache,
    BoundedRateLimiter,
    FinancialDataAuthenticationError,
    FinancialDataRateLimitError,
    FinancialDataRequest,
    FinancialDataTransportError,
    FinancialDataValidationError,
    FredProvider,
    GovernedHTTPSTransport,
    MappingCredentialResolver,
    fred_request,
    normalize_series_id,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
VALID_KEY = "a" * 32  # FRED keys are 32-char lowercase alphanumeric


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in these tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeResponse:
    def __init__(
        self,
        payload: bytes = b'{"observations":[{"date":"2026-07-01","value":"3.2"}]}',
        *,
        status: int = 200,
        content_type: str = "application/json",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self._status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}

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


def transport(opener: RecordingOpener, **kwargs: object) -> GovernedHTTPSTransport:
    return GovernedHTTPSTransport(
        opener=opener, wall_clock=lambda: NOW, sleeper=lambda _: None, **kwargs
    )


def provider(
    opener: RecordingOpener | None = None,
    *,
    key: str = VALID_KEY,
    cache: BoundedMemoryCache | None = None,
    limiter: BoundedRateLimiter | None = None,
) -> FredProvider:
    return FredProvider(
        identity_resolver=MappingCredentialResolver({"fred": key}),
        transport=transport(opener or RecordingOpener()),
        cache=cache,
        rate_limiter=limiter,
    )


def request(series_id: str = "CPIAUCSL") -> FinancialDataRequest:
    return fred_request(operation="series_observations", series_id=series_id, purpose="test")


def test_normalize_series_id_accepts_real_fred_mnemonics() -> None:
    assert normalize_series_id("cpiaucsl") == "CPIAUCSL"
    assert normalize_series_id("GDP") == "GDP"
    assert normalize_series_id("UNRATE") == "UNRATE"


def test_normalize_series_id_rejects_malformed_input() -> None:
    with pytest.raises(FinancialDataValidationError):
        normalize_series_id("123ABC")  # must start with a letter
    with pytest.raises(FinancialDataValidationError):
        normalize_series_id("a" * 65)  # too long


def test_fred_request_rejects_unsupported_operation() -> None:
    with pytest.raises(FinancialDataValidationError, match="unsupported"):
        fred_request(operation="delete_series", series_id="GDP", purpose="test")


def test_endpoint_for_series_observations_is_correct_and_allowlisted() -> None:
    url = provider().endpoint_for(request())
    parsed = urlsplit(url)

    assert parsed.scheme == "https"
    assert parsed.hostname == "api.stlouisfed.org"
    assert parse_qs(parsed.query)["series_id"] == ["CPIAUCSL"]


def test_metadata_declares_credential_required_and_allowlisted_host() -> None:
    meta = FredProvider(
        identity_resolver=MappingCredentialResolver({"fred": VALID_KEY})
    ).metadata

    assert meta.provider_id == "fred"
    assert meta.credential_required is True
    assert meta.allowed_hosts == ("api.stlouisfed.org",)


def test_health_reports_unconfigured_without_api_key() -> None:
    health = FredProvider(identity_resolver=MappingCredentialResolver({})).health()

    assert health.configured is False
    assert health.credential_required is True
    assert health.credential_available is False


def test_health_reports_configured_with_valid_key() -> None:
    health = provider().health()

    assert health.configured is True
    assert health.credential_available is True


def test_acquire_sends_api_key_as_query_parameter_not_header() -> None:
    opener = RecordingOpener()
    result = provider(opener).acquire(request())

    sent_url = urlsplit(opener.requests[0].full_url)
    query = parse_qs(sent_url.query)
    assert query["api_key"] == [VALID_KEY]
    assert result.normalized_payload == {"observations": ({"date": "2026-07-01", "value": "3.2"},)}


def test_invalid_api_key_format_is_rejected_before_any_network_call() -> None:
    opener = RecordingOpener()
    with pytest.raises(FinancialDataValidationError, match="32-character"):
        provider(opener, key="not-a-valid-key").acquire(request())
    assert opener.requests == []


def test_authentication_failure_surfaces_as_auth_error() -> None:
    opener = RecordingOpener([HTTPError("url", 401, "unauthorized", {}, None)])
    with pytest.raises(FinancialDataAuthenticationError):
        provider(opener).acquire(request())


def test_rate_limit_response_surfaces_with_retry_after() -> None:
    opener = RecordingOpener(
        [HTTPError("url", 429, "too many requests", {"Retry-After": "30"}, None)]
    )
    with pytest.raises(FinancialDataRateLimitError):
        provider(opener).acquire(request())


def test_response_exceeding_max_bytes_fails_closed() -> None:
    huge = b'{"observations":' + b"[1]" * 1_000_000 + b"}"
    opener = RecordingOpener([FakeResponse(payload=huge)])
    with pytest.raises(FinancialDataTransportError):
        provider(opener).acquire(
            fred_request(
                operation="series_observations",
                series_id="GDP",
                purpose="test",
                max_response_bytes=100,
            )
        )


def test_rate_limiter_bounds_consecutive_requests() -> None:
    opener = RecordingOpener([FakeResponse(), FakeResponse(), FakeResponse()])
    limiter = BoundedRateLimiter(2, 1000.0, monotonic=lambda: 0.0, wait_policy="reject")
    fast_provider = provider(opener, limiter=limiter)

    fast_provider.acquire(request("GDP"))
    fast_provider.acquire(request("UNRATE"))
    with pytest.raises(Exception):
        fast_provider.acquire(request("CPIAUCSL"))


def test_cache_returns_stored_response_without_a_second_network_call() -> None:
    opener = RecordingOpener([FakeResponse()])
    cache = BoundedMemoryCache(max_entries=10, max_total_bytes=1_000_000, monotonic=lambda: 0.0)
    cached_provider = provider(opener, cache=cache)

    req = fred_request(
        operation="series_observations", series_id="GDP", purpose="test", cache_ttl_seconds=60.0
    )
    first = cached_provider.acquire(req)
    second = cached_provider.acquire(req)

    assert first.normalized_payload == second.normalized_payload
    assert len(opener.requests) == 1  # the second acquire() must be served from cache


def test_wrong_provider_id_on_request_is_rejected() -> None:
    bad_request = FinancialDataRequest(
        provider_id="sec_edgar", operation="series_observations", resource_id="GDP", purpose="test"
    )
    with pytest.raises(FinancialDataValidationError, match="provider_id"):
        provider().acquire(bad_request)


def test_non_object_response_root_is_rejected() -> None:
    opener = RecordingOpener([FakeResponse(payload=b"[1, 2, 3]")])
    with pytest.raises(FinancialDataValidationError, match="object"):
        provider(opener).acquire(request())
