from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from sigil.financial.models import (
    FinancialDocument,
    Instrument,
    InstrumentType,
    Provenance,
    SentimentLabel,
)
from sigil.integrations.sentiment import (
    DeterministicFinancialSentimentAnalyzer,
    GovernedSentimentRouter,
    TitanFinBERTAnalyzer,
    TitanFinBERTError,
)


def document(text: str = "Revenue growth remained strong.") -> FinancialDocument:
    provenance = Provenance.for_text(
        source_name="fixture",
        source_uri="file:///fixtures/titan-finbert.txt",
        text=text,
        retrieved_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    return FinancialDocument(
        document_id="titan-fixture",
        text=text,
        provenance=provenance,
        instrument=Instrument(
            symbol="AAPL",
            name="Apple Inc.",
            instrument_type=InstrumentType.EQUITY,
        ).normalized(),
    )


@dataclass
class RecordingTransport:
    response: Mapping[str, Any]

    def __post_init__(self) -> None:
        self.requests: list[Mapping[str, Any]] = []

    def infer(self, *, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.requests.append(request)
        return self.response


def valid_response() -> Mapping[str, Any]:
    return {
        "schema_version": 1,
        "model": "ProsusAI/finbert",
        "model_version": "4556d1301521",
        "scores": {
            "positive": 0.82,
            "neutral": 0.13,
            "negative": 0.05,
        },
        "confidence": 0.82,
        "rationale": "FinBERT classified the supplied financial text.",
    }


def test_titan_adapter_builds_governed_local_only_request() -> None:
    transport = RecordingTransport(valid_response())
    result = TitanFinBERTAnalyzer(transport=transport).analyze(document())

    assert result.label is SentimentLabel.POSITIVE
    assert result.model_name == "ProsusAI/finbert"
    assert result.model_version == "4556d1301521"

    request = transport.requests[0]
    assert request["operation"] == "financial_sentiment"
    assert request["document"]["instrument_symbol"] == "AAPL"
    assert request["constraints"] == {
        "local_only": True,
        "allow_model_download": False,
        "allow_external_api": False,
        "allow_trade_execution": False,
    }


def test_titan_adapter_normalizes_probability_mass() -> None:
    response = dict(valid_response())
    response["scores"] = {"positive": 8, "neutral": 1, "negative": 1}
    result = TitanFinBERTAnalyzer(
        transport=RecordingTransport(response)
    ).analyze(document())

    assert result.positive == pytest.approx(0.8)
    assert result.neutral == pytest.approx(0.1)
    assert result.negative == pytest.approx(0.1)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"schema_version": 99},
        {
            "schema_version": 1,
            "model": "unexpected/model",
            "scores": {"positive": 1, "neutral": 0, "negative": 0},
        },
        {
            "schema_version": 1,
            "model": "ProsusAI/finbert",
            "scores": {"positive": -1, "neutral": 1, "negative": 1},
        },
    ],
)
def test_titan_adapter_rejects_untrusted_responses(
    response: Mapping[str, Any],
) -> None:
    with pytest.raises(TitanFinBERTError):
        TitanFinBERTAnalyzer(
            transport=RecordingTransport(response)
        ).analyze(document())


def test_router_uses_explicit_fallback_when_titan_response_is_invalid() -> None:
    primary = TitanFinBERTAnalyzer(transport=RecordingTransport({}))
    router = GovernedSentimentRouter(
        primary=primary,
        fallback=DeterministicFinancialSentimentAnalyzer(),
    )

    result = router.analyze(document("Strong growth and record profit."))

    assert result.label is SentimentLabel.POSITIVE
    assert result.model_name == "sigil-deterministic-financial-sentiment"


def test_router_can_forbid_fallback() -> None:
    router = GovernedSentimentRouter(
        primary=TitanFinBERTAnalyzer(transport=RecordingTransport({})),
        fallback=DeterministicFinancialSentimentAnalyzer(),
        allow_fallback=False,
    )

    with pytest.raises(TitanFinBERTError):
        router.analyze(document())
