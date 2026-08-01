"""Governed local-only Kronos financial forecasting contracts and provider."""

from __future__ import annotations

import importlib.util
import math
import os
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
from itertools import pairwise
from pathlib import Path
from typing import Protocol

from .evidence import build_invocation_evidence
from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .provider import ProviderFailure, ProviderFailureClass, ProviderInvocation, ProviderResult
from .registry import canonical_digest

KRONOS_SCHEMA_VERSION = 1
KRONOS_PROVIDER_ID = "local-kronos"
DEFAULT_KRONOS_MODEL = "NeoQuasar/Kronos-small"
DEFAULT_KRONOS_TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"
MAX_SEQUENCE_LENGTH = 2_048
MAX_FORECAST_HORIZON = 256
MAX_FORECAST_BATCH_SIZE = 8
MAX_FORECAST_POINTS = 256
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,15}$")
_SENSITIVE = ("api_key", "api-key", "authorization:", "bearer ", "password=", "secret=", "token=")
_EXECUTABLE = ("#!/bin/", "subprocess.", "os.system(", "eval(", "exec(")
_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3_600, "1d": 86_400}


class KronosValidationError(ValueError):
    """Kronos configuration, input, or output failed closed."""


class UncertaintyMode(str, Enum):
    NONE = "none"
    QUANTILES = "quantiles"


KRONOS_RESPONSIBILITIES = frozenset(
    {
        Responsibility.FORECASTING,
        Responsibility.MARKET_FORECASTING,
        Responsibility.PROPOSAL_SUPPORT,
        Responsibility.RISK_ANALYSIS,
        Responsibility.MARKET_CONTEXT,
        Responsibility.SCENARIO_ANALYSIS,
        Responsibility.ORCHESTRATION_SUPPORT,
        Responsibility.RESEARCH_ANALYSIS,
    }
)


def _environment_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None:
        return default
    if raw.strip().lower() in {"1", "true", "yes"}:
        return True
    if raw.strip().lower() in {"0", "false", "no"}:
        return False
    raise KronosValidationError(f"{name} must be a boolean")


def _environment_int(
    environment: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    try:
        value = default if name not in environment else int(environment[name])
    except ValueError as error:
        raise KronosValidationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise KronosValidationError(f"{name} is outside its governed bound")
    return value


def _safe_source(value: str, name: str) -> None:
    if not value.strip() or len(value) > 1_024:
        raise KronosValidationError(f"{name} is invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in _SENSITIVE):
        raise KronosValidationError(f"{name} contains credential material")


def _path_free_identity(source: str, default: str, prefix: str) -> str:
    if source == default:
        return default.lower().replace("/", ".")
    return f"{prefix}-local-{canonical_digest(source)[:16]}"


@dataclass(frozen=True, slots=True)
class KronosConfig:
    enabled: bool = False
    model: str = DEFAULT_KRONOS_MODEL
    model_version: str = "local-unverified"
    tokenizer: str = DEFAULT_KRONOS_TOKENIZER
    tokenizer_version: str = "local-unverified"
    device: str = "cpu"
    timeout_ms: int = 30_000
    max_sequence_length: int = 512
    min_sequence_length: int = 32
    max_horizon: int = 64
    local_files_only: bool = True
    allowed_intervals: tuple[str, ...] = ("1d", "1h")
    max_batch_size: int = 1

    def __post_init__(self) -> None:
        _safe_source(self.model, "Kronos model source")
        _safe_source(self.tokenizer, "Kronos tokenizer source")
        validate_identifier(self.model_version, "Kronos model_version")
        validate_identifier(self.tokenizer_version, "Kronos tokenizer_version")
        if self.device not in {"cpu", "mps", "cuda"}:
            raise KronosValidationError("Kronos device is unsupported")
        if not 100 <= self.timeout_ms <= 300_000:
            raise KronosValidationError("Kronos timeout is outside its governed bound")
        if not 8 <= self.min_sequence_length <= self.max_sequence_length <= MAX_SEQUENCE_LENGTH:
            raise KronosValidationError("Kronos sequence bounds are invalid")
        if not 1 <= self.max_horizon <= MAX_FORECAST_HORIZON:
            raise KronosValidationError("Kronos horizon bound is invalid")
        if not self.allowed_intervals or any(
            item not in _INTERVAL_SECONDS for item in self.allowed_intervals
        ):
            raise KronosValidationError("Kronos interval configuration is invalid")
        if tuple(sorted(set(self.allowed_intervals))) != self.allowed_intervals:
            raise KronosValidationError("Kronos intervals must be unique and sorted")
        if not 1 <= self.max_batch_size <= MAX_FORECAST_BATCH_SIZE:
            raise KronosValidationError("Kronos batch bound is invalid")
        if self.local_files_only is not True:
            raise KronosValidationError("Kronos must use local files only")

    @property
    def model_id(self) -> str:
        return _path_free_identity(self.model, DEFAULT_KRONOS_MODEL, "kronos")

    @property
    def tokenizer_id(self) -> str:
        return _path_free_identity(self.tokenizer, DEFAULT_KRONOS_TOKENIZER, "kronos-tokenizer")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> KronosConfig:
        source = os.environ if environment is None else environment
        intervals = tuple(
            sorted(
                {
                    item.strip().lower()
                    for item in source.get("SIGIL_AI_KRONOS_ALLOWED_INTERVALS", "1h,1d").split(",")
                    if item.strip()
                }
            )
        )
        return cls(
            enabled=_environment_bool(source, "SIGIL_AI_KRONOS_ENABLED", False),
            model=source.get("SIGIL_AI_KRONOS_MODEL", DEFAULT_KRONOS_MODEL),
            model_version=source.get("SIGIL_AI_KRONOS_MODEL_VERSION", "local-unverified"),
            tokenizer=source.get("SIGIL_AI_KRONOS_TOKENIZER", DEFAULT_KRONOS_TOKENIZER),
            tokenizer_version=source.get("SIGIL_AI_KRONOS_TOKENIZER_VERSION", "local-unverified"),
            device=source.get("SIGIL_AI_KRONOS_DEVICE", "cpu"),
            timeout_ms=_environment_int(source, "SIGIL_AI_KRONOS_TIMEOUT_MS", 30_000, 100, 300_000),
            max_sequence_length=_environment_int(
                source, "SIGIL_AI_KRONOS_MAX_SEQUENCE_LENGTH", 512, 8, MAX_SEQUENCE_LENGTH
            ),
            min_sequence_length=_environment_int(
                source, "SIGIL_AI_KRONOS_MIN_SEQUENCE_LENGTH", 32, 8, MAX_SEQUENCE_LENGTH
            ),
            max_horizon=_environment_int(
                source, "SIGIL_AI_KRONOS_MAX_HORIZON", 64, 1, MAX_FORECAST_HORIZON
            ),
            local_files_only=_environment_bool(source, "SIGIL_AI_KRONOS_LOCAL_FILES_ONLY", True),
            allowed_intervals=intervals,
            max_batch_size=_environment_int(
                source, "SIGIL_AI_KRONOS_MAX_BATCH_SIZE", 1, 1, MAX_FORECAST_BATCH_SIZE
            ),
        )


def _timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise KronosValidationError(f"{name} is invalid") from error
    if parsed.tzinfo is None:
        raise KronosValidationError(f"{name} requires a timezone")
    return parsed


def _number(value: object, name: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise KronosValidationError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise KronosValidationError(f"{name} is outside its valid range")
    return number


@dataclass(frozen=True, slots=True)
class GovernedMarketBar:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        _timestamp(self.timestamp, "market bar timestamp")
        values = tuple(
            _number(value, name)
            for name, value in (
                ("open", self.open),
                ("high", self.high),
                ("low", self.low),
                ("close", self.close),
            )
        )
        volume = _number(self.volume, "volume", positive=False)
        if volume < 0 or values[1] < max(values) or values[2] > min(values):
            raise KronosValidationError("market bar OHLCV relationship is invalid")


def market_series_digest(bars: Sequence[GovernedMarketBar]) -> str:
    return f"sha256:{canonical_digest([asdict(item) for item in bars])}"


@dataclass(frozen=True, slots=True)
class GovernedMarketSeries:
    series_id: str
    source_identity: str
    source_digest: str
    symbol: str
    asset_class: str
    venue: str | None
    interval: str
    timezone: str
    start_at: str
    end_at: str
    observed_at: str
    stale_after: str | None
    bars: tuple[GovernedMarketBar, ...]
    fields: tuple[str, ...] = ("open", "high", "low", "close", "volume")
    source_trust: TrustTier = TrustTier.TRUSTED
    privacy_classification: PrivacyTier = PrivacyTier.LOCAL_ONLY
    adjusted: bool = False
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    @property
    def bar_count(self) -> int:
        return len(self.bars)

    def is_stale(self, at: str) -> bool:
        return self.stale_after is not None and _timestamp(
            self.stale_after, "stale_after"
        ) < _timestamp(at, "freshness comparison")

    def __post_init__(self) -> None:
        if self.schema_version != KRONOS_SCHEMA_VERSION:
            raise KronosValidationError("unsupported market-series schema")
        validate_identifier(self.series_id, "series_id")
        if (
            _SAFE_REFERENCE.fullmatch(self.source_identity) is None
            or _SYMBOL.fullmatch(self.symbol) is None
        ):
            raise KronosValidationError("market-series source or symbol is invalid")
        validate_identifier(self.asset_class, "asset_class")
        if self.venue is not None and _SAFE_REFERENCE.fullmatch(self.venue) is None:
            raise KronosValidationError("market-series venue is invalid")
        if self.interval not in _INTERVAL_SECONDS or not self.timezone.strip():
            raise KronosValidationError("market-series interval or timezone is invalid")
        start, end, observed = (
            _timestamp(item, name)
            for item, name in (
                (self.start_at, "start_at"),
                (self.end_at, "end_at"),
                (self.observed_at, "observed_at"),
            )
        )
        if start > end or observed < end:
            raise KronosValidationError("market-series time range is invalid")
        if self.stale_after is not None and _timestamp(self.stale_after, "stale_after") < observed:
            raise KronosValidationError("market-series freshness is invalid")
        if not self.bars or len(self.bars) > MAX_SEQUENCE_LENGTH:
            raise KronosValidationError("market-series length is invalid")
        times = tuple(_timestamp(item.timestamp, "bar timestamp") for item in self.bars)
        if any(left >= right for left, right in pairwise(times)):
            raise KronosValidationError("market-series timestamps must be strictly ordered")
        if times[0] != start or times[-1] != end:
            raise KronosValidationError("market-series bounds do not match bars")
        if self.fields != ("open", "high", "low", "close", "volume"):
            raise KronosValidationError("market-series fields are unsupported")
        if self.source_digest != market_series_digest(self.bars):
            raise KronosValidationError("market-series source digest mismatch")
        lowered = f"{self.source_identity} {self.symbol} {self.venue or ''}".lower()
        if any(marker in lowered for marker in (*_SENSITIVE, *_EXECUTABLE)):
            raise KronosValidationError("market-series contains unsafe metadata")
        if self.paper_only is not True or self.broker_submission is not False:
            raise KronosValidationError("market-series cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class GovernedForecastRequest:
    request_id: str
    task_correlation_id: str
    responsibility: Responsibility
    series_id: str
    series_digest: str
    symbol: str
    interval: str
    forecast_horizon: int
    uncertainty_mode: UncertaintyMode
    requested_quantiles: tuple[float, ...]
    privacy_requirement: PrivacyTier
    minimum_trust_tier: TrustTier
    fallback_permission: bool
    timeout_ms: int
    requested_at: str
    evidence_context_digests: tuple[str, ...]
    require_fresh: bool = True
    capability: Capability = Capability.TIME_SERIES_FORECASTING
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema_version != KRONOS_SCHEMA_VERSION
            or self.capability != Capability.TIME_SERIES_FORECASTING
        ):
            raise KronosValidationError("forecast request schema or capability is invalid")
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        validate_identifier(self.series_id, "series_id")
        if (
            self.responsibility not in KRONOS_RESPONSIBILITIES
            or self.responsibility in PROHIBITED_RESPONSIBILITIES
        ):
            raise KronosValidationError("forecast responsibility is not advisory")
        if _SHA256.fullmatch(self.series_digest) is None or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_context_digests
        ):
            raise KronosValidationError("forecast digest references are invalid")
        if not self.evidence_context_digests or len(self.evidence_context_digests) > 64:
            raise KronosValidationError("forecast evidence context is invalid")
        if _SYMBOL.fullmatch(self.symbol) is None or self.interval not in _INTERVAL_SECONDS:
            raise KronosValidationError("forecast symbol or interval is invalid")
        if not 1 <= self.forecast_horizon <= MAX_FORECAST_HORIZON:
            raise KronosValidationError("forecast horizon is invalid")
        if not isinstance(self.uncertainty_mode, UncertaintyMode):
            raise KronosValidationError("forecast uncertainty mode is invalid")
        if self.uncertainty_mode == UncertaintyMode.NONE and self.requested_quantiles:
            raise KronosValidationError("point forecasts cannot request quantiles")
        if self.uncertainty_mode == UncertaintyMode.QUANTILES and self.requested_quantiles != (
            0.1,
            0.5,
            0.9,
        ):
            raise KronosValidationError("Kronos supports only governed 0.1/0.5/0.9 quantiles")
        if not 100 <= self.timeout_ms <= 300_000:
            raise KronosValidationError("forecast timeout is invalid")
        _timestamp(self.requested_at, "requested_at")
        if self.paper_only is not True or self.broker_submission is not False:
            raise KronosValidationError("forecast request cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    horizon_index: int
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    lower_close: float | None = None
    upper_close: float | None = None

    def __post_init__(self) -> None:
        if self.horizon_index < 1:
            raise KronosValidationError("forecast horizon index is invalid")
        _timestamp(self.timestamp, "forecast timestamp")
        values = tuple(
            _number(value, name)
            for name, value in (
                ("open", self.open),
                ("high", self.high),
                ("low", self.low),
                ("close", self.close),
            )
        )
        if values[1] < max(values) or values[2] > min(values):
            raise KronosValidationError("forecast OHLC relationship is invalid")
        if self.volume is not None and _number(self.volume, "forecast volume", positive=False) < 0:
            raise KronosValidationError("forecast volume is invalid")
        if (self.lower_close is None) != (self.upper_close is None):
            raise KronosValidationError("forecast uncertainty is incomplete")
        if self.lower_close is not None:
            lower = _number(self.lower_close, "lower close")
            upper = _number(self.upper_close, "upper close")
            if not lower <= self.close <= upper:
                raise KronosValidationError("forecast uncertainty ordering is invalid")


@dataclass(frozen=True, slots=True)
class KronosForecastPayload:
    request_id: str
    series_id: str
    symbol: str
    interval: str
    provider_id: str
    model_id: str
    model_version: str
    tokenizer_id: str
    tokenizer_version: str
    forecast_horizon: int
    generated_at: str
    forecast_points: tuple[ForecastPoint, ...]
    uncertainty_mode: UncertaintyMode
    calibration: str
    source_digest: str
    freshness_state: str
    limitations: tuple[str, ...]
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False


def validate_kronos_output(
    output: object,
    *,
    request: GovernedForecastRequest,
    series: GovernedMarketSeries,
    provider_id: str,
    model_id: str,
    model_version: str,
    tokenizer_id: str,
    tokenizer_version: str,
) -> KronosForecastPayload:
    if not isinstance(output, Mapping):
        raise KronosValidationError("Kronos output must be an object")
    required = {
        "schema_version",
        "request_id",
        "series_id",
        "symbol",
        "interval",
        "provider_id",
        "model_id",
        "model_version",
        "tokenizer_id",
        "tokenizer_version",
        "forecast_horizon",
        "generated_at",
        "forecast_points",
        "uncertainty_mode",
        "calibration",
        "source_digest",
        "freshness_state",
        "limitations",
        "paper_only",
        "execution_authorized",
        "broker_submission",
        "portfolio_mutation",
        "approval_authority",
    }
    if set(output) != required or output.get("schema_version") != KRONOS_SCHEMA_VERSION:
        raise KronosValidationError("Kronos output schema is invalid")
    identities = (
        ("request_id", request.request_id),
        ("series_id", series.series_id),
        ("symbol", series.symbol),
        ("interval", series.interval),
        ("provider_id", provider_id),
        ("model_id", model_id),
        ("model_version", model_version),
        ("tokenizer_id", tokenizer_id),
        ("tokenizer_version", tokenizer_version),
        ("source_digest", series.source_digest),
    )
    if any(output.get(name) != expected for name, expected in identities):
        raise KronosValidationError("Kronos output identity mismatch")
    if output.get("forecast_horizon") != request.forecast_horizon:
        raise KronosValidationError("Kronos forecast horizon mismatch")
    try:
        uncertainty = UncertaintyMode(output["uncertainty_mode"])
    except (TypeError, ValueError) as error:
        raise KronosValidationError("Kronos uncertainty is invalid") from error
    if uncertainty != request.uncertainty_mode:
        raise KronosValidationError("Kronos uncertainty mode mismatch")
    raw_points = output["forecast_points"]
    if (
        not isinstance(raw_points, Sequence)
        or isinstance(raw_points, (str, bytes))
        or len(raw_points) != request.forecast_horizon
        or len(raw_points) > MAX_FORECAST_POINTS
    ):
        raise KronosValidationError("Kronos forecast length is invalid")
    try:
        points = tuple(ForecastPoint(**item) for item in raw_points)
    except (TypeError, ValueError) as error:
        raise KronosValidationError("Kronos forecast point is malformed") from error
    if tuple(item.horizon_index for item in points) != tuple(range(1, len(points) + 1)):
        raise KronosValidationError("Kronos horizon indexes are invalid")
    has_bands = tuple(
        item.lower_close is not None and item.upper_close is not None for item in points
    )
    if uncertainty == UncertaintyMode.QUANTILES and not all(has_bands):
        raise KronosValidationError("Kronos quantile output omitted uncertainty bands")
    if uncertainty == UncertaintyMode.NONE and any(has_bands):
        raise KronosValidationError("Kronos point output included unsupported uncertainty")
    step = timedelta(seconds=_INTERVAL_SECONDS[series.interval])
    prior = _timestamp(series.end_at, "series end")
    for point in points:
        current = _timestamp(point.timestamp, "forecast timestamp")
        if current != prior + step:
            raise KronosValidationError("Kronos forecast timestamps are discontinuous")
        prior = current
    authority = {
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "approval_authority": False,
    }
    if any(output.get(name) is not expected for name, expected in authority.items()):
        raise KronosValidationError("Kronos output cannot carry execution authority")
    limitations = output["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or len(limitations) > 16
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 512 for item in limitations
        )
    ):
        raise KronosValidationError("Kronos limitations are invalid")
    calibration = output["calibration"]
    freshness = output["freshness_state"]
    if (
        not isinstance(calibration, str)
        or calibration not in {"unavailable", "runtime-reported"}
        or freshness not in {"current", "stale"}
    ):
        raise KronosValidationError("Kronos calibration or freshness is invalid")
    _timestamp(str(output["generated_at"]), "generated_at")
    serialized = str(output).lower()
    if any(
        marker in serialized
        for marker in (*_SENSITIVE, *_EXECUTABLE, "submit_order", "buy ", "sell ")
    ):
        raise KronosValidationError("Kronos output contains prohibited content")
    return KronosForecastPayload(
        **{
            **output,
            "forecast_points": points,
            "uncertainty_mode": uncertainty,
            "limitations": tuple(limitations),
        }
    )


@dataclass(frozen=True, slots=True)
class GovernedForecastArtifact:
    artifact_id: str
    request_id: str
    task_correlation_id: str
    series_id: str
    series_digest: str
    symbol: str
    interval: str
    provider_id: str
    model_id: str
    model_version: str
    tokenizer_id: str
    tokenizer_version: str
    forecast_horizon: int
    structured_payload: KronosForecastPayload
    source_trust: TrustTier
    freshness_state: str
    limitations: tuple[str, ...]
    routing_evidence_id: str
    invocation_evidence_id: str
    input_digest: str
    output_digest: str
    created_at: str
    stale_after: str | None
    responsibility: Responsibility
    capability: Capability = Capability.TIME_SERIES_FORECASTING
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    @property
    def confidence(self) -> None:
        return None

    def __post_init__(self) -> None:
        if (
            not self.artifact_id.startswith("analysis-artifact-")
            or self.capability != Capability.TIME_SERIES_FORECASTING
        ):
            raise KronosValidationError("forecast artifact identity or capability is invalid")
        if any(
            _SHA256.fullmatch(item) is None
            for item in (
                self.series_digest,
                self.routing_evidence_id,
                self.invocation_evidence_id,
                self.input_digest,
                self.output_digest,
            )
        ):
            raise KronosValidationError("forecast artifact digest is invalid")
        if self.forecast_horizon != len(self.structured_payload.forecast_points):
            raise KronosValidationError("forecast artifact horizon is invalid")
        if self.paper_only is not True or any(
            (
                self.execution_authorized,
                self.broker_submission,
                self.portfolio_mutation,
                self.approval_authority,
            )
        ):
            raise KronosValidationError("forecast artifact cannot carry execution authority")


def build_forecast_artifact(**values: object) -> GovernedForecastArtifact:
    identity = {**values, "artifact_id": "pending", "schema_version": KRONOS_SCHEMA_VERSION}
    structured_payload = identity.get("structured_payload")
    if not isinstance(structured_payload, KronosForecastPayload):
        raise KronosValidationError("forecast artifact payload is invalid")
    digest_payload = {
        **identity,
        "structured_payload": asdict(structured_payload),
    }
    return GovernedForecastArtifact(
        **{**identity, "artifact_id": f"analysis-artifact-{canonical_digest(digest_payload)}"}
    )


@dataclass(frozen=True, slots=True)
class HorizonMetric:
    horizon_index: int
    mae: float
    rmse: float
    directional_accuracy: float | None
    interval_coverage: float | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class GovernedForecastEvaluationArtifact:
    artifact_id: str
    request_id: str
    task_correlation_id: str
    forecast_artifact_id: str
    observed_series_id: str
    observed_series_digest: str
    evaluation_start_at: str
    evaluation_end_at: str
    mae: float
    rmse: float
    mape: float | None
    directional_accuracy: float | None
    interval_coverage: float | None
    horizon_metrics: tuple[HorizonMetric, ...]
    sample_count: int
    limitations: tuple[str, ...]
    created_at: str
    responsibility: Responsibility = Responsibility.RESEARCH_ANALYSIS
    capability: Capability = Capability.TIME_SERIES_FORECASTING
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    @property
    def confidence(self) -> None:
        return None

    def __post_init__(self) -> None:
        if not self.artifact_id.startswith("evaluation-artifact-") or self.sample_count < 1:
            raise KronosValidationError("forecast evaluation identity or count is invalid")
        for metric in (self.mae, self.rmse):
            if not math.isfinite(metric) or metric < 0:
                raise KronosValidationError("forecast evaluation metric is invalid")
        if self.paper_only is not True or any(
            (
                self.execution_authorized,
                self.broker_submission,
                self.portfolio_mutation,
                self.approval_authority,
            )
        ):
            raise KronosValidationError("forecast evaluation cannot carry execution authority")


@dataclass(frozen=True, slots=True)
class GovernedForecastComparison:
    comparison_id: str
    observed_series_id: str
    evaluation_artifact_ids: tuple[str, ...]
    metrics: tuple[tuple[str, float, float, float | None], ...]
    limitations: tuple[str, ...]
    compared_at: str
    schema_version: int = KRONOS_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    def __post_init__(self) -> None:
        if (
            not self.comparison_id.startswith("forecast-comparison-")
            or not 2 <= len(self.evaluation_artifact_ids) <= 8
            or len(set(self.evaluation_artifact_ids)) != len(self.evaluation_artifact_ids)
        ):
            raise KronosValidationError("forecast comparison identity or bounds are invalid")
        if self.paper_only is not True or any(
            (
                self.execution_authorized,
                self.broker_submission,
                self.portfolio_mutation,
                self.approval_authority,
            )
        ):
            raise KronosValidationError("forecast comparison cannot carry execution authority")


def compare_forecast_evaluations(
    evaluations: Sequence[GovernedForecastEvaluationArtifact], *, compared_at: str
) -> GovernedForecastComparison:
    if not 2 <= len(evaluations) <= 8:
        raise KronosValidationError("forecast comparison requires two to eight evaluations")
    observed = {item.observed_series_id for item in evaluations}
    identities = {item.artifact_id for item in evaluations}
    if len(observed) != 1 or len(identities) != len(evaluations):
        raise KronosValidationError("forecast comparison identities are incompatible")
    ordered = tuple(sorted(evaluations, key=lambda item: item.artifact_id))
    values = {
        "observed_series_id": ordered[0].observed_series_id,
        "evaluation_artifact_ids": tuple(item.artifact_id for item in ordered),
        "metrics": tuple(
            (item.artifact_id, item.mae, item.rmse, item.interval_coverage) for item in ordered
        ),
        "limitations": (
            "Comparison is observational and does not select or promote a model.",
            "Forecast error does not establish trading profitability.",
        ),
        "compared_at": compared_at,
    }
    return GovernedForecastComparison(
        comparison_id=f"forecast-comparison-{canonical_digest(values)}", **values
    )


def evaluate_forecast(
    forecast: GovernedForecastArtifact,
    observed: GovernedMarketSeries,
    *,
    request_id: str,
    task_correlation_id: str,
    evaluated_at: str,
) -> GovernedForecastEvaluationArtifact:
    validate_identifier(request_id, "request_id")
    validate_identifier(task_correlation_id, "task_correlation_id")
    if forecast.symbol != observed.symbol or forecast.interval != observed.interval:
        raise KronosValidationError("forecast evaluation series identity mismatch")
    observed_by_time = {item.timestamp: item for item in observed.bars}
    pairs = tuple(
        (point, observed_by_time[point.timestamp])
        for point in forecast.structured_payload.forecast_points
        if point.timestamp in observed_by_time
    )
    if not pairs:
        raise KronosValidationError("forecast evaluation has insufficient observations")
    errors = tuple(abs(point.close - bar.close) for point, bar in pairs)
    squared = tuple((point.close - bar.close) ** 2 for point, bar in pairs)
    prior = forecast.structured_payload.forecast_points[0].open
    directions = tuple((point.close >= prior) == (bar.close >= prior) for point, bar in pairs)
    covered = tuple(
        point.lower_close <= bar.close <= point.upper_close
        for point, bar in pairs
        if point.lower_close is not None and point.upper_close is not None
    )
    mape_values = tuple(
        abs(point.close - bar.close) / abs(bar.close) for point, bar in pairs if bar.close != 0
    )
    metrics = tuple(
        HorizonMetric(
            point.horizon_index,
            error,
            math.sqrt(square),
            float(direction),
            None
            if point.lower_close is None
            else float(point.lower_close <= bar.close <= point.upper_close),
            1,
        )
        for (point, bar), error, square, direction in zip(
            pairs, errors, squared, directions, strict=True
        )
    )
    values = {
        "request_id": request_id,
        "task_correlation_id": task_correlation_id,
        "forecast_artifact_id": forecast.artifact_id,
        "observed_series_id": observed.series_id,
        "observed_series_digest": observed.source_digest,
        "evaluation_start_at": pairs[0][1].timestamp,
        "evaluation_end_at": pairs[-1][1].timestamp,
        "mae": sum(errors) / len(errors),
        "rmse": math.sqrt(sum(squared) / len(squared)),
        "mape": None if len(mape_values) != len(pairs) else sum(mape_values) / len(mape_values),
        "directional_accuracy": sum(directions) / len(directions),
        "interval_coverage": None if not covered else sum(covered) / len(covered),
        "horizon_metrics": metrics,
        "sample_count": len(pairs),
        "limitations": (
            "Forecast error is observational and does not imply trading profitability.",
            "Evaluation cannot promote models, strategies, or routing priority.",
        ),
        "created_at": evaluated_at,
    }
    digest_payload = {**values, "horizon_metrics": [asdict(item) for item in metrics]}
    return GovernedForecastEvaluationArtifact(
        artifact_id=f"evaluation-artifact-{canonical_digest(digest_payload)}", **values
    )


class KronosRuntime(Protocol):
    def forecast(
        self, *, bars: Sequence[Mapping[str, object]], horizon: int, uncertainty_mode: str
    ) -> Sequence[Mapping[str, object]]: ...


class LocalKronosRuntime:
    """Optional Kronos runtime. Imports and local weight reads occur only on invocation."""

    def __init__(self, config: KronosConfig) -> None:
        from kronos import Kronos, KronosTokenizer  # type: ignore[import-not-found]

        self._tokenizer = KronosTokenizer.from_pretrained(config.tokenizer, local_files_only=True)
        self._model = Kronos.from_pretrained(
            config.model, local_files_only=True, device=config.device
        )

    def forecast(
        self, *, bars: Sequence[Mapping[str, object]], horizon: int, uncertainty_mode: str
    ) -> Sequence[Mapping[str, object]]:
        return self._model.predict(
            self._tokenizer, list(bars), horizon=horizon, uncertainty_mode=uncertainty_mode
        )


class LocalKronosProvider:
    input_contract = "application/json;schema=sigil.ai.input.market-series-forecast.v1"
    output_contract = "application/json;schema=sigil.ai.output.time-series-forecast.v1"
    capabilities = frozenset({Capability.TIME_SERIES_FORECASTING})
    model_family = "kronos"

    def __init__(self, config: KronosConfig, runtime: KronosRuntime | None = None) -> None:
        self.config = config
        self.model_id = config.model_id
        self.model_version = config.model_version
        self.tokenizer_id = config.tokenizer_id
        self.tokenizer_version = config.tokenizer_version
        self.request_timeout_ms = config.timeout_ms
        self._runtime = runtime
        dependencies = (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("kronos") is not None
        )
        local_sources = all(
            source == default or Path(source).exists()
            for source, default in (
                (config.model, DEFAULT_KRONOS_MODEL),
                (config.tokenizer, DEFAULT_KRONOS_TOKENIZER),
            )
        )
        healthy = config.enabled and local_sources and (runtime is not None or dependencies)
        self.identity = ProviderIdentity(
            KRONOS_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            ProviderHealth.HEALTHY if healthy else ProviderHealth.UNAVAILABLE,
            config.enabled,
            (("runtime", "local-files-only"),),
        )

    def registration(self) -> ModelRegistration:
        return ModelRegistration(
            model_id=self.model_id,
            provider_id=self.identity.provider_id,
            family=self.model_family,
            version=self.model_version,
            capabilities=self.capabilities,
            execution_location=ExecutionLocation.LOCAL,
            context_limit=self.config.max_sequence_length,
            supported_input_types=frozenset({InputType.TIME_SERIES}),
            structured_output=True,
            cost_class=CostClass.FREE,
            trust_tier=TrustTier.TRUSTED,
            privacy_tier=PrivacyTier.LOCAL_ONLY,
            health=self.identity.health,
            enabled=self.config.enabled,
            allowed_responsibilities=KRONOS_RESPONSIBILITIES,
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        failure: ProviderFailure | None = None
        output: Mapping[str, object] | None = None
        payload = invocation.input_payload
        bars = payload.get("bars")
        horizon = payload.get("forecast_horizon")
        if not self.config.enabled or self.identity.health != ProviderHealth.HEALTHY:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE,
                "Local Kronos model or tokenizer is unavailable.",
                True,
            )
        elif invocation.model_id != self.model_id or payload.get("model_id") != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Kronos model identity mismatch.",
                False,
            )
        elif (
            payload.get("tokenizer_id") != self.tokenizer_id
            or payload.get("tokenizer_version") != self.tokenizer_version
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "Kronos tokenizer identity mismatch.",
                False,
            )
        elif invocation.capability != Capability.TIME_SERIES_FORECASTING:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH, "Kronos capability mismatch.", False
            )
        elif (
            payload.get("interval") not in self.config.allowed_intervals
            or not isinstance(horizon, int)
            or not 1 <= horizon <= self.config.max_horizon
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "Kronos interval or horizon is unsupported.",
                False,
            )
        elif (
            not isinstance(bars, list)
            or not self.config.min_sequence_length <= len(bars) <= self.config.max_sequence_length
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT, "Kronos sequence length is invalid.", False
            )
        else:
            try:
                runtime = self._runtime or LocalKronosRuntime(self.config)
                self._runtime = runtime
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(
                        runtime.forecast,
                        bars=bars,
                        horizon=horizon,
                        uncertainty_mode=str(payload.get("uncertainty_mode")),
                    )
                    points = future.result(timeout=invocation.timeout_ms / 1_000)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                output = {
                    "schema_version": KRONOS_SCHEMA_VERSION,
                    "request_id": invocation.request_id,
                    "series_id": payload["series_id"],
                    "symbol": payload["symbol"],
                    "interval": payload["interval"],
                    "provider_id": self.identity.provider_id,
                    "model_id": self.model_id,
                    "model_version": self.model_version,
                    "tokenizer_id": self.tokenizer_id,
                    "tokenizer_version": self.tokenizer_version,
                    "forecast_horizon": horizon,
                    "generated_at": invocation.ended_at,
                    "forecast_points": list(points),
                    "uncertainty_mode": payload["uncertainty_mode"],
                    "calibration": "unavailable"
                    if payload["uncertainty_mode"] == UncertaintyMode.NONE.value
                    else "runtime-reported",
                    "source_digest": payload["series_digest"],
                    "freshness_state": payload["freshness_state"],
                    "limitations": [
                        "Forecasts are advisory and cannot authorize or execute trades.",
                        "Uncertainty is unavailable unless emitted by the configured runtime.",
                    ],
                    "paper_only": True,
                    "execution_authorized": False,
                    "broker_submission": False,
                    "portfolio_mutation": False,
                    "approval_authority": False,
                }
            except FutureTimeoutError:
                failure = ProviderFailure(
                    ProviderFailureClass.TIMEOUT, "Kronos inference timed out.", True
                )
            except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                failure = ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE, "Kronos inference failed safely.", True
                )
        evidence_input = {key: value for key, value in payload.items() if key != "bars"}
        evidence_output = (
            None
            if output is None
            else {
                **{key: value for key, value in output.items() if key != "forecast_points"},
                "forecast_points_digest": f"sha256:{canonical_digest(output['forecast_points'])}",
            }
        )
        evidence = build_invocation_evidence(
            request_id=invocation.request_id,
            task_correlation_id=invocation.task_correlation_id,
            provider_id=self.identity.provider_id,
            model_id=invocation.model_id,
            registry_revision=invocation.registry_revision,
            capability=invocation.capability,
            execution_location=self.identity.execution_location,
            started_at=invocation.started_at,
            ended_at=invocation.ended_at,
            succeeded=failure is None,
            failure_classification=None if failure is None else failure.classification.value,
            input_payload=evidence_input,
            output_payload=evidence_output,
            provider_metadata=(("adapter", "kronos-local-v1"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)
