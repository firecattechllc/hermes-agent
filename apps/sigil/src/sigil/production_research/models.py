"""Immutable Decimal-first production research models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def checksum(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def decimal(value: object, name: str, *, nonnegative: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} is not a valid decimal") from None
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError(f"{name} is outside the valid range")
    return result


def parse_time(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not a valid timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{name} requires a timezone")
    return parsed


class EvidenceStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    CONTRADICTORY = "contradictory"
    MALFORMED = "malformed"
    UNSUPPORTED = "unsupported"
    PROVIDER_ERROR = "provider_error"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class MarketBar:
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        parse_time(self.timestamp, "bar timestamp")
        values = (self.open, self.high, self.low, self.close)
        if any(value <= 0 for value in values) or self.volume < 0:
            raise ValueError("bar contains impossible price or volume")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high is contradictory")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low is contradictory")

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
        }


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    symbol: str
    observed_at: str
    received_at: str
    source: str
    feed: str
    adjustment: str
    status: EvidenceStatus
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    last_trade: Decimal | None
    last_trade_at: str | None
    daily_bars: tuple[MarketBar, ...]
    missing_classifications: tuple[str, ...] = ()
    demonstration: bool = False

    def __post_init__(self) -> None:
        parse_time(self.observed_at, "evidence observation")
        parse_time(self.received_at, "evidence receipt")
        if self.demonstration and self.status is EvidenceStatus.COMPLETE:
            raise ValueError("demonstration evidence cannot be production-complete")
        if self.bid is not None and self.bid <= 0:
            raise ValueError("bid must be positive")
        if self.ask is not None and self.ask <= 0:
            raise ValueError("ask must be positive")
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("quote is crossed")
        if self.last_trade is not None and self.last_trade <= 0:
            raise ValueError("last trade must be positive")

    @property
    def evidence_checksum(self) -> str:
        return checksum(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "observed_at": self.observed_at,
            "received_at": self.received_at,
            "source": self.source,
            "feed": self.feed,
            "adjustment": self.adjustment,
            "status": self.status.value,
            "bid": None if self.bid is None else str(self.bid),
            "ask": None if self.ask is None else str(self.ask),
            "bid_size": None if self.bid_size is None else str(self.bid_size),
            "ask_size": None if self.ask_size is None else str(self.ask_size),
            "last_trade": None if self.last_trade is None else str(self.last_trade),
            "last_trade_at": self.last_trade_at,
            "daily_bars": [bar.to_dict() for bar in self.daily_bars],
            "missing_classifications": list(self.missing_classifications),
            "demonstration": self.demonstration,
        }


@dataclass(frozen=True, slots=True)
class ProductionStrategyPolicy:
    strategy_id: str = "sigil-liquid-trend"
    strategy_version: str = "2.8.0"
    minimum_history_bars: int = 50
    short_average_bars: int = 20
    medium_average_bars: int = 50
    momentum_lookback_bars: int = 20
    reversal_lookback_bars: int = 5
    minimum_price: Decimal = Decimal(5)
    maximum_quote_age_seconds: int = 30
    maximum_bar_age_seconds: int = 129600
    maximum_spread_bps: Decimal = Decimal(40)
    minimum_average_dollar_volume: Decimal = Decimal(5000000)
    minimum_relative_volume: Decimal = Decimal("0.75")
    maximum_annualized_volatility: Decimal = Decimal("0.80")
    maximum_twenty_day_momentum: Decimal = Decimal("0.25")
    maximum_five_day_gain: Decimal = Decimal("0.12")
    maximum_gap: Decimal = Decimal("0.10")
    minimum_normalized_score: Decimal = Decimal("0.68")
    minimum_confidence: Decimal = Decimal("0.80")
    shadow_slippage_bps: Decimal = Decimal(5)
    proposal_ttl_seconds: int = 60
    maximum_holding_days: int = 10
    stop_loss_percent: Decimal = Decimal("0.05")
    take_profit_percent: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        if self.minimum_history_bars < self.medium_average_bars:
            raise ValueError("history must cover the medium moving average")
        if not Decimal(0) < self.minimum_normalized_score <= Decimal(1):
            raise ValueError("minimum score is invalid")
        if not Decimal(0) < self.minimum_confidence <= Decimal(1):
            raise ValueError("minimum confidence is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


@dataclass(frozen=True, slots=True)
class StrategyScore:
    strategy_id: str
    strategy_version: str
    symbol: str
    timestamp: str
    total_score: Decimal
    normalized_score: Decimal
    confidence: Decimal
    component_scores: tuple[tuple[str, Decimal], ...]
    component_evidence: tuple[tuple[str, str], ...]
    hard_rejection_reasons: tuple[str, ...]
    soft_penalties: tuple[str, ...]
    eligible: bool
    proposal_recommendation: str
    evidence_checksum: str
    average_dollar_volume: Decimal
    spread_bps: Decimal

    def ranking_key(self) -> tuple[Decimal, Decimal, Decimal, Decimal, str]:
        return (
            -self.normalized_score,
            -self.confidence,
            -self.average_dollar_volume,
            self.spread_bps,
            self.symbol,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "total_score": str(self.total_score),
            "normalized_score": str(self.normalized_score),
            "confidence": str(self.confidence),
            "component_scores": {key: str(value) for key, value in self.component_scores},
            "component_evidence": dict(self.component_evidence),
            "hard_rejection_reasons": list(self.hard_rejection_reasons),
            "soft_penalties": list(self.soft_penalties),
            "eligible": self.eligible,
            "proposal_recommendation": self.proposal_recommendation,
            "evidence_checksum": self.evidence_checksum,
            "average_dollar_volume": str(self.average_dollar_volume),
            "spread_bps": str(self.spread_bps),
        }
