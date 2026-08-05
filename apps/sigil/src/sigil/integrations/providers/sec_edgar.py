"""Read-only governed adapter for bounded SEC EDGAR JSON operations."""

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


SEC_PROVIDER_ID = "sec_edgar"
SEC_USER_AGENT_ENVIRONMENT_VARIABLE = "SIGIL_SEC_USER_AGENT"
SEC_ADAPTER_VERSION = "sigil-sec-edgar-v1"
SEC_ALLOWED_HOSTS = ("data.sec.gov",)
SEC_SUPPORTED_OPERATIONS = ("company_facts", "company_submissions")
_CIK_RE = re.compile(r"^[0-9]{1,10}$")


def normalize_cik(value: str) -> str:
    """Validate a decimal CIK and return its SEC ten-digit representation."""

    if not isinstance(value, str) or _CIK_RE.fullmatch(value) is None:
        raise FinancialDataValidationError("CIK must contain 1 to 10 decimal digits")
    return value.zfill(10)


def sec_request(
    *,
    operation: str,
    cik: str,
    purpose: str,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 2_000_000,
    cache_ttl_seconds: float = 0.0,
    correlation_id: str | None = None,
) -> FinancialDataRequest:
    """Build a governed SEC request without accepting a URL or headers."""

    if operation not in SEC_SUPPORTED_OPERATIONS:
        raise FinancialDataValidationError("unsupported SEC operation")
    return FinancialDataRequest(
        provider_id=SEC_PROVIDER_ID,
        operation=operation,
        resource_id=normalize_cik(cik),
        purpose=purpose,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        cache_ttl_seconds=cache_ttl_seconds,
        correlation_id=correlation_id,
    )


class SECEdgarProvider:
    """Provider adapter for company submissions and company facts."""

    metadata = FinancialDataProviderMetadata(
        provider_id=SEC_PROVIDER_ID,
        display_name="SEC EDGAR",
        adapter_version=SEC_ADAPTER_VERSION,
        supported_operations=SEC_SUPPORTED_OPERATIONS,
        allowed_hosts=SEC_ALLOWED_HOSTS,
        credential_required=False,
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
        self._limiter = rate_limiter or BoundedRateLimiter(allowance=8, window_seconds=1.0)

    def endpoint_for(self, request: FinancialDataRequest) -> str:
        self._validate_request(request)
        if request.operation == "company_submissions":
            return f"https://data.sec.gov/submissions/CIK{request.resource_id}.json"
        return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{request.resource_id}.json"

    def acquire(self, request: FinancialDataRequest) -> FinancialDataResponse:
        self._validate_request(request)
        if self._cache is not None and request.cache_ttl_seconds > 0:
            try:
                cached = self._cache.get(request.request_id)
            except Exception:
                cached = None
            if cached is not None:
                return cached
        identity = self._identity.resolve(
            SEC_PROVIDER_ID, SEC_USER_AGENT_ENVIRONMENT_VARIABLE
        )
        self._validate_identity(identity)
        self._limiter.acquire()
        response = self._transport.fetch_json(
            request=request,
            url=self.endpoint_for(request),
            allowed_hosts=SEC_ALLOWED_HOSTS,
            headers={"Accept": "application/json"},
            provider_version=SEC_ADAPTER_VERSION,
            normalizer=self._normalize_response,
            credential=identity,
            credential_placement=CredentialPlacement(kind="header", name="User-Agent"),
        )
        if self._cache is not None:
            try:
                self._cache.put(request.request_id, response, request.cache_ttl_seconds)
            except Exception:
                pass
        return response

    def health(self) -> FinancialDataProviderHealth:
        available = self._identity.available(
            SEC_PROVIDER_ID, SEC_USER_AGENT_ENVIRONMENT_VARIABLE
        )
        return FinancialDataProviderHealth(
            provider_id=SEC_PROVIDER_ID,
            configured=available,
            credential_required=False,
            credential_available=False,
            supported_operations=SEC_SUPPORTED_OPERATIONS,
            allowed_hosts=SEC_ALLOWED_HOSTS,
            cache_enabled=self._cache is not None,
            rate_limit_remaining=self._limiter.remaining(),
            locally_available=available,
        )

    @staticmethod
    def _validate_request(request: FinancialDataRequest) -> None:
        if not isinstance(request, FinancialDataRequest):
            raise FinancialDataValidationError("SEC acquisition requires FinancialDataRequest")
        if request.provider_id != SEC_PROVIDER_ID:
            raise FinancialDataValidationError("request provider_id does not match SEC adapter")
        if request.operation not in SEC_SUPPORTED_OPERATIONS:
            raise FinancialDataValidationError("unsupported SEC operation")
        if request.query_parameters:
            raise FinancialDataValidationError("SEC operations do not accept query parameters")
        if normalize_cik(request.resource_id) != request.resource_id:
            raise FinancialDataValidationError("SEC request CIK must be normalized")

    @staticmethod
    def _validate_identity(value: str) -> None:
        if len(value) > 256 or "@" not in value or len(value.split()) < 2:
            raise FinancialDataValidationError(
                "SEC user agent must identify an application and contact method"
            )

    @staticmethod
    def _normalize_response(value: object) -> object:
        if not isinstance(value, dict):
            raise FinancialDataValidationError("SEC response root must be an object")
        return value
