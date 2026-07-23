from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sigil.financial.models import FinancialDocument, SentimentResult
from sigil.integrations.hermes.ports import HermesEvidencePort
from sigil.integrations.sentiment.ports import FinancialSentimentPort


@dataclass(slots=True)
class AnalyzeFinancialTextWorkflow:
    sentiment: FinancialSentimentPort
    evidence: HermesEvidencePort

    def run(self, document: FinancialDocument) -> SentimentResult:
        result = self.sentiment.analyze(document)
        payload: dict[str, Any] = {
            "document_id": document.document_id,
            "source_uri": document.provenance.source_uri,
            "content_sha256": document.provenance.content_sha256,
            "instrument_symbol": (
                document.instrument.symbol if document.instrument else None
            ),
            "label": result.label.value,
            "scores": {
                "positive": result.positive,
                "neutral": result.neutral,
                "negative": result.negative,
            },
            "confidence": result.confidence.value,
            "confidence_rationale": result.confidence.rationale,
            "model": {
                "name": result.model_name,
                "version": result.model_version,
            },
        }
        self.evidence.record(
            kind="sigil_financial_sentiment_analyzed",
            payload=payload,
        )
        return result
