from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sigil.desktop_bridge.governed_news_context import production_news_context
from sigil.desktop_bridge.governed_news_store import NewsEvidenceStore

NOW = datetime(2026, 7, 30, 19, 30, tzinfo=UTC)


def payload(
    *,
    headline: str,
    symbol: str = "AAPL",
    source: str = "Source One",
    sentiment: str = "neutral",
    published_at: datetime = NOW,
) -> dict[str, object]:
    return {
        "headline": headline,
        "summary": f"Summary for {headline}",
        "source": source,
        "source_url": f"https://example.com/{headline.replace(' ', '-').lower()}",
        "published_at": published_at.isoformat().replace("+00:00", "Z"),
        "symbols": [symbol],
        "sentiment": sentiment,
        "confidence": "0.80",
        "execution_authority": False,
        "broker_submission": False,
    }


def ingest(
    store: NewsEvidenceStore,
    item: dict[str, object],
    *,
    received_at: datetime = NOW,
) -> None:
    result = store.ingest(
        item,
        received_at=received_at.isoformat().replace("+00:00", "Z"),
    )
    assert result["status"] == "stored"


def test_missing_news_is_explicit_and_cannot_change_candidate_eligibility(tmp_path) -> None:
    result = production_news_context(
        NewsEvidenceStore(tmp_path),
        ("AAPL",),
        now=NOW,
    )

    assert result["status"] == "empty"
    assert result["by_symbol"]["AAPL"]["status"] == "unavailable"
    assert result["by_symbol"]["AAPL"]["risk_flags"] == ["news_evidence_missing"]
    assert result["advisory_only"] is True
    assert result["affects_candidate_eligibility"] is False
    assert result["execution_authority"] is False
    assert result["broker_submission_attempted"] is False


def test_fresh_multi_source_news_is_linked_to_the_research_symbol(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)

    ingest(
        store,
        payload(
            headline="Company raises guidance",
            source="Wire One",
            sentiment="bullish",
        ),
    )
    ingest(
        store,
        payload(
            headline="Analyst confirms demand",
            source="Wire Two",
            sentiment="bullish",
        ),
    )

    result = production_news_context(store, ("aapl",), now=NOW)
    context = result["by_symbol"]["AAPL"]

    assert result["status"] == "ready"
    assert result["symbols_with_current_news"] == 1
    assert context["current_headline_count"] == 2
    assert context["source_count"] == 2
    assert context["agreement"] == "corroborated"
    assert context["sentiment_counts"]["bullish"] == 2
    assert context["risk_flags"] == []
    assert context["affects_candidate_eligibility"] is False


def test_conflicting_directional_news_is_flagged_without_execution_authority(
    tmp_path,
) -> None:
    store = NewsEvidenceStore(tmp_path)

    ingest(
        store,
        payload(
            headline="Demand improves",
            source="Wire One",
            sentiment="bullish",
        ),
    )
    ingest(
        store,
        payload(
            headline="Regulatory risk increases",
            source="Wire Two",
            sentiment="bearish",
        ),
    )

    result = production_news_context(store, ("AAPL",), now=NOW)
    context = result["by_symbol"]["AAPL"]

    assert context["agreement"] == "conflicting"
    assert "directional_news_conflict" in context["risk_flags"]
    assert context["execution_authority"] is False
    assert context["broker_submission_attempted"] is False


def test_bearish_majority_is_advisory_only(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)

    ingest(
        store,
        payload(
            headline="Margin pressure emerges",
            source="Wire One",
            sentiment="bearish",
        ),
    )
    ingest(
        store,
        payload(
            headline="Supplier warns on demand",
            source="Wire Two",
            sentiment="bearish",
        ),
    )
    ingest(
        store,
        payload(
            headline="Product launch remains scheduled",
            source="Wire Three",
            sentiment="neutral",
        ),
    )

    context = production_news_context(store, ("AAPL",), now=NOW)["by_symbol"]["AAPL"]

    assert "bearish_news_majority" in context["risk_flags"]
    assert context["affects_candidate_eligibility"] is False
    assert context["advisory_only"] is True


def test_old_news_is_not_represented_as_current(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)

    ingest(
        store,
        payload(
            headline="Old earnings headline",
            sentiment="bullish",
            published_at=NOW - timedelta(hours=73),
        ),
    )

    result = production_news_context(store, ("AAPL",), now=NOW)
    context = result["by_symbol"]["AAPL"]

    assert result["status"] == "stale_or_invalid"
    assert context["status"] == "unavailable"
    assert context["current_headline_count"] == 0
    assert context["stale_headline_count"] == 1
    assert context["risk_flags"] == [
        "news_evidence_not_current",
        "stale_news_present",
    ]


def test_projection_is_deterministic_and_symbol_order_is_normalized(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)

    ingest(
        store,
        payload(
            headline="Microsoft headline",
            symbol="MSFT",
            sentiment="neutral",
        ),
    )

    first = production_news_context(store, ("msft", "AAPL", "MSFT"), now=NOW)
    second = production_news_context(store, ("AAPL", "MSFT"), now=NOW)

    assert first == second
    assert first["symbols_requested"] == ["AAPL", "MSFT"]
