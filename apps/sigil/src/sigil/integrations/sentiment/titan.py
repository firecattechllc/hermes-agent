from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from sigil.financial.models import (
    ConfidenceScore,
    FinancialDocument,
    SentimentLabel,
    SentimentResult,
)


class TitanFinBERTTransport(Protocol):
    """Governed request boundary for Titan-hosted FinBERT inference."""

    def infer(self, *, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return one normalized FinBERT response without unrelated side effects."""


class TitanFinBERTError(RuntimeError):
    """Raised when Titan FinBERT cannot return a valid governed result."""


@dataclass(frozen=True, slots=True)
class TitanFinBERTAnalyzer:
    """Financial sentiment analyzer backed by governed Titan inference."""

    transport: TitanFinBERTTransport
    model_name: str = "ProsusAI/finbert"
    model_version: str = "titan-certified-local"
    max_characters: int = 20_000

    def analyze(self, document: FinancialDocument) -> SentimentResult:
        text = document.text.strip()
        if len(text) > self.max_characters:
            raise TitanFinBERTError(
                f"document exceeds governed FinBERT limit of {self.max_characters} characters"
            )

        request = {
            "schema_version": 1,
            "operation": "financial_sentiment",
            "model": self.model_name,
            "document": {
                "id": document.document_id,
                "text": text,
                "content_sha256": document.provenance.content_sha256,
                "source_uri": document.provenance.source_uri,
                "instrument_symbol": (
                    document.instrument.symbol if document.instrument else None
                ),
            },
            "constraints": {
                "local_only": True,
                "allow_model_download": False,
                "allow_external_api": False,
                "allow_trade_execution": False,
            },
        }

        response = self.transport.infer(request=request)
        return self._normalize_response(response)

    def _normalize_response(self, response: Mapping[str, Any]) -> SentimentResult:
        if response.get("schema_version") != 1:
            raise TitanFinBERTError("unsupported Titan FinBERT response schema")

        if response.get("model") != self.model_name:
            raise TitanFinBERTError("Titan returned an unexpected model identity")

        raw_scores = response.get("scores")
        if not isinstance(raw_scores, Mapping):
            raise TitanFinBERTError("Titan response is missing sentiment scores")

        scores = {
            label: self._score(raw_scores, label)
            for label in ("positive", "neutral", "negative")
        }
        total = sum(scores.values())
        if total <= 0:
            raise TitanFinBERTError("Titan returned empty sentiment probability mass")

        normalized = {key: value / total for key, value in scores.items()}
        label_name = max(normalized, key=normalized.__getitem__)

        confidence = response.get("confidence")
        if confidence is None:
            confidence = normalized[label_name]
        if not isinstance(confidence, (int, float)):
            raise TitanFinBERTError("Titan returned invalid confidence")
        confidence_value = float(confidence)
        if not 0.0 <= confidence_value <= 1.0:
            raise TitanFinBERTError("Titan confidence must be between 0 and 1")

        rationale = response.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = (
                "Confidence reflects the highest normalized probability returned "
                "by the governed Titan FinBERT adapter."
            )

        return SentimentResult(
            label=SentimentLabel(label_name),
            positive=normalized["positive"],
            neutral=normalized["neutral"],
            negative=normalized["negative"],
            confidence=ConfidenceScore(
                value=confidence_value,
                rationale=rationale,
            ),
            model_name=self.model_name,
            model_version=str(response.get("model_version") or self.model_version),
        )

    @staticmethod
    def _score(scores: Mapping[str, Any], label: str) -> float:
        value = scores.get(label)
        if not isinstance(value, (int, float)):
            raise TitanFinBERTError(f"Titan response is missing numeric {label} score")
        score = float(value)
        if score < 0:
            raise TitanFinBERTError(f"Titan returned a negative {label} score")
        return score


@dataclass(frozen=True, slots=True)
class GovernedSentimentRouter:
    """Prefer Titan FinBERT while allowing an explicit governed fallback."""

    primary: TitanFinBERTAnalyzer
    fallback: Any
    allow_fallback: bool = True

    def analyze(self, document: FinancialDocument) -> SentimentResult:
        try:
            return self.primary.analyze(document)
        except TitanFinBERTError:
            if not self.allow_fallback:
                raise
            return self.fallback.analyze(document)
