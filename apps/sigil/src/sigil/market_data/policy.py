"""Policy for deterministic governed and provider market data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sigil.accounting.models import canonical_digest

from .models import MarketDataKind, MarketDataValidationError


@dataclass(frozen=True, slots=True)
class GovernedMarketDataPolicy:
    permitted_kinds: tuple[MarketDataKind, ...] = (
        MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR,
        MarketDataKind.REFERENCE,
    )
    permitted_sources: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ("last_price",)
    maximum_age_seconds: int = 900
    expiration_age_seconds: int = 86400
    require_evidence_references: bool = True
    reject_duplicate_observation_ids: bool = True
    policy_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.maximum_age_seconds < 0:
            raise MarketDataValidationError("maximum_age_seconds must be nonnegative")
        if self.expiration_age_seconds < self.maximum_age_seconds:
            raise MarketDataValidationError(
                "expiration_age_seconds must be at least maximum_age_seconds"
            )
        if not self.permitted_kinds:
            raise MarketDataValidationError("permitted_kinds must not be empty")
        if not self.required_fields:
            raise MarketDataValidationError("required_fields must not be empty")
        object.__setattr__(
            self, "policy_identity",
            canonical_digest({
                "permitted_kinds": tuple(item.value for item in self.permitted_kinds),
                "permitted_sources": self.permitted_sources,
                "required_fields": self.required_fields,
                "maximum_age_seconds": self.maximum_age_seconds,
                "expiration_age_seconds": self.expiration_age_seconds,
                "require_evidence_references": self.require_evidence_references,
                "reject_duplicate_observation_ids": self.reject_duplicate_observation_ids,
            }),
        )

    def permits_kind(self, kind: MarketDataKind) -> bool:
        return kind in self.permitted_kinds

    def permits_source(self, source_id: str) -> bool:
        return not self.permitted_sources or source_id in self.permitted_sources


class MarketDataPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarketDataPolicy:
    iex_symbol_limit: int = 30
    delayed_sip_seconds: int = 900
    batch_size: int = 200
    request_timeout_seconds: float = 10.0
    max_retries: int = 3
    policy_version: str = "sigil-alpaca-free-v1"

    def __post_init__(self) -> None:
        if not 1 <= self.iex_symbol_limit <= 30:
            raise MarketDataPolicyError("IEX symbol limit must be between 1 and free-plan maximum 30")
        if self.delayed_sip_seconds < 900:
            raise MarketDataPolicyError("delayed SIP boundary cannot be below 15 minutes")
        if not 1 <= self.batch_size <= 1000:
            raise MarketDataPolicyError("batch size must be between 1 and 1000")
        if not 1 <= self.max_retries <= 10 or self.request_timeout_seconds <= 0:
            raise MarketDataPolicyError("retry and timeout policy is invalid")

    def validate_delayed_timestamp(self, provider_timestamp: str, received_at: str) -> None:
        provider, received = _datetime(provider_timestamp), _datetime(received_at)
        if provider > received:
            raise MarketDataPolicyError("future_timestamp")
        if (received - provider).total_seconds() < self.delayed_sip_seconds:
            raise MarketDataPolicyError("too_recent_delayed_sip")

    def decimal(self, value: object, label: str) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise MarketDataPolicyError(f"invalid_{label}")
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise MarketDataPolicyError(f"invalid_{label}") from None
        if not result.is_finite() or result < 0:
            raise MarketDataPolicyError(f"invalid_{label}")
        return result


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise MarketDataPolicyError("invalid_timestamp") from None
    if parsed.tzinfo is None:
        raise MarketDataPolicyError("timestamp_requires_timezone")
    return parsed.astimezone(timezone.utc)
