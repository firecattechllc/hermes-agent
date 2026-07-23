from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from sigil.integrations.providers import (
    BoundedMemoryCache,
    BoundedRateLimiter,
    CredentialPlacement,
    EnvironmentCredentialResolver,
    FinancialDataAuthenticationError,
    FinancialDataProviderError,
    FinancialDataProviderRegistry,
    FinancialDataRateLimitError,
    FinancialDataRequest,
    FinancialDataTransportError,
    FinancialDataValidationError,
    GovernedHTTPSTransport,
    MappingCredentialResolver,
    SECEdgarProvider,
    normalize_cik,
    redact_text,
    sec_request,
    thaw_json,
)


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
IDENTITY = "Example Sigil Test test@example.invalid"


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is forbidden in Step 9 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeResponse:
    def __init__(
        self,
        payload: bytes = b'{"cik":"0000320193","facts":[]}',
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
        opener=opener,
        wall_clock=lambda: NOW,
        sleeper=lambda _: None,
        **kwargs,  # type: ignore[arg-type]
    )


def provider(
    opener: RecordingOpener | None = None,
    *,
    identity: str = IDENTITY,
    cache: BoundedMemoryCache | None = None,
    limiter: BoundedRateLimiter | None = None,
) -> SECEdgarProvider:
    return SECEdgarProvider(
        identity_resolver=MappingCredentialResolver({"sec_edgar": identity}),
        transport=transport(opener or RecordingOpener()),
        cache=cache,
        rate_limiter=limiter,
    )


def request(**kwargs: object) -> FinancialDataRequest:
    values: dict[str, object] = {
        "operation": "company_facts",
        "cik": "320193",
        "purpose": "deterministic offline test",
    }
    values.update(kwargs)
    return sec_request(**values)  # type: ignore[arg-type]


def test_request_is_immutable_canonical_and_has_deterministic_identity() -> None:
    first = FinancialDataRequest(
        provider_id="example",
        operation="quote",
        resource_id="ABC",
        purpose="test",
        query_parameters=(("z", "2"), ("a", "1")),
    )
    second = FinancialDataRequest(
        provider_id="example",
        operation="quote",
        resource_id="ABC",
        purpose="test",
        query_parameters=(("a", "1"), ("z", "2")),
    )
    assert first.query_parameters == (("a", "1"), ("z", "2"))
    assert first.request_id == second.request_id
    assert first.canonical_bytes() == second.canonical_bytes()
    with pytest.raises(FrozenInstanceError):
        first.operation = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "Bad Provider"),
        ("operation", "Bad-Operation"),
        ("resource_id", "https://attacker.invalid/path"),
        ("timeout_seconds", 0),
        ("timeout_seconds", 31),
        ("max_response_bytes", 0),
        ("max_response_bytes", 10_000_001),
        ("query_parameters", (("api_key", "secret"),)),
    ],
)
def test_request_validation_fails_closed(field: str, value: object) -> None:
    values: dict[str, object] = {
        "provider_id": "example",
        "operation": "quote",
        "resource_id": "ABC",
        "purpose": "test",
    }
    values[field] = value
    with pytest.raises(FinancialDataValidationError):
        FinancialDataRequest(**values)  # type: ignore[arg-type]


def test_registry_rejects_duplicates_resolves_exactly_and_lists_deterministically() -> None:
    sec = provider()
    registry = FinancialDataProviderRegistry((sec,))
    assert registry.resolve("sec_edgar") is sec
    assert tuple(item.provider_id for item in registry.list_metadata()) == ("sec_edgar",)
    with pytest.raises(FinancialDataProviderError, match="duplicate"):
        registry.register(sec)
    with pytest.raises(FinancialDataProviderError, match="unknown"):
        registry.resolve("SEC_EDGAR")


def test_environment_resolver_reads_only_exact_allowlisted_variable() -> None:
    resolver = EnvironmentCredentialResolver(
        {"paid": "SIGIL_PAID_API_KEY"},
        environ={"SIGIL_PAID_API_KEY": "test-secret", "OTHER": "must-not-read"},
    )
    assert resolver.resolve("paid", "SIGIL_PAID_API_KEY") == "test-secret"
    with pytest.raises(FinancialDataAuthenticationError, match="allowlisted"):
        resolver.resolve("paid", "OTHER")
    with pytest.raises(FinancialDataAuthenticationError, match="allowlisted"):
        resolver.resolve("other", "SIGIL_PAID_API_KEY")


def test_missing_sec_identity_fails_before_network() -> None:
    opener = RecordingOpener()
    sec = SECEdgarProvider(
        identity_resolver=MappingCredentialResolver({}),
        transport=transport(opener),
    )
    with pytest.raises(FinancialDataAuthenticationError):
        sec.acquire(request())
    assert opener.requests == []


@pytest.mark.parametrize(
    ("placement", "expected"),
    [
        (CredentialPlacement(kind="header", name="Authorization", prefix="Bearer "), "header"),
        (CredentialPlacement(kind="query", name="apikey"), "query"),
    ],
)
def test_credentials_are_inserted_only_at_final_transport_boundary(
    placement: CredentialPlacement, expected: str
) -> None:
    secret = "fictional-test-secret"
    opener = RecordingOpener()
    req = FinancialDataRequest(
        provider_id="paid", operation="quote", resource_id="ABC", purpose="test"
    )
    response = transport(opener).fetch_json(
        request=req,
        url="https://api.example.invalid/v1/quote",
        allowed_hosts=("api.example.invalid",),
        headers={},
        provider_version="v1",
        normalizer=lambda value: value,
        credential=secret,
        credential_placement=placement,
    )
    outbound = opener.requests[0]
    if expected == "header":
        assert outbound.get_header("Authorization") == f"Bearer {secret}"  # type: ignore[attr-defined]
    else:
        assert parse_qs(urlsplit(outbound.full_url).query)["apikey"] == [secret]  # type: ignore[attr-defined]
    combined = repr(req) + repr(response) + response.request_id
    assert secret not in combined
    assert secret not in response.provenance.endpoint_identity
    assert secret not in response.provenance.query_parameter_names


def test_redaction_removes_configured_and_labelled_secrets() -> None:
    secret = "fictional-secret"
    redacted = redact_text(f"api_key={secret} authorization:Bearer-{secret}", (secret,))
    assert secret not in redacted
    assert "[REDACTED]" in redacted


@pytest.mark.parametrize(
    ("url", "hosts"),
    [
        ("http://api.example.invalid/x", ("api.example.invalid",)),
        ("https://attacker.invalid/x", ("api.example.invalid",)),
        ("https://user:pass@api.example.invalid/x", ("api.example.invalid",)),
    ],
)
def test_transport_rejects_non_https_or_unallowlisted_destinations(
    url: str, hosts: tuple[str, ...]
) -> None:
    with pytest.raises(FinancialDataValidationError):
        transport(RecordingOpener()).fetch_json(
            request=FinancialDataRequest("paid", "quote", "ABC", "test"),
            url=url,
            allowed_hosts=hosts,
            headers={},
            provider_version="v1",
            normalizer=lambda value: value,
        )


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(b"not json"), FinancialDataValidationError),
        (FakeResponse(b"{}", content_type="text/html"), FinancialDataValidationError),
        (FakeResponse(status=401), FinancialDataAuthenticationError),
        (FakeResponse(status=403), FinancialDataAuthenticationError),
        (FakeResponse(status=404), FinancialDataTransportError),
    ],
)
def test_transport_status_and_json_failures(
    response: FakeResponse, error: type[Exception]
) -> None:
    with pytest.raises(error):
        provider(RecordingOpener([response])).acquire(request())


def test_response_size_bound_is_enforced() -> None:
    with pytest.raises(FinancialDataTransportError, match="maximum"):
        provider(RecordingOpener([FakeResponse(b'{"long":"value"}')])).acquire(
            request(max_response_bytes=5)
        )


@pytest.mark.parametrize(
    "header",
    [{"Content-Length": "999"}, {"Content-Length": "not-a-number"}],
)
def test_partial_or_malformed_content_length_is_rejected(header: dict[str, str]) -> None:
    with pytest.raises((FinancialDataTransportError, FinancialDataValidationError)):
        provider(RecordingOpener([FakeResponse(headers=header)])).acquire(request())


def test_remote_rate_limit_parses_and_bounds_retry_after() -> None:
    opener = RecordingOpener(
        [HTTPError("https://data.sec.gov/x", 429, "secret", {"Retry-After": "999"}, None)]
    )
    with pytest.raises(FinancialDataRateLimitError) as caught:
        provider(opener).acquire(request())
    assert caught.value.retry_after_seconds == 30.0
    assert "secret" not in str(caught.value)


def test_transient_failure_retries_once_and_then_succeeds_without_sleep() -> None:
    opener = RecordingOpener([FakeResponse(status=503), FakeResponse()])
    response = provider(opener).acquire(request())
    assert len(opener.requests) == 2
    assert response.provenance.http_status == 200


def test_transport_failure_is_bounded_and_safe() -> None:
    opener = RecordingOpener([URLError("fictional-secret"), URLError("fictional-secret")])
    with pytest.raises(FinancialDataTransportError) as caught:
        provider(opener).acquire(request())
    assert len(opener.requests) == 2
    assert "fictional-secret" not in str(caught.value)


def test_success_is_normalized_hashed_safe_and_immutable() -> None:
    raw = b'{"facts":[],"cik":"0000320193"}'
    opener = RecordingOpener(
        [
            FakeResponse(
                raw,
                headers={
                    "ETag": '"safe"',
                    "Authorization": "must-not-capture",
                    "Set-Cookie": "must-not-capture",
                },
            )
        ]
    )
    response = provider(opener).acquire(request(correlation_id="run-1"))
    assert thaw_json(response.normalized_payload) == {
        "cik": "0000320193",
        "facts": [],
    }
    assert response.raw_content_sha256 == response.provenance.content_sha256
    assert response.provenance.response_bytes == len(raw)
    assert response.provenance.correlation_id == "run-1"
    assert ("etag", '"safe"') in response.provenance.safe_response_headers
    assert "Authorization" not in repr(response.provenance.safe_response_headers)
    with pytest.raises(TypeError):
        response.normalized_payload["cik"] = "changed"  # type: ignore[index]


def test_cache_disabled_miss_hit_expiration_and_deterministic_eviction() -> None:
    times = iter([0.0, 1.0, 20.0])
    cache = BoundedMemoryCache(
        max_entries=1, max_total_bytes=1_000, monotonic=lambda: next(times)
    )
    first_opener = RecordingOpener([FakeResponse(b'{"cik":"0000320193"}')])
    sec = provider(first_opener, cache=cache)
    cached_request = request(cache_ttl_seconds=10)
    first = sec.acquire(cached_request)
    second = sec.acquire(cached_request)
    assert first.provenance.cache_status == "miss"
    assert second.provenance.cache_status == "hit"
    assert len(first_opener.requests) == 1
    assert cache.get(cached_request.request_id) is None

    disabled_opener = RecordingOpener([FakeResponse(), FakeResponse()])
    disabled = provider(disabled_opener, cache=cache)
    disabled.acquire(request())
    disabled.acquire(request())
    assert len(disabled_opener.requests) == 2

    eviction_clock = [0.0]
    eviction_cache = BoundedMemoryCache(
        max_entries=1, max_total_bytes=1_000, monotonic=lambda: eviction_clock[0]
    )
    eviction_opener = RecordingOpener(
        [FakeResponse(b'{"cik":"0000320193"}'), FakeResponse(b'{"cik":"0000789019"}')]
    )
    eviction_provider = provider(eviction_opener, cache=eviction_cache)
    old_request = request(cache_ttl_seconds=10)
    new_request = request(cik="789019", cache_ttl_seconds=10)
    eviction_provider.acquire(old_request)
    eviction_provider.acquire(new_request)
    assert eviction_cache.get(old_request.request_id) is None
    assert eviction_cache.get(new_request.request_id) is not None


def test_cache_key_is_request_id_and_contains_no_credentials() -> None:
    req = request(cache_ttl_seconds=10)
    assert IDENTITY not in req.request_id
    assert "@" not in req.request_id
    assert len(req.request_id) == 64


def test_cache_failure_does_not_corrupt_successful_acquisition() -> None:
    class BrokenCache(BoundedMemoryCache):
        def get(self, key: str):  # type: ignore[no-untyped-def]
            raise RuntimeError("cache unavailable")

        def put(self, key: str, response: object, ttl_seconds: float) -> None:
            raise RuntimeError("cache unavailable")

    cache = BrokenCache(max_entries=1, max_total_bytes=1_000)
    response = provider(RecordingOpener(), cache=cache).acquire(
        request(cache_ttl_seconds=10)
    )
    assert response.provenance.cache_status == "miss"


def test_local_rate_limiter_allows_then_rejects_and_recovers() -> None:
    current = [0.0]
    limiter = BoundedRateLimiter(2, 10, monotonic=lambda: current[0])
    limiter.acquire()
    limiter.acquire()
    assert limiter.remaining() == 0
    with pytest.raises(FinancialDataRateLimitError) as caught:
        limiter.acquire()
    assert caught.value.retry_after_seconds == 10
    current[0] = 10
    limiter.acquire()
    assert limiter.remaining() == 1


def test_local_rate_limiter_wait_is_explicit_and_bounded() -> None:
    current = [0.0]

    def sleep(seconds: float) -> None:
        current[0] += seconds

    limiter = BoundedRateLimiter(
        1,
        2,
        monotonic=lambda: current[0],
        wait_policy="wait",
        sleeper=sleep,
        max_wait_seconds=2,
    )
    limiter.acquire()
    limiter.acquire()
    assert current[0] == 2


def test_health_is_local_only_and_does_not_call_network() -> None:
    opener = RecordingOpener()
    health = provider(opener).health()
    assert health.configured is True
    assert health.credential_required is False
    assert health.credential_available is False
    assert health.supported_operations == ("company_facts", "company_submissions")
    assert health.allowed_hosts == ("data.sec.gov",)
    assert opener.requests == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", "0000000001"), ("320193", "0000320193"), ("0000320193", "0000320193")],
)
def test_sec_cik_normalization(value: str, expected: str) -> None:
    assert normalize_cik(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "-1", "12345678901", " 320193"])
def test_sec_cik_validation(value: str) -> None:
    with pytest.raises(FinancialDataValidationError):
        normalize_cik(value)


def test_sec_endpoint_construction_is_exact_and_operation_allowlisted() -> None:
    sec = provider()
    submissions = request(operation="company_submissions")
    facts = request(operation="company_facts")
    assert sec.endpoint_for(submissions) == (
        "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert sec.endpoint_for(facts) == (
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    )
    with pytest.raises(FinancialDataValidationError):
        request(operation="crawl_filings")


@pytest.mark.parametrize(
    "identity",
    ["", "invented-without-contact", "test@example.invalid", "X" * 257 + " @x.invalid"],
)
def test_sec_requires_honest_shaped_identity_before_network(identity: str) -> None:
    opener = RecordingOpener()
    with pytest.raises((FinancialDataAuthenticationError, FinancialDataValidationError)):
        provider(opener, identity=identity).acquire(request())
    assert opener.requests == []


def test_entire_suite_cannot_use_socket() -> None:
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("example.invalid", 443))
    assert provider(RecordingOpener()).acquire(request()).provider_id == "sec_edgar"
