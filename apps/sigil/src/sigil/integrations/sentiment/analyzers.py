from __future__ import annotations

import re
from dataclasses import dataclass

from sigil.financial.models import (
    ConfidenceScore,
    FinancialDocument,
    SentimentLabel,
    SentimentResult,
)

_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z'-]*")

_POSITIVE = frozenset(
    {
        "accelerate",
        "beat",
        "benefit",
        "improve",
        "growth",
        "profit",
        "record",
        "resilient",
        "strong",
        "upgrade",
    }
)
_NEGATIVE = frozenset(
    {
        "decline",
        "downgrade",
        "loss",
        "miss",
        "pressure",
        "risk",
        "slowdown",
        "weak",
        "uncertain",
        "impairment",
    }
)


@dataclass(frozen=True, slots=True)
class DeterministicFinancialSentimentAnalyzer:
    """Offline test/reference analyzer; not a replacement for FinBERT."""

    model_name: str = "sigil-deterministic-financial-sentiment"
    model_version: str = "1"

    def analyze(self, document: FinancialDocument) -> SentimentResult:
        tokens = [token.lower() for token in _TOKEN.findall(document.text)]
        positive_hits = sum(token in _POSITIVE for token in tokens)
        negative_hits = sum(token in _NEGATIVE for token in tokens)
        evidence_hits = positive_hits + negative_hits

        if evidence_hits == 0:
            positive, negative = 0.1, 0.1
            neutral = 0.8
            label = SentimentLabel.NEUTRAL
            confidence_value = 0.55
            rationale = "No directional lexicon evidence; neutral fallback."
        else:
            directional = positive_hits - negative_hits
            strength = min(evidence_hits / max(len(tokens), 1) * 8, 0.75)
            neutral = max(0.15, 0.65 - strength)
            remaining = 1.0 - neutral
            if directional > 0:
                positive = remaining * 0.8
                negative = remaining * 0.2
                label = SentimentLabel.POSITIVE
            elif directional < 0:
                positive = remaining * 0.2
                negative = remaining * 0.8
                label = SentimentLabel.NEGATIVE
            else:
                positive = remaining / 2
                negative = remaining / 2
                label = SentimentLabel.NEUTRAL
            confidence_value = min(0.95, 0.6 + strength)
            rationale = (
                f"Deterministic lexicon evidence: {positive_hits} positive and "
                f"{negative_hits} negative hits."
            )

        total = positive + neutral + negative
        return SentimentResult(
            label=label,
            positive=positive / total,
            neutral=neutral / total,
            negative=negative / total,
            confidence=ConfidenceScore(
                value=confidence_value,
                rationale=rationale,
            ),
            model_name=self.model_name,
            model_version=self.model_version,
        )
