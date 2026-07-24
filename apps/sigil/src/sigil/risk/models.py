"""Immutable contracts for deterministic governed portfolio risk analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sigil.accounting.models import (
    CompletenessStatus,
    canonical_digest,
    decimal_text,
    digest,
    identifier,
    reject_secret_bearing,
    timestamp,
)
from sigil.integrations.providers.models import FinancialDataValidationError

SUPPORTED_INSTRUMENTS = frozenset({"EQUITY", "ETF"})
SUPPORTED_CURRENCIES = frozenset({"USD"})
SUPPORTED_CONFIDENCE_LEVELS = frozenset({"0.9", "0.95", "0.99"})
SUPPORTED_HORIZONS = frozenset({1})
SUPPORTED_SEVERITIES = frozenset({"informational", "warning", "blocking"})
SUPPORTED_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "abs_gt"})
SUPPORTED_LIMIT_METRICS = frozenset(
    {
        "single_position_weight",
        "top_3_concentration",
        "top_5_concentration",
        "issuer_concentration",
        "sector_concentration",
        "industry_concentration",
        "etf_concentration",
        "cash_percentage",
        "invested_percentage",
        "stale_position_weight",
        "unpriced_position_weight",
        "average_daily_dollar_volume",
        "days_to_liquidate",
        "historical_volatility",
        "downside_volatility",
        "maximum_drawdown",
        "value_at_risk",
        "expected_shortfall",
        "tracking_error",
        "absolute_beta",
        "pairwise_correlation",
        "stress_loss",
        "unresolved_data_weight",
        "position_count",
    }
)


class PortfolioRiskUnavailableReason(StrEnum):
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    INSUFFICIENT_OVERLAP = "insufficient_overlap"
    ZERO_VARIANCE = "zero_variance"
    INCOMPLETE_HISTORY = "incomplete_history"
    PARTIAL_INPUT = "partial_input"
    STALE_INPUT = "stale_input"
    UNPRICED_POSITION = "unpriced_position"
    MISSING_LIQUIDITY = "missing_liquidity"
    MISSING_CLASSIFICATION = "missing_classification"
    BENCHMARK_MISMATCH = "benchmark_mismatch"
    UNAVAILABLE_VALUATION = "unavailable_valuation"


def exact(value: str | Decimal, name: str, *, nonnegative: bool = False) -> Decimal:
    normalized = decimal_text(value, name, nonnegative=nonnegative)
    assert normalized is not None
    return Decimal(normalized)


def ratio(value: str | Decimal, name: str) -> str:
    parsed = exact(value, name)
    if parsed < 0 or parsed > 1:
        raise FinancialDataValidationError(f"{name} must be between zero and one")
    return decimal_text(parsed, name) or "0"


def _identity(instance: object, field_name: str) -> None:
    def normalize(value: object) -> object:
        if isinstance(value, timedelta):
            return (
                value.days * 86_400_000_000
                + value.seconds * 1_000_000
                + value.microseconds
            )
        if isinstance(value, dict):
            return {str(key): normalize(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(child) for child in value]
        return value

    material = normalize(
        {key: value for key, value in asdict(instance).items() if key != field_name}
    )
    computed = canonical_digest(material)
    supplied = getattr(instance, field_name)
    if supplied and supplied != computed:
        raise FinancialDataValidationError(f"{field_name} mismatch")
    object.__setattr__(instance, field_name, computed)


@dataclass(frozen=True, slots=True)
class PortfolioRiskLimit:
    limit_id: str
    metric: str
    threshold: str
    operator: str
    severity: str
    subject: str | None = None

    def __post_init__(self) -> None:
        identifier(self.limit_id, "limit_id")
        if self.metric not in SUPPORTED_LIMIT_METRICS:
            raise FinancialDataValidationError("risk-limit metric is unsupported")
        object.__setattr__(self, "threshold", decimal_text(self.threshold, "threshold"))
        if self.operator not in SUPPORTED_OPERATORS:
            raise FinancialDataValidationError("risk-limit operator is unsupported")
        if self.severity not in SUPPORTED_SEVERITIES:
            raise FinancialDataValidationError("risk-limit severity is unsupported")
        if self.subject is not None:
            identifier(self.subject, "limit subject")


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    version: str = "sigil-risk-v1"
    currency: str = "USD"
    confidence_level: str = "0.95"
    horizon_days: int = 1
    observations_per_year: int | None = 252
    minimum_acceptable_return: str = "0"
    participation_rate: str = "0.1"
    valuation_tolerance: str = "0.01"
    allowed_future_tolerance: timedelta = timedelta(minutes=5)
    maximum_portfolio_age: timedelta = timedelta(minutes=15)
    maximum_valuation_age: timedelta = timedelta(minutes=15)
    maximum_price_age: timedelta = timedelta(minutes=15)
    maximum_liquidity_age: timedelta = timedelta(days=2)
    maximum_return_observation_age: timedelta = timedelta(days=10)
    maximum_acquisition_duration: timedelta = timedelta(minutes=2)
    minimum_return_observations: int = 3
    minimum_benchmark_observations: int = 3
    minimum_common_observations: int = 2
    minimum_tail_observations: int = 1
    maximum_position_count: int = 1_000
    maximum_return_series_length: int = 10_000
    maximum_scenario_count: int = 100
    require_complete_portfolio: bool = True
    limits: tuple[PortfolioRiskLimit, ...] = ()
    policy_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.version, "policy version")
        if any(
            marker in self.version.lower()
            for marker in ("authorization", "api_key", "access_token", "private_key")
        ):
            raise FinancialDataValidationError("secret-bearing risk policy is forbidden")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("risk-policy currency is unsupported")
        object.__setattr__(
            self, "confidence_level", decimal_text(self.confidence_level, "confidence_level")
        )
        if self.confidence_level not in SUPPORTED_CONFIDENCE_LEVELS:
            raise FinancialDataValidationError("confidence level is unsupported")
        if self.horizon_days not in SUPPORTED_HORIZONS:
            raise FinancialDataValidationError("time horizon is unsupported")
        if self.observations_per_year is not None and not 1 <= self.observations_per_year <= 366:
            raise FinancialDataValidationError("observations_per_year is invalid")
        object.__setattr__(
            self,
            "minimum_acceptable_return",
            decimal_text(
                self.minimum_acceptable_return,
                "minimum_acceptable_return",
                nonnegative=False,
            ),
        )
        object.__setattr__(
            self, "participation_rate", ratio(self.participation_rate, "participation_rate")
        )
        if exact(self.participation_rate, "participation_rate") == 0:
            raise FinancialDataValidationError("participation_rate must be positive")
        object.__setattr__(
            self,
            "valuation_tolerance",
            decimal_text(self.valuation_tolerance, "valuation_tolerance"),
        )
        durations = (
            self.allowed_future_tolerance,
            self.maximum_portfolio_age,
            self.maximum_valuation_age,
            self.maximum_price_age,
            self.maximum_liquidity_age,
            self.maximum_return_observation_age,
            self.maximum_acquisition_duration,
        )
        if any(not isinstance(item, timedelta) or item < timedelta(0) for item in durations):
            raise FinancialDataValidationError("risk-policy duration is invalid")
        counts = (
            self.minimum_return_observations,
            self.minimum_benchmark_observations,
            self.minimum_common_observations,
            self.minimum_tail_observations,
            self.maximum_position_count,
            self.maximum_return_series_length,
            self.maximum_scenario_count,
        )
        if any(isinstance(item, bool) or item < 1 for item in counts):
            raise FinancialDataValidationError("risk-policy sample limit is invalid")
        if self.minimum_return_observations > self.maximum_return_series_length:
            raise FinancialDataValidationError("contradictory return-observation limits")
        ordered = tuple(sorted(self.limits, key=lambda item: item.limit_id))
        if len({item.limit_id for item in ordered}) != len(ordered):
            raise FinancialDataValidationError("duplicate risk-limit identities")
        object.__setattr__(self, "limits", ordered)
        reject_secret_bearing(asdict(self))
        _identity(self, "policy_identity")


@dataclass(frozen=True, slots=True)
class PortfolioRiskProvenance:
    source_kind: str
    source_identity: str
    source_digest: str
    source_timestamp: datetime
    acquired_at: datetime
    completeness: CompletenessStatus
    truncated: bool = False
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    account_binding: str | None = None

    def __post_init__(self) -> None:
        identifier(self.source_kind, "source_kind")
        identifier(self.source_identity, "source_identity")
        digest(self.source_digest, "source_digest")
        timestamp(self.source_timestamp, "source_timestamp")
        timestamp(self.acquired_at, "acquired_at")
        if self.source_timestamp > self.acquired_at:
            raise FinancialDataValidationError("provenance source is after acquisition")
        if self.truncated and self.completeness is CompletenessStatus.COMPLETE:
            raise FinancialDataValidationError("truncated provenance cannot be complete")
        if (self.observation_start is None) != (self.observation_end is None):
            raise FinancialDataValidationError("provenance observation range is incomplete")
        if self.observation_start and self.observation_end:
            timestamp(self.observation_start, "observation_start")
            timestamp(self.observation_end, "observation_end")
            if self.observation_end < self.observation_start:
                raise FinancialDataValidationError("provenance observation range is invalid")
        if self.account_binding is not None:
            identifier(self.account_binding, "account_binding")


@dataclass(frozen=True, slots=True)
class RiskPosition:
    symbol: str
    instrument_type: str
    quantity: str
    price: str | None
    market_value: str | None
    price_timestamp: datetime | None
    stale: bool
    currency: str = "USD"

    def __post_init__(self) -> None:
        identifier(self.symbol, "symbol")
        if self.instrument_type not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("risk instrument is unsupported")
        object.__setattr__(self, "quantity", decimal_text(self.quantity, "quantity"))
        object.__setattr__(self, "price", decimal_text(self.price, "price", optional=True))
        object.__setattr__(
            self, "market_value", decimal_text(self.market_value, "market_value", optional=True)
        )
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("position currency is unsupported")
        if self.price_timestamp is not None:
            timestamp(self.price_timestamp, "price_timestamp")
        if (self.price is None) != (self.market_value is None):
            raise FinancialDataValidationError("price and market value availability mismatch")


@dataclass(frozen=True, slots=True)
class RiskPriceObservation:
    symbol: str
    observed_at: datetime
    price: str
    source_identity: str
    source_digest: str
    acquired_at: datetime
    completeness: CompletenessStatus = CompletenessStatus.COMPLETE

    def __post_init__(self) -> None:
        identifier(self.symbol, "symbol")
        timestamp(self.observed_at, "observed_at")
        timestamp(self.acquired_at, "acquired_at")
        object.__setattr__(self, "price", decimal_text(self.price, "price"))
        identifier(self.source_identity, "source_identity")
        digest(self.source_digest, "source_digest")


@dataclass(frozen=True, slots=True)
class RiskReturnObservation:
    identity: str
    period_start: datetime
    period_end: datetime
    exact_return: str
    source_identity: str
    source_digest: str
    acquired_at: datetime
    completeness: CompletenessStatus = CompletenessStatus.COMPLETE

    def __post_init__(self) -> None:
        identifier(self.identity, "return identity")
        start = timestamp(self.period_start, "period_start")
        end = timestamp(self.period_end, "period_end")
        if end <= start:
            raise FinancialDataValidationError("return period is invalid")
        object.__setattr__(
            self,
            "exact_return",
            decimal_text(self.exact_return, "exact_return", nonnegative=False),
        )
        if exact(self.exact_return, "exact_return") < -1:
            raise FinancialDataValidationError("return cannot be below negative one")
        identifier(self.source_identity, "source_identity")
        digest(self.source_digest, "source_digest")
        timestamp(self.acquired_at, "acquired_at")


@dataclass(frozen=True, slots=True)
class RiskBenchmarkObservation(RiskReturnObservation):
    pass


@dataclass(frozen=True, slots=True)
class RiskClassification:
    symbol: str
    issuer: str | None = None
    sector: str | None = None
    industry: str | None = None
    source_identity: str = "caller-supplied"
    source_digest: str = "0" * 64
    verified: bool = True

    def __post_init__(self) -> None:
        identifier(self.symbol, "symbol")
        for value, name in (
            (self.issuer, "issuer"),
            (self.sector, "sector"),
            (self.industry, "industry"),
        ):
            if value is not None:
                identifier(value, name)
        identifier(self.source_identity, "source_identity")
        digest(self.source_digest, "source_digest")
        if not self.verified:
            raise FinancialDataValidationError("unverified classifications are forbidden")


@dataclass(frozen=True, slots=True)
class RiskLiquidityObservation:
    symbol: str
    observed_at: datetime
    average_daily_volume: str
    average_daily_dollar_volume: str
    source_identity: str
    source_digest: str
    acquired_at: datetime
    bid_price: str | None = None
    ask_price: str | None = None
    spread: str | None = None
    completeness: CompletenessStatus = CompletenessStatus.COMPLETE

    def __post_init__(self) -> None:
        identifier(self.symbol, "symbol")
        timestamp(self.observed_at, "observed_at")
        timestamp(self.acquired_at, "acquired_at")
        for name in ("average_daily_volume", "average_daily_dollar_volume"):
            object.__setattr__(self, name, decimal_text(getattr(self, name), name))
        for name in ("bid_price", "ask_price", "spread"):
            object.__setattr__(self, name, decimal_text(getattr(self, name), name, optional=True))
        identifier(self.source_identity, "source_identity")
        digest(self.source_digest, "source_digest")
        if (
            self.bid_price
            and self.ask_price
            and exact(self.ask_price, "ask") < exact(self.bid_price, "bid")
        ):
            raise FinancialDataValidationError("liquidity quote is crossed")


@dataclass(frozen=True, slots=True)
class PortfolioRiskInput:
    account_binding: str
    snapshot_identity: str
    accounting_state_identity: str
    valuation_identity: str
    as_of: datetime
    cash_value: str
    positions: tuple[RiskPosition, ...]
    portfolio_returns: tuple[RiskReturnObservation, ...]
    asset_returns: tuple[tuple[str, tuple[RiskReturnObservation, ...]], ...]
    benchmark_returns: tuple[RiskBenchmarkObservation, ...]
    classifications: tuple[RiskClassification, ...]
    liquidity: tuple[RiskLiquidityObservation, ...]
    policy: PortfolioRiskPolicy
    provenance: tuple[PortfolioRiskProvenance, ...]
    portfolio_complete: bool
    history_complete: bool
    acquisition_duration_seconds: str
    input_identity: str = ""

    def __post_init__(self) -> None:
        identifier(self.account_binding, "account_binding")
        for value, name in (
            (self.snapshot_identity, "snapshot_identity"),
            (self.accounting_state_identity, "accounting_state_identity"),
            (self.valuation_identity, "valuation_identity"),
        ):
            digest(value, name)
        timestamp(self.as_of, "as_of")
        object.__setattr__(
            self, "cash_value", decimal_text(self.cash_value, "cash_value", nonnegative=False)
        )
        object.__setattr__(
            self,
            "acquisition_duration_seconds",
            decimal_text(self.acquisition_duration_seconds, "acquisition_duration_seconds"),
        )
        positions = tuple(sorted(self.positions, key=lambda item: item.symbol))
        if len(positions) > self.policy.maximum_position_count:
            raise FinancialDataValidationError("position count exceeds policy")
        if len({item.symbol for item in positions}) != len(positions):
            raise FinancialDataValidationError("duplicate risk positions")
        object.__setattr__(self, "positions", positions)
        object.__setattr__(
            self,
            "portfolio_returns",
            validate_returns(self.portfolio_returns, self.policy, self.as_of),
        )
        assets = tuple(sorted(self.asset_returns, key=lambda item: item[0]))
        if len({key for key, _ in assets}) != len(assets):
            raise FinancialDataValidationError("duplicate asset return series")
        object.__setattr__(
            self,
            "asset_returns",
            tuple(
                (key, validate_returns(values, self.policy, self.as_of)) for key, values in assets
            ),
        )
        object.__setattr__(
            self,
            "benchmark_returns",
            validate_returns(self.benchmark_returns, self.policy, self.as_of),
        )
        for name in ("classifications", "liquidity"):
            ordered = tuple(sorted(getattr(self, name), key=lambda item: item.symbol))
            if len({item.symbol for item in ordered}) != len(ordered):
                raise FinancialDataValidationError(f"duplicate {name}")
            object.__setattr__(self, name, ordered)
        object.__setattr__(
            self, "provenance", tuple(sorted(self.provenance, key=lambda item: item.source_kind))
        )
        reject_secret_bearing(asdict(self))
        _identity(self, "input_identity")


def validate_returns(
    values: tuple[RiskReturnObservation, ...],
    policy: PortfolioRiskPolicy,
    now: datetime,
) -> tuple[RiskReturnObservation, ...]:
    if len(values) > policy.maximum_return_series_length:
        raise FinancialDataValidationError("return series exceeds policy")
    ordered = tuple(sorted(values, key=lambda item: (item.period_start, item.period_end)))
    seen: set[tuple[datetime, datetime]] = set()
    previous_end: datetime | None = None
    for item in ordered:
        period = (item.period_start, item.period_end)
        if period in seen:
            raise FinancialDataValidationError("duplicate return period")
        if previous_end is not None and item.period_start < previous_end:
            raise FinancialDataValidationError("overlapping return periods")
        if item.period_end > now + policy.allowed_future_tolerance:
            raise FinancialDataValidationError("future return observation")
        seen.add(period)
        previous_end = item.period_end
    return ordered


@dataclass(frozen=True, slots=True)
class PositionConcentration:
    symbol: str
    market_value: str | None
    weight: str | None
    priced: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class ClassificationConcentration:
    classification_type: str
    classification: str
    market_value: str
    weight: str
    symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioExposureReport:
    total_equity: str | None
    total_position_market_value: str
    cash_value: str
    gross_long_exposure: str
    net_exposure: str
    invested_percentage: str | None
    cash_percentage: str | None
    positions: tuple[PositionConcentration, ...]
    classifications: tuple[ClassificationConcentration, ...]
    top_1_concentration: str | None
    top_3_concentration: str | None
    top_5_concentration: str | None
    concentration_hhi: str | None
    stale_position_weight: str | None
    unpriced_position_weight: str | None
    completeness: CompletenessStatus
    limitations: tuple[str, ...]
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PositionLiquidity:
    symbol: str
    market_value: str | None
    percentage_of_daily_dollar_volume: str | None
    days_to_liquidate: str | None
    missing: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class PortfolioLiquidityReport:
    positions: tuple[PositionLiquidity, ...]
    missing_liquidity_weight: str | None
    stale_liquidity_weight: str | None
    breaching_weight: str | None
    completeness: CompletenessStatus
    limitations: tuple[str, ...]
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PortfolioVolatilityReport:
    arithmetic_mean_return: str | None
    population_volatility: str | None
    sample_volatility: str | None
    annualized_volatility: str | None
    downside_deviation: str | None
    observation_count: int
    period_start: datetime | None
    period_end: datetime | None
    missing_data_count: int
    completeness: CompletenessStatus
    unavailable_reason: PortfolioRiskUnavailableReason | None = None
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class DrawdownPeriod:
    peak_timestamp: datetime
    trough_timestamp: datetime
    recovery_timestamp: datetime | None
    drawdown: str
    duration_days: int


@dataclass(frozen=True, slots=True)
class PortfolioDrawdownReport:
    wealth_path: tuple[tuple[datetime, str], ...]
    maximum_drawdown: str | None
    maximum_drawdown_start: datetime | None
    maximum_drawdown_trough: datetime | None
    recovery_timestamp: datetime | None
    current_drawdown: str | None
    longest_drawdown_duration_days: int | None
    periods: tuple[DrawdownPeriod, ...]
    observation_start: datetime | None
    observation_end: datetime | None
    lifetime_claim: bool
    completeness: CompletenessStatus
    unavailable_reason: PortfolioRiskUnavailableReason | None = None
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        if self.lifetime_claim and self.completeness is not CompletenessStatus.COMPLETE:
            raise FinancialDataValidationError("limited history cannot be lifetime drawdown")
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PairwiseRiskStatistic:
    left: str
    right: str
    covariance: str | None
    correlation: str | None
    overlap_count: int
    period_start: datetime | None
    period_end: datetime | None
    completeness: CompletenessStatus
    unavailable_reason: PortfolioRiskUnavailableReason | None = None


@dataclass(frozen=True, slots=True)
class PortfolioCorrelationMatrix:
    symbols: tuple[str, ...]
    pairs: tuple[PairwiseRiskStatistic, ...]
    completeness: CompletenessStatus
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PortfolioBetaReport:
    benchmark_identity: str
    covariance: str | None
    benchmark_variance: str | None
    beta: str | None
    tracking_error: str | None
    annualized_tracking_error: str | None
    observation_count: int
    observation_start: datetime | None
    observation_end: datetime | None
    completeness: CompletenessStatus
    unavailable_reason: PortfolioRiskUnavailableReason | None = None
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PortfolioValueAtRiskReport:
    confidence_level: str
    horizon_days: int
    method: str
    observation_count: int
    percentile_index: int | None
    loss_percentage: str | None
    loss_amount: str | None
    valuation_identity: str
    observation_identity: str
    completeness: CompletenessStatus
    limitations: tuple[str, ...]
    unavailable_reason: PortfolioRiskUnavailableReason | None = None
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class PortfolioExpectedShortfallReport:
    confidence_level: str
    var_threshold: str | None
    tail_observation_count: int
    mean_tail_loss_percentage: str | None
    mean_tail_loss_amount: str | None
    worst_observed_loss: str | None
    observation_start: datetime | None
    observation_end: datetime | None
    completeness: CompletenessStatus
    unavailable_reason: PortfolioRiskUnavailableReason | None = None
    calculation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "calculation_identity")


@dataclass(frozen=True, slots=True)
class StressShock:
    target_type: str
    target: str
    percentage: str

    def __post_init__(self) -> None:
        if self.target_type not in {
            "portfolio",
            "symbol",
            "issuer",
            "sector",
            "industry",
            "instrument",
            "cash",
        }:
            raise FinancialDataValidationError("stress-shock target is unsupported")
        identifier(self.target, "stress target")
        object.__setattr__(
            self, "percentage", decimal_text(self.percentage, "shock percentage", nonnegative=False)
        )
        if exact(self.percentage, "shock percentage") < -1:
            raise FinancialDataValidationError("stress shock cannot exceed total loss")


@dataclass(frozen=True, slots=True)
class PortfolioStressScenario:
    scenario_id: str
    name: str
    version: str
    description: str
    shocks: tuple[StressShock, ...]
    author_identity: str
    created_at: datetime
    currency: str = "USD"
    require_complete_classification: bool = True
    scenario_digest: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.scenario_id, "scenario_id"),
            (self.name, "scenario name"),
            (self.version, "scenario version"),
            (self.author_identity, "author_identity"),
        ):
            identifier(value, name)
        timestamp(self.created_at, "created_at")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise FinancialDataValidationError("scenario currency is unsupported")
        keys = tuple((item.target_type, item.target) for item in self.shocks)
        if len(set(keys)) != len(keys):
            raise FinancialDataValidationError("duplicate or conflicting stress shocks")
        reject_secret_bearing(asdict(self))
        _identity(self, "scenario_digest")


@dataclass(frozen=True, slots=True)
class PortfolioStressResult:
    scenario_identity: str
    initial_equity: str | None
    shocked_position_values: tuple[tuple[str, str | None], ...]
    shocked_cash: str
    resulting_equity: str | None
    absolute_loss: str | None
    percentage_loss: str | None
    affected_symbols: tuple[str, ...]
    unshocked_unknown_positions: tuple[str, ...]
    missing_classifications: tuple[str, ...]
    completeness: CompletenessStatus
    limit_violation_ids: tuple[str, ...] = ()
    result_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "result_identity")


@dataclass(frozen=True, slots=True)
class PortfolioRiskLimitViolation:
    limit_id: str
    metric: str
    observed_value: str
    threshold: str
    operator: str
    severity: str
    subjects: tuple[str, ...]
    provenance_identity: str
    calculation_identity: str
    explanation_code: str
    violation_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "violation_identity")


@dataclass(frozen=True, slots=True)
class PortfolioRiskReport:
    report_version: str
    policy_identity: str
    account_binding: str
    portfolio_snapshot_identity: str
    accounting_state_identity: str
    valuation_identity: str
    report_timestamp: datetime
    acquisition_duration_seconds: str
    exposure: PortfolioExposureReport
    liquidity: PortfolioLiquidityReport
    volatility: PortfolioVolatilityReport
    drawdown: PortfolioDrawdownReport
    correlation: PortfolioCorrelationMatrix
    beta: PortfolioBetaReport | None
    value_at_risk: PortfolioValueAtRiskReport
    expected_shortfall: PortfolioExpectedShortfallReport
    stress_results: tuple[PortfolioStressResult, ...]
    violations: tuple[PortfolioRiskLimitViolation, ...]
    incomplete_data: tuple[str, ...]
    unavailable_metrics: tuple[str, ...]
    stale_data: tuple[str, ...]
    overall_completeness: CompletenessStatus
    pre_trade_eligible: bool
    provenance: tuple[PortfolioRiskProvenance, ...]
    limitations: tuple[str, ...]
    report_identity: str = ""

    def __post_init__(self) -> None:
        _identity(self, "report_identity")


@dataclass(frozen=True, slots=True)
class ProposedTradeRiskInput:
    account_binding: str
    symbol: str
    instrument_type: str
    side: str
    quantity: str | None
    notional: str | None
    proposed_price: str
    price_timestamp: datetime
    estimated_fees: str
    proposal_identity: str
    simulation_timestamp: datetime
    source_approval_reference: str | None = None

    def __post_init__(self) -> None:
        identifier(self.account_binding, "account_binding")
        identifier(self.symbol, "symbol")
        if self.instrument_type not in SUPPORTED_INSTRUMENTS:
            raise FinancialDataValidationError("proposal instrument is unsupported")
        if self.side not in {"BUY", "SELL"}:
            raise FinancialDataValidationError("proposal side is unsupported")
        if (self.quantity is None) == (self.notional is None):
            raise FinancialDataValidationError("exactly one of quantity or notional is required")
        object.__setattr__(
            self, "quantity", decimal_text(self.quantity, "quantity", positive=True, optional=True)
        )
        object.__setattr__(
            self, "notional", decimal_text(self.notional, "notional", positive=True, optional=True)
        )
        object.__setattr__(
            self,
            "proposed_price",
            decimal_text(self.proposed_price, "proposed_price", positive=True),
        )
        object.__setattr__(
            self, "estimated_fees", decimal_text(self.estimated_fees, "estimated_fees")
        )
        timestamp(self.price_timestamp, "price_timestamp")
        timestamp(self.simulation_timestamp, "simulation_timestamp")
        identifier(self.proposal_identity, "proposal_identity")
        if self.source_approval_reference is not None:
            identifier(self.source_approval_reference, "source_approval_reference")


@dataclass(frozen=True, slots=True)
class ProposedTradeRiskComparison:
    proposal_identity: str
    pre_trade_report_identity: str
    projected_report_identity: str | None
    pre_trade_cash: str
    post_trade_cash: str | None
    post_trade_positions: tuple[RiskPosition, ...]
    post_trade_violations: tuple[PortfolioRiskLimitViolation, ...]
    newly_introduced_violations: tuple[str, ...]
    resolved_violations: tuple[str, ...]
    worsened_violations: tuple[str, ...]
    improved_metrics: tuple[str, ...]
    risk_eligible: bool
    trade_approved: bool
    ineligibility_reasons: tuple[str, ...]
    comparison_identity: str = ""

    def __post_init__(self) -> None:
        if self.trade_approved:
            raise FinancialDataValidationError("risk comparison cannot approve a trade")
        _identity(self, "comparison_identity")
