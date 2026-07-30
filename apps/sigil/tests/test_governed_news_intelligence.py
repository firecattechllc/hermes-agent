import pytest

from sigil.desktop_bridge.governed_news import (
    build_news_intelligence,
    empty_news_intelligence,
    normalize_news_item,
)
from sigil.desktop_bridge.paper_execution import initialize_execution_state

NOW = "2026-07-30T03:00:00Z"


def test_normalizes_source_backed_news_deterministically() -> None:
    payload = {
        "headline": "Company raises full-year guidance",
        "summary": "Management increased its revenue outlook.",
        "source": "Example Wire",
        "source_url": "https://example.com/story",
        "published_at": "2026-07-29T22:55:00-04:00",
        "symbols": ["msft", "MSFT"],
        "sentiment": "bullish",
        "confidence": "0.84",
    }
    first = normalize_news_item(payload, received_at=NOW)
    second = normalize_news_item(payload, received_at=NOW)
    assert first == second
    assert first["symbols"] == ["MSFT"]
    assert first["published_at"] == "2026-07-30T02:55:00Z"
    assert first["execution_authority"] is False
    assert first["broker_submission_attempted"] is False
    assert len(first["evidence_identity"]) == 64


def test_news_rejects_execution_authority() -> None:
    with pytest.raises(ValueError, match="cannot grant execution authority"):
        normalize_news_item(
            {
                "headline": "Unsafe item",
                "source": "Example",
                "source_url": "https://example.com",
                "published_at": NOW,
                "execution_authority": True,
            },
            received_at=NOW,
        )


def test_news_requires_provenance() -> None:
    with pytest.raises(ValueError, match="source and source_url are required"):
        normalize_news_item(
            {"headline": "Missing provenance", "published_at": NOW}, received_at=NOW
        )


def test_builds_bounded_runtime_projection() -> None:
    item = normalize_news_item(
        {
            "headline": "Demand remains resilient",
            "source": "Example Wire",
            "source_url": "https://example.com/demand",
            "published_at": NOW,
            "symbols": ["NVDA"],
            "sentiment": "mixed",
            "confidence": "0.65",
        },
        received_at=NOW,
    )
    result = build_news_intelligence([item])
    assert result["status"] == "ready"
    assert result["headline_count"] == 1
    assert result["symbol_count"] == 1
    assert result["sentiment_counts"]["mixed"] == 1
    assert result["broker_submission_available"] is False
    assert result["broker_submission_attempted"] is False


def test_runtime_upgrade_exposes_empty_news_intelligence() -> None:
    state = {
        "schema_version": 1,
        "balances": {"cash": "100.00", "portfolio_value": "100.00"},
        "positions": [],
        "audit": [],
        "proposals": [],
        "executions": [],
        "reconciliation": [],
        "connection": {"status": "connected"},
    }
    initialize_execution_state(state)
    assert state["news_intelligence"] == empty_news_intelligence()
    assert state["news_intelligence"]["mode"] == "research-only"
    assert state["news_intelligence"]["execution_authority"] is False
