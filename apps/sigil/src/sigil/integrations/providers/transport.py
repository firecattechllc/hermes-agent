"""Governed HTTPS transport, credentials, cache, rate limiting, and redaction."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import replace
from datetime import datetime
from email.message import Message
from hashlib import sha256
import json
import os
import re
import time
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import (
    PROVIDER_SCHEMA_VERSION,
    FinancialDataAuthenticationError,
    FinancialDataCacheEntry,
    FinancialDataProvenance,
    FinancialDataRateLimitError,
    FinancialDataRequest,
    FinancialDataResponse,
    FinancialDataTransportError,
    FinancialDataValidationError,
    freeze_json,
    utc_now,
)


SAFE_RESPONSE_HEADERS = frozenset(
    {"content-type", "date", "etag", "last-modified", "x-ratelimit-limit", "x-ratelimit-remaining"}
)
TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
AUTH_HEADER_NAMES = frozenset({"authorization", "proxy-authorization"})
SECRET_QUERY_NAMES = frozenset({"apikey", "api_key", "api-key", "token", "access_token", "key"})
_SECRET_LABEL_RE = re.compile(
    r"(?i)\b(authorization|api[-_]?key|access[-_]?token|token|secret|credential)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def redact_text(value: object, secrets: tuple[str, ...] = ()) -> str:
    """Redact configured secret values and common labelled credential forms."""

    text = str(value)
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return _SECRET_LABEL_RE.sub(r"\1\2[REDACTED]", text)


class CredentialResolver(Protocol):
    def resolve(self, provider_id: str, environment_variable: str) -> str: ...

    def available(self, provider_id: str, environment_variable: str) -> bool: ...


class MappingCredentialResolver:
    """Resolve only explicitly provided provider credentials."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def resolve(self, provider_id: str, environment_variable: str) -> str:
        value = self._values.get(provider_id)
        if not isinstance(value, str) or not value.strip():
            raise FinancialDataAuthenticationError(f"configuration missing for {provider_id}")
        return value

    def available(self, provider_id: str, environment_variable: str) -> bool:
        value = self._values.get(provider_id)
        return isinstance(value, str) and bool(value.strip())


class EnvironmentCredentialResolver:
    """Read a fixed allowlist of provider-to-environment-variable bindings."""

    def __init__(
        self,
        allowed_variables: Mapping[str, str],
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._allowed = dict(allowed_variables)
        self._environ = os.environ if environ is None else environ

    def _validate(self, provider_id: str, environment_variable: str) -> None:
        if self._allowed.get(provider_id) != environment_variable:
            raise FinancialDataAuthenticationError("environment variable is not allowlisted")

    def resolve(self, provider_id: str, environment_variable: str) -> str:
        self._validate(provider_id, environment_variable)
        value = self._environ.get(environment_variable)
        if not isinstance(value, str) or not value.strip():
            raise FinancialDataAuthenticationError(f"configuration missing for {provider_id}")
        return value

    def available(self, provider_id: str, environment_variable: str) -> bool:
        self._validate(provider_id, environment_variable)
        value = self._environ.get(environment_variable)
        return isinstance(value, str) and bool(value.strip())


class CredentialPlacement:
    """Header- or query-based credential placement at the final boundary."""

    def __init__(self, *, kind: str, name: str, prefix: str = "") -> None:
        if kind not in {"header", "query"}:
            raise FinancialDataValidationError("credential placement kind is invalid")
        if not isinstance(name, str) or not name.strip():
            raise FinancialDataValidationError("credential placement name is invalid")
        self._kind = kind
        self._name = name
        self._prefix = prefix

    def apply(
        self, headers: dict[str, str], query: list[tuple[str, str]], credential: str
    ) -> None:
        value = f"{self._prefix}{credential}"
        if self._kind == "header":
            headers[self._name] = value
        else:
            query.append((self._name, value))

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(kind={self._kind!r}, name={self._name!r}, "
            f"prefix={self._prefix!r})"
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _default_opener(request: Request, timeout: float):  # type: ignore[no-untyped-def]
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


class BoundedRateLimiter:
    """Deterministic sliding-window provider-local limiter."""

    def __init__(
        self,
        allowance: int,
        window_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wait_policy: str = "reject",
        sleeper: Callable[[float], None] = time.sleep,
        max_wait_seconds: float = 0.0,
    ) -> None:
        if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance < 1:
            raise FinancialDataValidationError("allowance must be a positive integer")
        if window_seconds <= 0:
            raise FinancialDataValidationError("window_seconds must be positive")
        if wait_policy not in {"reject", "wait"} or max_wait_seconds < 0:
            raise FinancialDataValidationError("rate-limit wait policy is invalid")
        self._allowance = allowance
        self._window = float(window_seconds)
        self._clock = monotonic
        self._wait_policy = wait_policy
        self._sleeper = sleeper
        self._max_wait = float(max_wait_seconds)
        self._events: deque[float] = deque()

    def acquire(self) -> None:
        now = self._clock()
        self._prune(now)
        if len(self._events) < self._allowance:
            self._events.append(now)
            return
        wait = max(0.0, self._events[0] + self._window - now)
        if self._wait_policy != "wait" or wait > self._max_wait:
            raise FinancialDataRateLimitError(
                "local provider rate limit exceeded", retry_after_seconds=wait
            )
        self._sleeper(wait)
        now = self._clock()
        self._prune(now)
        if len(self._events) >= self._allowance:
            raise FinancialDataRateLimitError("local provider rate limit remains exceeded")
        self._events.append(now)

    def remaining(self) -> int:
        self._prune(self._clock())
        return self._allowance - len(self._events)

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] >= self._window:
            self._events.popleft()


class BoundedMemoryCache:
    """Optional LRU cache with explicit TTL and byte/entry limits."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_total_bytes: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1:
            raise FinancialDataValidationError("cache limits must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_total_bytes
        self._clock = monotonic
        self._entries: OrderedDict[str, FinancialDataCacheEntry] = OrderedDict()
        self._bytes = 0

    def get(self, key: str) -> FinancialDataResponse | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at_monotonic:
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return replace(
            entry.response,
            provenance=replace(entry.response.provenance, cache_status="hit"),
        )

    def put(self, key: str, response: FinancialDataResponse, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            return
        size = response.provenance.response_bytes
        if size > self._max_bytes:
            return
        if key in self._entries:
            self._drop(key)
        now = self._clock()
        self._entries[key] = FinancialDataCacheEntry(key, response, now, now + ttl_seconds, size)
        self._bytes += size
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            self._drop(next(iter(self._entries)))

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key)
        self._bytes -= entry.byte_count


class GovernedHTTPSTransport:
    """Standard-library-only, non-redirecting, bounded JSON transport."""

    def __init__(
        self,
        *,
        opener: Callable[[Request, float], object] = _default_opener,
        wall_clock: Callable[[], datetime] = utc_now,
        sleeper: Callable[[float], None] = time.sleep,
        max_retries: int = 1,
        max_retry_after_seconds: float = 30.0,
    ) -> None:
        if not 0 <= max_retries <= 3:
            raise FinancialDataValidationError("max_retries must be between 0 and 3")
        self._opener = opener
        self._clock = wall_clock
        self._sleeper = sleeper
        self._max_retries = max_retries
        self._max_retry_after = max_retry_after_seconds

    def fetch_json(
        self,
        *,
        request: FinancialDataRequest,
        url: str,
        allowed_hosts: tuple[str, ...],
        headers: Mapping[str, str],
        provider_version: str,
        normalizer: Callable[[object], object],
        credential: str | None = None,
        credential_placement: CredentialPlacement | None = None,
    ) -> FinancialDataResponse:
        split = urlsplit(url)
        if split.scheme != "https":
            raise FinancialDataValidationError("provider endpoint must use HTTPS")
        if split.username or split.password or split.fragment or split.hostname not in allowed_hosts:
            raise FinancialDataValidationError("provider endpoint is not allowlisted")
        query = parse_qsl(split.query, keep_blank_values=True)
        final_headers = dict(headers)
        if credential is not None:
            if credential_placement is None:
                raise FinancialDataValidationError("credential placement is required")
            credential_placement.apply(final_headers, query, credential)
        final_url = urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))
        requested_at = self._clock()
        secrets = (credential,) if credential else ()
        for attempt in range(self._max_retries + 1):
            outbound = Request(final_url, headers=final_headers, method="GET")
            try:
                response = self._opener(outbound, float(request.timeout_seconds))
                status = int(response.getcode())  # type: ignore[attr-defined]
                response_headers = response.headers  # type: ignore[attr-defined]
                raw = response.read(request.max_response_bytes + 1)  # type: ignore[attr-defined]
            except HTTPError as exc:
                status = exc.code
                response_headers = exc.headers
                raw = b""
            except (URLError, TimeoutError, OSError):
                if attempt < self._max_retries:
                    continue
                raise FinancialDataTransportError(
                    redact_text("provider transport failed", secrets)
                ) from None
            if status in TRANSIENT_STATUSES and attempt < self._max_retries:
                continue
            break
        if status in {401, 403}:
            raise FinancialDataAuthenticationError("provider authentication rejected")
        if status == 429:
            retry_after = self._retry_after(response_headers)
            raise FinancialDataRateLimitError(
                "provider rate limit exceeded", retry_after_seconds=retry_after
            )
        if not 200 <= status < 300:
            raise FinancialDataTransportError(f"provider returned HTTP {status}")
        if len(raw) > request.max_response_bytes:
            raise FinancialDataTransportError("provider response exceeds maximum bytes")
        declared_length = response_headers.get("Content-Length")
        if declared_length is not None:
            try:
                expected_length = int(declared_length)
            except (TypeError, ValueError):
                raise FinancialDataValidationError(
                    "provider Content-Length is malformed"
                ) from None
            if expected_length < 0 or expected_length != len(raw):
                raise FinancialDataTransportError("provider response is incomplete")
        content_type = response_headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise FinancialDataValidationError("provider response must be application/json")
        try:
            decoded = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinancialDataValidationError("provider returned malformed JSON") from exc
        try:
            normalized = freeze_json(normalizer(decoded))
        except Exception as exc:
            if isinstance(exc, FinancialDataValidationError):
                raise
            raise FinancialDataValidationError("provider response normalization failed") from None
        safe_headers = tuple(
            sorted(
                (name.lower(), value)
                for name, value in response_headers.items()
                if name.lower() in SAFE_RESPONSE_HEADERS
                and name.lower() not in AUTH_HEADER_NAMES
            )
        )
        safe_query_names = tuple(
            sorted(name for name, _ in query if name.lower() not in SECRET_QUERY_NAMES)
        )
        endpoint = urlunsplit((split.scheme, split.netloc, split.path, "", ""))
        digest = sha256(raw).hexdigest()
        provenance = FinancialDataProvenance(
            provider_id=request.provider_id,
            operation=request.operation,
            request_id=request.request_id,
            requested_at=requested_at,
            responded_at=self._clock(),
            http_status=status,
            endpoint_identity=endpoint,
            query_parameter_names=safe_query_names,
            content_sha256=digest,
            response_bytes=len(raw),
            cache_status="miss",
            provider_version=provider_version,
            correlation_id=request.correlation_id,
            safe_response_headers=safe_headers,
        )
        return FinancialDataResponse(
            schema_version=PROVIDER_SCHEMA_VERSION,
            request_id=request.request_id,
            provider_id=request.provider_id,
            operation=request.operation,
            normalized_payload=normalized,
            raw_content_sha256=digest,
            provenance=provenance,
        )

    def _retry_after(self, headers: Message | Mapping[str, str]) -> float | None:
        value = headers.get("Retry-After")
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            return None
        return min(parsed, self._max_retry_after)
