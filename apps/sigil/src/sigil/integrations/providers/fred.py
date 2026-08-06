"""Read-only governed adapter for the official FRED (Federal Reserve Economic Data) API.

Hermes add-on continuation run. Mirrors ``sec_edgar.py``'s pattern exactly
(same ``GovernedHTTPSTransport``, ``BoundedRateLimiter``, ``BoundedMemoryCache``,
``CredentialResolver`` boundary) so both providers share one deterministic,
host-allowlisted acquisition contract. Upstream: https://fred.stlouisfed.org/docs/api/fred/
(official St. Louis Fed API, ``api.stlouisfed.org``, requires a free
registered API key -- never fabricated here, resolved only through the
existing ``CredentialResolver`` boundary).
"""

from __future__ import annotations

import re

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

FRED_PROVIDER_ID = "fred"
FRED_API_KEY_ENVIRONMENT_VARIABLE = "SIGIL_FRED_API_KEY"
FRED_ADAPTER_VERSION = "sigil-fred-v1"
FRED_ALLOWED_HOSTS = ("api.stlouisfed.org",)
FRED_SUPPORTED_OPERATIONS = ("series_metadata", "series_observations")
# FRED series IDs are short alphanumeric mnemonics (e.g. "CPIAUCSL", "GDP", "UNRATE").
_SERIES_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")


def normalize_series_id(value: str) -> str:
    """Validate a FRED series ID (e.g. ``CPIAUCSL``) and return it uppercased."""

    if not isinstance(value, str) or _SERIES_ID_RE.fullmatch(value) is None:
        raise FinancialDataValidationError(
            "FRED series ID must be 1-64 alphanumeric characters starting with a letter"
        )
    return value.upper()


def fred_request(
    *,
    operation: str,
    series_id: str,
    purpose: str,
    file_type: str = "json",
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 2_000_000,
    cache_ttl_seconds: float = 0.0,
    correlation_id: str | None = None,
) -> FinancialDataRequest:
    """Build a governed FRED request without accepting a URL, API key, or arbitrary headers."""

    if operation not in FRED_SUPPORTED_OPERATIONS:
        raise FinancialDataValidationError("unsupported FRED operation")
    if file_type != "json":
        raise FinancialDataValidationError("only file_type=json is supported")
    return FinancialDataRequest(
        provider_id=FRED_PROVIDER_ID,
        operation=operation,
        resource_id=normalize_series_id(series_id),
        purpose=purpose,
        query_parameters=(("file_type", "json"),),
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        cache_ttl_seconds=cache_ttl_seconds,
        correlation_id=correlation_id,
    )


class FredProvider:
    """Provider adapter for FRED series observations and series metadata."""

    metadata = FinancialDataProviderMetadata(
        provider_id=FRED_PROVIDER_ID,
        display_name="FRED (Federal Reserve Economic Data)",
        adapter_version=FRED_ADAPTER_VERSION,
        supported_operations=FRED_SUPPORTED_OPERATIONS,
        allowed_hosts=FRED_ALLOWED_HOSTS,
        credential_required=True,
    )

    def __init__(
        self,
        *,
        identity_resolver: CredentialResolver,
        transport: GovernedHTTPSTransport | None = None,
        cache: BoundedMemoryCache | None = None,
        rate_limiter: BoundedRateLimiter | None = None,
    ) -> None:
        self._identity = identity_resolver
        self._transport = transport or GovernedHTTPSTransport()
        self._cache = cache
        # FRED's documented free-tier guidance: stay well under 120
        # requests/minute; 4/second leaves comfortable headroom.
        self._limiter = rate_limiter or BoundedRateLimiter(allowance=4, window_seconds=1.0)

    def endpoint_for(self, request: FinancialDataRequest) -> str:
        self._validate_request(request)
        if request.operation == "series_observations":
            return (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={request.resource_id}&file_type=json"
            )
        return f"https://api.stlouisfed.org/fred/series?series_id={request.resource_id}&file_type=json"

    def acquire(self, request: FinancialDataRequest) -> FinancialDataResponse:
        self._validate_request(request)
        if self._cache is not None and request.cache_ttl_seconds > 0:
            try:
                cached = self._cache.get(request.request_id)
            except Exception:
                cached = None
            if cached is not None:
                return cached
        identity = self._identity.resolve(FRED_PROVIDER_ID, FRED_API_KEY_ENVIRONMENT_VARIABLE)
        self._validate_identity(identity)
        self._limiter.acquire()
        response = self._transport.fetch_json(
            request=request,
            url=self.endpoint_for(request),
            allowed_hosts=FRED_ALLOWED_HOSTS,
            headers={"Accept": "application/json"},
            provider_version=FRED_ADAPTER_VERSION,
            normalizer=self._normalize_response,
            credential=identity,
            credential_placement=CredentialPlacement(kind="query", name="api_key"),
        )
        if self._cache is not None:
            try:
                self._cache.put(request.request_id, response, request.cache_ttl_seconds)
            except Exception:
                pass
        return response

    def health(self) -> FinancialDataProviderHealth:
        available = self._identity.available(FRED_PROVIDER_ID, FRED_API_KEY_ENVIRONMENT_VARIABLE)
        return FinancialDataProviderHealth(
            provider_id=FRED_PROVIDER_ID,
            configured=available,
            credential_required=True,
            credential_available=available,
            supported_operations=FRED_SUPPORTED_OPERATIONS,
            allowed_hosts=FRED_ALLOWED_HOSTS,
            cache_enabled=self._cache is not None,
            rate_limit_remaining=self._limiter.remaining(),
            locally_available=available,
        )

    @staticmethod
    def _validate_request(request: FinancialDataRequest) -> None:
        if not isinstance(request, FinancialDataRequest):
            raise FinancialDataValidationError("FRED acquisition requires FinancialDataRequest")
        if request.provider_id != FRED_PROVIDER_ID:
            raise FinancialDataValidationError("request provider_id does not match FRED adapter")
        if request.operation not in FRED_SUPPORTED_OPERATIONS:
            raise FinancialDataValidationError("unsupported FRED operation")
        if request.query_parameters != (("file_type", "json"),):
            raise FinancialDataValidationError("FRED requests only accept the fixed file_type=json parameter")
        if normalize_series_id(request.resource_id) != request.resource_id:
            raise FinancialDataValidationError("FRED request series_id must be normalized")

    @staticmethod
    def _validate_identity(value: str) -> None:
        # FRED API keys are 32-character lowercase alphanumeric strings.
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]{32}", value):
            raise FinancialDataValidationError(
                "FRED API key must be a 32-character lowercase alphanumeric string"
            )

    @staticmethod
    def _normalize_response(value: object) -> object:
        if not isinstance(value, dict):
            raise FinancialDataValidationError("FRED response root must be an object")
        return value
