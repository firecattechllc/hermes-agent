from datetime import UTC, date, datetime

import pytest

from sigil.financial.models import (
    ConfidenceScore,
    Filing,
    FilingType,
    FinancialDocument,
    Instrument,
    InstrumentType,
    Provenance,
    SentimentLabel,
    SentimentResult,
)
from sigil.integrations.hermes.client import InMemoryHermesAdapter
from sigil.integrations.sentiment.analyzers import (
    DeterministicFinancialSentimentAnalyzer,
)
from sigil.workflows.analyze_financial_text import AnalyzeFinancialTextWorkflow


def instrument() -> Instrument:
    return Instrument(
        symbol=" aapl ",
        name=" Apple Inc. ",
        instrument_type=InstrumentType.EQUITY,
        exchange=" nasdaq ",
    ).normalized()


def document(text: str) -> FinancialDocument:
    provenance = Provenance.for_text(
        source_name="fixture",
        source_uri="file:///fixtures/aapl.txt",
        text=text,
        retrieved_at=datetime(2026, 7, 23, tzinfo=UTC),
    )
    return FinancialDocument(
        document_id="fixture-aapl",
        text=text,
        provenance=provenance,
        instrument=instrument(),
    )


def test_instrument_normalization() -> None:
    normalized = instrument()
    assert normalized.symbol == "AAPL"
    assert normalized.exchange == "NASDAQ"
    assert normalized.currency == "USD"


def test_filing_rejects_period_after_filing_date() -> None:
    provenance = Provenance.for_text(
        source_name="fixture",
        source_uri="file:///fixtures/filing.txt",
        text="filing",
    )
    with pytest.raises(ValueError, match="period_end"):
        Filing(
            instrument=instrument(),
            filing_type=FilingType.TEN_Q,
            filed_on=date(2026, 1, 1),
            period_end=date(2026, 2, 1),
            accession_number=None,
            provenance=provenance,
        )


def test_document_detects_provenance_hash_mismatch() -> None:
    provenance = Provenance.for_text(
        source_name="fixture",
        source_uri="file:///fixtures/hash.txt",
        text="original",
    )
    with pytest.raises(ValueError, match="provenance hash"):
        FinancialDocument(
            document_id="mismatch",
            text="changed",
            provenance=provenance,
        )


def test_sentiment_result_requires_normalized_scores() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        SentimentResult(
            label=SentimentLabel.POSITIVE,
            positive=0.8,
            neutral=0.4,
            negative=0.1,
            confidence=ConfidenceScore(0.8, "fixture"),
            model_name="fixture",
            model_version="1",
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Record profit and strong growth improved outlook.", SentimentLabel.POSITIVE),
        ("Weak demand and impairment risk caused a loss.", SentimentLabel.NEGATIVE),
        ("The company filed its quarterly report.", SentimentLabel.NEUTRAL),
    ],
)
def test_deterministic_financial_sentiment(
    text: str,
    expected: SentimentLabel,
) -> None:
    result = DeterministicFinancialSentimentAnalyzer().analyze(document(text))
    assert result.label is expected
    assert result.positive + result.neutral + result.negative == pytest.approx(1.0)


def test_workflow_records_governed_evidence() -> None:
    hermes = InMemoryHermesAdapter()
    workflow = AnalyzeFinancialTextWorkflow(
        sentiment=DeterministicFinancialSentimentAnalyzer(),
        evidence=hermes,
    )

    result = workflow.run(document("Strong growth and record profit."))

    assert result.label is SentimentLabel.POSITIVE
    assert len(hermes.recorded) == 1
    evidence = hermes.recorded[0]
    assert evidence["id"] == "sigil-evidence-1"
    assert evidence["kind"] == "sigil_financial_sentiment_analyzed"

    payload = evidence["payload"]
    assert payload["instrument_symbol"] == "AAPL"
    assert payload["content_sha256"]
    assert payload["model"]["name"] == "sigil-deterministic-financial-sentiment"
