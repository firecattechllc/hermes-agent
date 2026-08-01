"""Governed local-only FinBERT financial sentiment contracts and provider."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Protocol

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
from .provider import (
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)
from .registry import canonical_digest

FINBERT_SCHEMA_VERSION = 1
FINBERT_PROVIDER_ID = "local-finbert"
DEFAULT_FINBERT_MODEL = "ProsusAI/finbert"
MAX_SENTIMENT_BATCH_SIZE = 16
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_SENSITIVE_MARKERS = (
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "private_key",
    "private key",
    "password=",
    "secret=",
    "token=",
)
_EXECUTABLE_MARKERS = (
    "#!/bin/",
    "subprocess.",
    "os.system(",
    "eval(",
    "exec(",
)


class FinBERTValidationError(ValueError):
    """A FinBERT configuration, request, or output failed closed."""


class SentimentSourceType(str, Enum):
    NEWS_HEADLINE = "news_headline"
    NEWS_EXCERPT = "news_excerpt"
    EARNINGS_CALL_EXCERPT = "earnings_call_excerpt"
    SEC_FILING_EXCERPT = "sec_filing_excerpt"
    ANALYST_NOTE_EXCERPT = "analyst_note_excerpt"
    COMPANY_ANNOUNCEMENT_EXCERPT = "company_announcement_excerpt"
    MARKET_COMMENTARY_EXCERPT = "market_commentary_excerpt"
    PROPOSAL_EVIDENCE_EXCERPT = "proposal_evidence_excerpt"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


FINBERT_RESPONSIBILITIES = frozenset(
    {
        Responsibility.FINANCIAL_SENTIMENT_ANALYSIS,
        Responsibility.NEWS_SENTIMENT,
        Responsibility.EARNINGS_SENTIMENT,
        Responsibility.MARKET_CONTEXT,
        Responsibility.PROPOSAL_SUPPORT,
        Responsibility.RISK_ANALYSIS,
    }
)


def _environment_bool(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise FinBERTValidationError(f"{name} must be a boolean")


def _environment_int(
    environment: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = environment.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise FinBERTValidationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise FinBERTValidationError(f"{name} is outside its governed bound")
    return value


@dataclass(frozen=True, slots=True)
class FinBERTConfig:
    enabled: bool = False
    model: str = DEFAULT_FINBERT_MODEL
    model_version: str = "local-unverified"
    device: str = "cpu"
    timeout_ms: int = 15_000
    max_input_chars: int = 20_000
    max_batch_size: int = 8
    local_files_only: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or len(self.model) > 1_024
            or any(marker in self.model.lower() for marker in _SENSITIVE_MARKERS)
        ):
            raise FinBERTValidationError("FinBERT model source is invalid")
        validate_identifier(self.model_version, "FinBERT model_version")
        if self.device not in {"cpu", "mps", "cuda"}:
            raise FinBERTValidationError("FinBERT device is unsupported")
        if not 100 <= self.timeout_ms <= 300_000:
            raise FinBERTValidationError("FinBERT timeout is outside its governed bound")
        if not 256 <= self.max_input_chars <= 100_000:
            raise FinBERTValidationError("FinBERT input bound is invalid")
        if not 1 <= self.max_batch_size <= MAX_SENTIMENT_BATCH_SIZE:
            raise FinBERTValidationError("FinBERT batch bound is invalid")
        if self.local_files_only is not True:
            raise FinBERTValidationError("FinBERT must use local files only")

    @property
    def model_id(self) -> str:
        """Return a path-free stable registry identity for the configured source."""
        if self.model == DEFAULT_FINBERT_MODEL:
            return "prosusai.finbert"
        return f"finbert-local-{canonical_digest(self.model)[:16]}"

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> FinBERTConfig:
        source = os.environ if environment is None else environment
        return cls(
            enabled=_environment_bool(source, "SIGIL_AI_FINBERT_ENABLED", False),
            model=source.get("SIGIL_AI_FINBERT_MODEL", DEFAULT_FINBERT_MODEL),
            model_version=source.get("SIGIL_AI_FINBERT_MODEL_VERSION", "local-unverified"),
            device=source.get("SIGIL_AI_FINBERT_DEVICE", "cpu"),
            timeout_ms=_environment_int(
                source, "SIGIL_AI_FINBERT_TIMEOUT_MS", 15_000, 100, 300_000
            ),
            max_input_chars=_environment_int(
                source, "SIGIL_AI_FINBERT_MAX_INPUT_CHARS", 20_000, 256, 100_000
            ),
            max_batch_size=_environment_int(
                source, "SIGIL_AI_FINBERT_MAX_BATCH_SIZE", 8, 1, MAX_SENTIMENT_BATCH_SIZE
            ),
            local_files_only=_environment_bool(source, "SIGIL_AI_FINBERT_LOCAL_FILES_ONLY", True),
        )


@dataclass(frozen=True, slots=True)
class GovernedSentimentRequest:
    request_id: str
    task_correlation_id: str
    responsibility: Responsibility
    input_digest: str
    evidence_context_digests: tuple[str, ...]
    source_type: SentimentSourceType
    source_identity: str
    source_text: str
    language: str
    requested_at: str
    timeout_ms: int
    privacy_requirement: PrivacyTier = PrivacyTier.LOCAL_ONLY
    fallback_permission: bool = False
    schema_version: int = FINBERT_SCHEMA_VERSION
    capability: Capability = Capability.FINANCIAL_SENTIMENT
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != FINBERT_SCHEMA_VERSION:
            raise FinBERTValidationError("unsupported sentiment request schema")
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if self.capability != Capability.FINANCIAL_SENTIMENT:
            raise FinBERTValidationError("sentiment request capability mismatch")
        if not isinstance(self.source_type, SentimentSourceType):
            raise FinBERTValidationError("sentiment source type is unsupported")
        if self.responsibility not in FINBERT_RESPONSIBILITIES:
            raise FinBERTValidationError("sentiment responsibility is not advisory")
        if self.responsibility in PROHIBITED_RESPONSIBILITIES:
            raise FinBERTValidationError("sentiment responsibility is prohibited")
        if _SHA256.fullmatch(self.input_digest) is None:
            raise FinBERTValidationError("sentiment input digest is invalid")
        if not self.evidence_context_digests or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_context_digests
        ):
            raise FinBERTValidationError("sentiment evidence context is invalid")
        if len(self.evidence_context_digests) > 64:
            raise FinBERTValidationError("sentiment evidence context exceeds its bound")
        if _SAFE_SOURCE_ID.fullmatch(self.source_identity) is None:
            raise FinBERTValidationError("sentiment source identity is invalid")
        if self.language != "en":
            raise FinBERTValidationError("FinBERT supports governed English input only")
        if not self.source_text.strip():
            raise FinBERTValidationError("sentiment source text cannot be blank")
        encoded = self.source_text.encode("utf-8")
        if len(encoded) > 100_000:
            raise FinBERTValidationError("sentiment source text exceeds the absolute bound")
        lowered = self.source_text.lower()
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            raise FinBERTValidationError("sentiment source contains credential material")
        if any(marker in lowered for marker in _EXECUTABLE_MARKERS):
            raise FinBERTValidationError("sentiment source contains executable instructions")
        if f"sha256:{canonical_digest(self.source_text)}" != self.input_digest:
            raise FinBERTValidationError("sentiment source digest mismatch")
        if not 100 <= self.timeout_ms <= 300_000:
            raise FinBERTValidationError("sentiment timeout is outside its governed bound")
        if not self.requested_at:
            raise FinBERTValidationError("sentiment request timestamp cannot be blank")
        if self.paper_only is not True or self.broker_submission is not False:
            raise FinBERTValidationError("sentiment request cannot receive execution authority")


@dataclass(frozen=True, slots=True)
class FinBERTSentimentPayload:
    label: SentimentLabel
    positive_score: float
    neutral_score: float
    negative_score: float
    confidence: float
    model_id: str
    model_version: str
    source_identity: str
    source_digest: str
    analyzed_at: str
    limitations: tuple[str, ...]
    schema_version: int = FINBERT_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False


def validate_finbert_output(
    output: object,
    *,
    request: GovernedSentimentRequest,
    expected_model_id: str,
    expected_model_version: str,
) -> FinBERTSentimentPayload:
    if not isinstance(output, Mapping):
        raise FinBERTValidationError("FinBERT output must be an object")
    required = {
        "schema_version",
        "label",
        "positive_score",
        "neutral_score",
        "negative_score",
        "confidence",
        "model_id",
        "model_version",
        "source_identity",
        "source_digest",
        "analyzed_at",
        "limitations",
        "paper_only",
        "execution_authorized",
        "broker_submission",
        "portfolio_mutation",
        "approval_authority",
    }
    if set(output) != required or output.get("schema_version") != FINBERT_SCHEMA_VERSION:
        raise FinBERTValidationError("FinBERT output schema is invalid")
    scores: list[float] = []
    for name in ("positive_score", "neutral_score", "negative_score"):
        value = output[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FinBERTValidationError("FinBERT scores must be numeric")
        score = float(value)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise FinBERTValidationError("FinBERT scores must be between zero and one")
        scores.append(score)
    if not math.isclose(sum(scores), 1.0, abs_tol=1e-6):
        raise FinBERTValidationError("FinBERT scores must sum to one")
    try:
        label = SentimentLabel(output["label"])
    except (TypeError, ValueError) as error:
        raise FinBERTValidationError("FinBERT label is invalid") from error
    labels = (SentimentLabel.POSITIVE, SentimentLabel.NEUTRAL, SentimentLabel.NEGATIVE)
    selected = labels[max(range(3), key=lambda index: (scores[index], -index))]
    if label != selected:
        raise FinBERTValidationError("FinBERT label contradicts score distribution")
    confidence = output["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isclose(float(confidence), max(scores), abs_tol=1e-6)
    ):
        raise FinBERTValidationError("FinBERT confidence is invalid")
    if output["model_id"] != expected_model_id or output["model_version"] != expected_model_version:
        raise FinBERTValidationError("FinBERT output model identity mismatch")
    if output["source_identity"] != request.source_identity:
        raise FinBERTValidationError("FinBERT output source identity mismatch")
    if output["source_digest"] != request.input_digest:
        raise FinBERTValidationError("FinBERT output source digest mismatch")
    if not isinstance(output["analyzed_at"], str) or not output["analyzed_at"].strip():
        raise FinBERTValidationError("FinBERT analysis timestamp is invalid")
    limitations = output["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or len(limitations) > 16
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 512 for item in limitations
        )
    ):
        raise FinBERTValidationError("FinBERT limitations are invalid")
    authority = {
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "approval_authority": False,
    }
    if any(output[name] is not expected for name, expected in authority.items()):
        raise FinBERTValidationError("FinBERT output cannot carry execution authority")
    encoded = json.dumps(dict(output), sort_keys=True).lower()
    if any(marker in encoded for marker in _SENSITIVE_MARKERS):
        raise FinBERTValidationError("FinBERT output contains credential material")
    return FinBERTSentimentPayload(
        label=label,
        positive_score=scores[0],
        neutral_score=scores[1],
        negative_score=scores[2],
        confidence=float(confidence),
        model_id=expected_model_id,
        model_version=expected_model_version,
        source_identity=request.source_identity,
        source_digest=request.input_digest,
        analyzed_at=str(output["analyzed_at"]),
        limitations=tuple(item.strip() for item in limitations),
    )


@dataclass(frozen=True, slots=True)
class GovernedSentimentArtifact:
    artifact_id: str
    request_id: str
    task_correlation_id: str
    provider_id: str
    model_id: str
    model_version: str
    capability: Capability
    responsibility: Responsibility
    created_at: str
    routing_evidence_id: str
    invocation_evidence_id: str
    input_digest: str
    output_digest: str
    structured_payload: FinBERTSentimentPayload
    citations: tuple[str, ...]
    confidence: float
    limitations: tuple[str, ...]
    stale_after: str | None = None
    schema_version: int = FINBERT_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    def __post_init__(self) -> None:
        if re.fullmatch(r"analysis-artifact-[0-9a-f]{64}", self.artifact_id) is None:
            raise FinBERTValidationError("sentiment artifact identity is invalid")
        if self.capability != Capability.FINANCIAL_SENTIMENT:
            raise FinBERTValidationError("sentiment artifact capability mismatch")
        if any(
            _SHA256.fullmatch(item) is None
            for item in (
                self.routing_evidence_id,
                self.invocation_evidence_id,
                self.input_digest,
                self.output_digest,
                *self.citations,
            )
        ):
            raise FinBERTValidationError("sentiment artifact evidence identity is invalid")
        if self.input_digest != self.structured_payload.source_digest:
            raise FinBERTValidationError("sentiment artifact source digest mismatch")
        if self.confidence != self.structured_payload.confidence:
            raise FinBERTValidationError("sentiment artifact confidence mismatch")
        if self.limitations != self.structured_payload.limitations:
            raise FinBERTValidationError("sentiment artifact limitations mismatch")
        if any(
            (
                self.paper_only is not True,
                self.execution_authorized is not False,
                self.broker_submission is not False,
                self.portfolio_mutation is not False,
                self.approval_authority is not False,
            )
        ):
            raise FinBERTValidationError("sentiment artifact cannot carry execution authority")


def build_sentiment_artifact(**values: object) -> GovernedSentimentArtifact:
    unsigned = {**values, "artifact_id": "pending", "schema_version": FINBERT_SCHEMA_VERSION}
    payload = {**unsigned, "structured_payload": asdict(unsigned["structured_payload"])}
    return GovernedSentimentArtifact(
        **{**unsigned, "artifact_id": f"analysis-artifact-{canonical_digest(payload)}"}
    )


@dataclass(frozen=True, slots=True)
class SentimentAggregation:
    positive_score: float
    neutral_score: float
    negative_score: float
    label: SentimentLabel
    confidence: float
    source_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    source_identities: tuple[str, ...]
    window_start: str
    window_end: str
    freshness: str
    limitations: tuple[str, ...]
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False


def aggregate_sentiment(
    artifacts: Sequence[GovernedSentimentArtifact],
    *,
    weights: Sequence[float] | None = None,
    window_start: str,
    window_end: str,
    freshness: str,
) -> SentimentAggregation:
    if not artifacts or len(artifacts) > 100:
        raise FinBERTValidationError("sentiment aggregation requires 1 to 100 artifacts")
    resolved = tuple(1.0 for _ in artifacts) if weights is None else tuple(weights)
    if len(resolved) != len(artifacts) or any(
        isinstance(item, bool) or not isinstance(item, (int, float)) or item <= 0
        for item in resolved
    ):
        raise FinBERTValidationError("sentiment aggregation weights are invalid")
    total = float(sum(resolved))
    scores = tuple(
        sum(
            weight * getattr(item.structured_payload, field)
            for item, weight in zip(artifacts, resolved, strict=True)
        )
        / total
        for field in ("positive_score", "neutral_score", "negative_score")
    )
    labels = (SentimentLabel.POSITIVE, SentimentLabel.NEUTRAL, SentimentLabel.NEGATIVE)
    label = labels[max(range(3), key=lambda index: (scores[index], -index))]
    confidence = min(max(scores), max(item.confidence for item in artifacts))
    limitations = tuple(sorted({value for item in artifacts for value in item.limitations}))
    return SentimentAggregation(
        positive_score=scores[0],
        neutral_score=scores[1],
        negative_score=scores[2],
        label=label,
        confidence=confidence,
        source_count=len(artifacts),
        positive_count=sum(
            item.structured_payload.label == SentimentLabel.POSITIVE for item in artifacts
        ),
        neutral_count=sum(
            item.structured_payload.label == SentimentLabel.NEUTRAL for item in artifacts
        ),
        negative_count=sum(
            item.structured_payload.label == SentimentLabel.NEGATIVE for item in artifacts
        ),
        source_identities=tuple(item.structured_payload.source_identity for item in artifacts),
        window_start=window_start,
        window_end=window_end,
        freshness=freshness,
        limitations=limitations,
    )


class FinBERTRuntime(Protocol):
    def predict(self, *, text: str) -> Mapping[str, float]: ...


class TransformersFinBERTRuntime:
    """Lazy optional Transformers runtime; construction never downloads weights."""

    def __init__(self, config: FinBERTConfig) -> None:
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            pipeline,
        )

        tokenizer = AutoTokenizer.from_pretrained(config.model, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            config.model, local_files_only=True
        )
        self._pipeline = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            device=-1 if config.device == "cpu" else config.device,
            top_k=None,
        )

    def predict(self, *, text: str) -> Mapping[str, float]:
        result = self._pipeline(text, truncation=True)
        rows = result[0] if result and isinstance(result[0], list) else result
        return {str(item["label"]).lower(): float(item["score"]) for item in rows}


class LocalFinBERTProvider:
    input_contract = "application/json;schema=sigil.ai.input.financial-sentiment.v1"
    output_contract = "application/json;schema=sigil.ai.output.financial-sentiment.v1"
    capabilities = frozenset({Capability.FINANCIAL_SENTIMENT})
    model_family = "finbert"

    def __init__(self, config: FinBERTConfig, runtime: FinBERTRuntime | None = None) -> None:
        self.config = config
        self.model_id = config.model_id
        self.model_version = config.model_version
        self.request_timeout_ms = config.timeout_ms
        self._runtime = runtime
        dependencies = all(
            importlib.util.find_spec(name) is not None for name in ("torch", "transformers")
        )
        health = (
            ProviderHealth.HEALTHY
            if config.enabled and (runtime or dependencies)
            else ProviderHealth.UNAVAILABLE
        )
        self.identity = ProviderIdentity(
            FINBERT_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            health=health,
            enabled=config.enabled,
            metadata=(("runtime", "local-files-only"),),
        )

    def registration(self) -> ModelRegistration:
        return ModelRegistration(
            model_id=self.model_id,
            provider_id=self.identity.provider_id,
            family=self.model_family,
            version=self.model_version,
            capabilities=self.capabilities,
            execution_location=ExecutionLocation.LOCAL,
            context_limit=self.config.max_input_chars,
            supported_input_types=frozenset({InputType.TEXT}),
            structured_output=True,
            cost_class=CostClass.FREE,
            trust_tier=TrustTier.TRUSTED,
            privacy_tier=PrivacyTier.LOCAL_ONLY,
            health=self.identity.health,
            enabled=self.config.enabled,
            allowed_responsibilities=FINBERT_RESPONSIBILITIES,
        )

    def invoke(self, invocation: ProviderInvocation) -> ProviderResult:
        failure: ProviderFailure | None = None
        output: Mapping[str, object] | None = None
        payload = invocation.input_payload
        if not self.config.enabled or self.identity.health != ProviderHealth.HEALTHY:
            failure = ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Local FinBERT is unavailable.", True
            )
        elif invocation.model_id != self.model_id:
            failure = ProviderFailure(
                ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                "FinBERT model identity mismatch.",
                False,
            )
        elif invocation.capability != Capability.FINANCIAL_SENTIMENT:
            failure = ProviderFailure(
                ProviderFailureClass.CAPABILITY_MISMATCH,
                "FinBERT capability mismatch.",
                False,
            )
        elif (
            not isinstance(payload.get("source_text"), str)
            or len(payload["source_text"]) > self.config.max_input_chars
        ):
            failure = ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "FinBERT input exceeded its governed bound.",
                False,
            )
        else:
            try:
                runtime = self._runtime or TransformersFinBERTRuntime(self.config)
                self._runtime = runtime
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(runtime.predict, text=payload["source_text"])
                    scores = future.result(timeout=invocation.timeout_ms / 1000)
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)
                normalized = self._normalize_scores(scores)
                output = {
                    "schema_version": 1,
                    "label": normalized["label"],
                    "positive_score": normalized["positive"],
                    "neutral_score": normalized["neutral"],
                    "negative_score": normalized["negative"],
                    "confidence": normalized[normalized["label"]],
                    "model_id": self.model_id,
                    "model_version": self.model_version,
                    "source_identity": payload["source_identity"],
                    "source_digest": payload["source_digest"],
                    "analyzed_at": invocation.ended_at,
                    "limitations": [
                        "Advisory financial sentiment only.",
                        "Classification may not capture context, irony, or changing facts.",
                    ],
                    "paper_only": True,
                    "execution_authorized": False,
                    "broker_submission": False,
                    "portfolio_mutation": False,
                    "approval_authority": False,
                }
            except FutureTimeoutError:
                failure = ProviderFailure(
                    ProviderFailureClass.TIMEOUT, "Local FinBERT timed out.", True
                )
            except (ImportError, OSError, RuntimeError):
                failure = ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE, "Local FinBERT is unavailable.", True
                )
            except (KeyError, TypeError, ValueError):
                failure = ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Local FinBERT returned malformed scores.",
                    False,
                )
        evidence_payload = {key: value for key, value in payload.items() if key != "source_text"}
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
            input_payload=evidence_payload,
            output_payload=output,
            provider_metadata=(("runtime", "local-finbert-v1"),),
        )
        return ProviderResult(output=output, failure=failure, evidence=evidence)

    @staticmethod
    def _normalize_scores(scores: Mapping[str, float]) -> dict[str, Any]:
        values: dict[str, float] = {}
        for label in ("positive", "neutral", "negative"):
            value = scores.get(label)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError("invalid FinBERT score")
            values[label] = float(value)
        total = sum(values.values())
        if total <= 0:
            raise ValueError("empty FinBERT probability mass")
        normalized = {label: value / total for label, value in values.items()}
        normalized["label"] = max(
            ("positive", "neutral", "negative"), key=lambda label: normalized[label]
        )
        return normalized
