"""Immutable contracts for governed external financial-data acquisition."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Mapping


PROVIDER_SCHEMA_VERSION = 1
MIN_TIMEOUT_SECONDS = 0.1
MAX_TIMEOUT_SECONDS = 30.0
MIN_RESPONSE_BYTES = 1
MAX_RESPONSE_BYTES = 10_000_000
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CORRELATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_QUERY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SECRET_NAME_RE = re.compile(
    r"(?:authorization|api[-_]?key|access[-_]?token|auth[-_]?token|secret|credential)",
    re.IGNORECASE,
)
_JsonScalar = str | int | float | bool | None
ImmutableJSON = _JsonScalar | tuple["ImmutableJSON", ...] | Mapping[str, "ImmutableJSON"]


class FinancialDataProviderError(RuntimeError):
    """Base error for governed provider failures."""


class FinancialDataAuthenticationError(FinancialDataProviderError):
    """Raised when provider identity or credentials are unavailable or rejected."""


class FinancialDataRateLimitError(FinancialDataProviderError):
    """Raised when local or remote rate limits deny a request."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class FinancialDataTransportError(FinancialDataProviderError):
    """Raised when bounded HTTPS transport fails."""


class FinancialDataValidationError(FinancialDataProviderError, ValueError):
    """Raised when a provider contract fails closed."""


def _require_text(value: object, name: str, *, maximum: int = 1_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FinancialDataValidationError(f"{name} must be non-empty and at most {maximum} chars")
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FinancialDataValidationError("value must be canonical JSON") from exc


def freeze_json(value: object) -> ImmutableJSON:
    """Return a recursively immutable, deterministic JSON-compatible value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FinancialDataValidationError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        items: list[tuple[str, ImmutableJSON]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise FinancialDataValidationError("JSON object keys must be strings")
            items.append((key, freeze_json(item)))
        items.sort(key=lambda pair: pair[0])
        if len({key for key, _ in items}) != len(items):
            raise FinancialDataValidationError("JSON object keys must be unique")
        return MappingProxyType(dict(items))
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    raise FinancialDataValidationError("value must be JSON-compatible")


def thaw_json(value: ImmutableJSON) -> object:
    """Convert immutable JSON into ordinary JSON containers for serialization."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _validate_timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FinancialDataValidationError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FinancialDataRequest:
    """One bounded provider-neutral acquisition request without credentials."""

    provider_id: str
    operation: str
    resource_id: str
    purpose: str
    query_parameters: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 10.0
    max_response_bytes: int = 1_000_000
    cache_ttl_seconds: float = 0.0
    correlation_id: str | None = None
    request_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _ID_RE.fullmatch(self.provider_id) is None:
            raise FinancialDataValidationError("provider_id is invalid")
        if not isinstance(self.operation, str) or _OPERATION_RE.fullmatch(self.operation) is None:
            raise FinancialDataValidationError("operation is invalid")
        _require_text(self.resource_id, "resource_id", maximum=512)
        _require_text(self.purpose, "purpose", maximum=500)
        if "://" in self.resource_id:
            raise FinancialDataValidationError("resource_id must not be an arbitrary URL")
        if not isinstance(self.query_parameters, tuple):
            raise FinancialDataValidationError("query_parameters must be a tuple")
        normalized: list[tuple[str, str]] = []
        for pair in self.query_parameters:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise FinancialDataValidationError("query_parameters must contain name/value tuples")
            name, value = pair
            if not isinstance(name, str) or _QUERY_NAME_RE.fullmatch(name) is None:
                raise FinancialDataValidationError("query parameter name is invalid")
            if _SECRET_NAME_RE.search(name):
                raise FinancialDataValidationError("credential-bearing query parameters are forbidden")
            _require_text(value, "query parameter value", maximum=500)
            normalized.append((name, value))
        normalized.sort()
        if len({name for name, _ in normalized}) != len(normalized):
            raise FinancialDataValidationError("query parameter names must be unique")
        object.__setattr__(self, "query_parameters", tuple(normalized))
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not MIN_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise FinancialDataValidationError(
                f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}"
            )
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not MIN_RESPONSE_BYTES <= self.max_response_bytes <= MAX_RESPONSE_BYTES
        ):
            raise FinancialDataValidationError(
                f"max_response_bytes must be between {MIN_RESPONSE_BYTES} and {MAX_RESPONSE_BYTES}"
            )
        if (
            isinstance(self.cache_ttl_seconds, bool)
            or not isinstance(self.cache_ttl_seconds, (int, float))
            or not math.isfinite(self.cache_ttl_seconds)
            or not 0 <= self.cache_ttl_seconds <= 86_400
        ):
            raise FinancialDataValidationError("cache_ttl_seconds must be between 0 and 86400")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or _CORRELATION_RE.fullmatch(self.correlation_id) is None
        ):
            raise FinancialDataValidationError("correlation_id is invalid")
        object.__setattr__(self, "request_id", sha256(self.canonical_bytes()).hexdigest())

    def canonical_dict(self) -> dict[str, object]:
        return {
            "cache_ttl_seconds": float(self.cache_ttl_seconds),
            "correlation_id": self.correlation_id,
            "max_response_bytes": self.max_response_bytes,
            "operation": self.operation,
            "provider_id": self.provider_id,
            "purpose": self.purpose,
            "query_parameters": list(self.query_parameters),
            "resource_id": self.resource_id,
            "timeout_seconds": float(self.timeout_seconds),
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.canonical_dict())


@dataclass(frozen=True, slots=True)
class FinancialDataProviderMetadata:
    provider_id: str
    display_name: str
    adapter_version: str
    supported_operations: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    credential_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _ID_RE.fullmatch(self.provider_id) is None:
            raise FinancialDataValidationError("metadata provider_id is invalid")
        _require_text(self.display_name, "display_name", maximum=100)
        _require_text(self.adapter_version, "adapter_version", maximum=100)
        if (
            not isinstance(self.supported_operations, tuple)
            or not self.supported_operations
            or tuple(sorted(set(self.supported_operations))) != self.supported_operations
            or any(_OPERATION_RE.fullmatch(item) is None for item in self.supported_operations)
        ):
            raise FinancialDataValidationError(
                "supported_operations must be a sorted unique tuple"
            )
        if (
            not isinstance(self.allowed_hosts, tuple)
            or not self.allowed_hosts
            or tuple(sorted(set(self.allowed_hosts))) != self.allowed_hosts
            or any(
                not isinstance(host, str)
                or host != host.lower()
                or "/" in host
                or ":" in host
                or "." not in host
                for host in self.allowed_hosts
            )
        ):
            raise FinancialDataValidationError("allowed_hosts must be a sorted unique tuple")
        if not isinstance(self.credential_required, bool):
            raise FinancialDataValidationError("credential_required must be a boolean")


@dataclass(frozen=True, slots=True)
class FinancialDataProvenance:
    provider_id: str
    operation: str
    request_id: str
    requested_at: datetime
    responded_at: datetime
    http_status: int
    endpoint_identity: str
    query_parameter_names: tuple[str, ...]
    content_sha256: str
    response_bytes: int
    cache_status: str
    provider_version: str
    correlation_id: str | None
    safe_response_headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _ID_RE.fullmatch(self.provider_id) is None:
            raise FinancialDataValidationError("provenance provider_id is invalid")
        if not isinstance(self.operation, str) or _OPERATION_RE.fullmatch(self.operation) is None:
            raise FinancialDataValidationError("provenance operation is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.request_id):
            raise FinancialDataValidationError("provenance request_id is invalid")
        _validate_timestamp(self.requested_at, "requested_at")
        _validate_timestamp(self.responded_at, "responded_at")
        if self.responded_at < self.requested_at:
            raise FinancialDataValidationError("responded_at cannot precede requested_at")
        if self.cache_status not in {"miss", "hit"}:
            raise FinancialDataValidationError("cache_status must be miss or hit")
        if not 100 <= self.http_status <= 599:
            raise FinancialDataValidationError("http_status is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise FinancialDataValidationError("content_sha256 is invalid")
        if (
            isinstance(self.response_bytes, bool)
            or not isinstance(self.response_bytes, int)
            or self.response_bytes < 0
        ):
            raise FinancialDataValidationError("response_bytes is invalid")
        if not isinstance(self.query_parameter_names, tuple):
            raise FinancialDataValidationError("query_parameter_names must be a tuple")
        if not isinstance(self.safe_response_headers, tuple):
            raise FinancialDataValidationError("safe_response_headers must be a tuple")


@dataclass(frozen=True, slots=True)
class FinancialDataResponse:
    schema_version: int
    request_id: str
    provider_id: str
    operation: str
    normalized_payload: ImmutableJSON
    raw_content_sha256: str
    provenance: FinancialDataProvenance
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_SCHEMA_VERSION:
            raise FinancialDataValidationError("unsupported response schema_version")
        object.__setattr__(self, "normalized_payload", freeze_json(thaw_json(self.normalized_payload)))
        if self.request_id != self.provenance.request_id:
            raise FinancialDataValidationError("response request_id does not match provenance")
        if self.provider_id != self.provenance.provider_id:
            raise FinancialDataValidationError("response provider_id does not match provenance")
        if self.operation != self.provenance.operation:
            raise FinancialDataValidationError("response operation does not match provenance")
        if self.raw_content_sha256 != self.provenance.content_sha256:
            raise FinancialDataValidationError("response content hash does not match provenance")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(item, str) for item in self.warnings
        ):
            raise FinancialDataValidationError("warnings must be a tuple of strings")


@dataclass(frozen=True, slots=True)
class FinancialDataCacheEntry:
    cache_key: str
    response: FinancialDataResponse
    stored_at_monotonic: float
    expires_at_monotonic: float
    byte_count: int

    def __post_init__(self) -> None:
        if self.cache_key != self.response.request_id:
            raise FinancialDataValidationError("cache_key must equal response request_id")
        if self.expires_at_monotonic <= self.stored_at_monotonic:
            raise FinancialDataValidationError("cache expiration must follow storage time")
        if self.byte_count != self.response.provenance.response_bytes:
            raise FinancialDataValidationError("cache byte_count must match response")


@dataclass(frozen=True, slots=True)
class FinancialDataProviderHealth:
    provider_id: str
    configured: bool
    credential_required: bool
    credential_available: bool
    supported_operations: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    cache_enabled: bool
    rate_limit_remaining: int | None
    locally_available: bool

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or _ID_RE.fullmatch(self.provider_id) is None:
            raise FinancialDataValidationError("health provider_id is invalid")
        for value, name in (
            (self.configured, "configured"),
            (self.credential_required, "credential_required"),
            (self.credential_available, "credential_available"),
            (self.cache_enabled, "cache_enabled"),
            (self.locally_available, "locally_available"),
        ):
            if not isinstance(value, bool):
                raise FinancialDataValidationError(f"{name} must be a boolean")
        if self.rate_limit_remaining is not None and (
            isinstance(self.rate_limit_remaining, bool)
            or not isinstance(self.rate_limit_remaining, int)
            or self.rate_limit_remaining < 0
        ):
            raise FinancialDataValidationError("rate_limit_remaining is invalid")


def utc_now() -> datetime:
    """Default injectable wall clock."""

    return datetime.now(timezone.utc)


EMPTY_MAPPING: Mapping[str, str] = MappingProxyType({})
