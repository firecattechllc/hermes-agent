from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from urllib.parse import urlparse


def utc_now() -> datetime:
    return datetime.now(UTC)


class InstrumentType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FUND = "fund"
    INDEX = "index"
    BOND = "bond"
    CRYPTO = "crypto"
    OTHER = "other"


class FilingType(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"
    EIGHT_K = "8-K"
    TWENTY_F = "20-F"
    SIX_K = "6-K"
    PROXY = "DEF 14A"
    EARNINGS_RELEASE = "earnings-release"
    OTHER = "other"


class SentimentLabel(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class ConfidenceScore:
    value: float
    rationale: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("confidence value must be between 0 and 1")
        if not self.rationale.strip():
            raise ValueError("confidence rationale is required")


@dataclass(frozen=True, slots=True)
class Provenance:
    source_name: str
    source_uri: str
    retrieved_at: datetime = field(default_factory=utc_now)
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.source_name.strip():
            raise ValueError("source_name is required")
        parsed = urlparse(self.source_uri)
        if parsed.scheme not in {"https", "http", "file", "hermes"}:
            raise ValueError("source_uri must use https, http, file, or hermes")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")

    @classmethod
    def for_text(
        cls,
        *,
        source_name: str,
        source_uri: str,
        text: str,
        retrieved_at: datetime | None = None,
    ) -> Provenance:
        return cls(
            source_name=source_name,
            source_uri=source_uri,
            retrieved_at=retrieved_at or utc_now(),
            content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    instrument_type: InstrumentType
    exchange: str | None = None
    currency: str = "USD"

    def normalized(self) -> Instrument:
        symbol = self.symbol.strip().upper()
        name = self.name.strip()
        exchange = self.exchange.strip().upper() if self.exchange else None
        currency = self.currency.strip().upper()
        if not symbol:
            raise ValueError("instrument symbol is required")
        if not name:
            raise ValueError("instrument name is required")
        if len(currency) != 3:
            raise ValueError("currency must be a three-letter code")
        return Instrument(
            symbol=symbol,
            name=name,
            instrument_type=self.instrument_type,
            exchange=exchange,
            currency=currency,
        )


@dataclass(frozen=True, slots=True)
class Filing:
    instrument: Instrument
    filing_type: FilingType
    filed_on: date
    period_end: date | None
    accession_number: str | None
    provenance: Provenance

    def __post_init__(self) -> None:
        if self.period_end and self.period_end > self.filed_on:
            raise ValueError("period_end cannot be after filed_on")


@dataclass(frozen=True, slots=True)
class FinancialDocument:
    document_id: str
    text: str
    provenance: Provenance
    instrument: Instrument | None = None
    filing: Filing | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id is required")
        if not self.text.strip():
            raise ValueError("document text is required")
        if (
            self.provenance.content_sha256 is not None
            and self.provenance.content_sha256
            != sha256(self.text.encode("utf-8")).hexdigest()
        ):
            raise ValueError("document text does not match provenance hash")


@dataclass(frozen=True, slots=True)
class SentimentResult:
    label: SentimentLabel
    positive: float
    neutral: float
    negative: float
    confidence: ConfidenceScore
    model_name: str
    model_version: str
    analyzed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        scores = (self.positive, self.neutral, self.negative)
        if any(score < 0.0 or score > 1.0 for score in scores):
            raise ValueError("sentiment scores must be between 0 and 1")
        if abs(sum(scores) - 1.0) > 1e-6:
            raise ValueError("sentiment scores must sum to 1")
        if not self.model_name.strip() or not self.model_version.strip():
            raise ValueError("model name and version are required")
        if self.analyzed_at.tzinfo is None:
            raise ValueError("analyzed_at must be timezone-aware")
