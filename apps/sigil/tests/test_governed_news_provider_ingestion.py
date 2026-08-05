from datetime import UTC, datetime

from sigil.desktop_bridge.governed_news_ingestion import collect_governed_news
from sigil.desktop_bridge.governed_news_providers import JsonNewsProvider
from sigil.desktop_bridge.governed_news_store import NewsEvidenceStore

NOW = datetime(2026, 7, 30, 3, 30, tzinfo=UTC)


def item(headline: str = "Revenue outlook raised") -> dict[str, object]:
    return {
        "headline": headline,
        "summary": "A governed provider item.",
        "source_url": "https://example.com/news/revenue-outlook",
        "published_at": "2026-07-30T03:25:00Z",
        "symbols": ["MSFT"],
        "sentiment": "bullish",
        "confidence": "0.9",
    }


def test_json_provider_uses_https_and_projects_rate_limit() -> None:
    captured: dict[str, object] = {}

    def fetch(url, headers, timeout):
        captured.update(url=url, headers=dict(headers), timeout=timeout)
        return {"articles": [item()]}, {
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Remaining": "91",
            "X-RateLimit-Reset": "123456",
        }

    provider = JsonNewsProvider(
        name="Test Wire",
        endpoint="https://news.example.test/v1/articles",
        items_field="articles",
        fetch_json=fetch,
    )
    batch = provider.collect(["msft", "MSFT"])

    assert captured["url"].endswith("symbols=MSFT")
    assert batch.provider == "Test Wire"
    assert len(batch.items) == 1
    assert batch.rate_limit == {"limit": 100, "remaining": 91, "reset": 123456}


def test_ingestion_persists_valid_items_and_deduplicates(tmp_path) -> None:
    def fetch(url, headers, timeout):
        return {"items": [item()]}, {}

    provider = JsonNewsProvider(
        name="Test Wire",
        endpoint="https://news.example.test/v1/articles",
        fetch_json=fetch,
    )
    store = NewsEvidenceStore(tmp_path)

    first = collect_governed_news(
        provider=provider,
        store=store,
        symbols=["MSFT"],
        now=NOW,
    )
    second = collect_governed_news(
        provider=provider,
        store=store,
        symbols=["MSFT"],
        now=NOW,
    )

    assert first["status"] == "complete"
    assert first["stored_count"] == 1
    assert second["duplicate_count"] == 1
    assert len(store.records()) == 1
    assert first["execution_authority"] is False
    assert first["broker_submission_attempted"] is False


def test_ingestion_isolates_invalid_provider_items(tmp_path) -> None:
    invalid = item("Missing URL")
    invalid.pop("source_url")

    def fetch(url, headers, timeout):
        return {"items": [item(), invalid]}, {}

    provider = JsonNewsProvider(
        name="Test Wire",
        endpoint="https://news.example.test/v1/articles",
        fetch_json=fetch,
    )
    result = collect_governed_news(
        provider=provider,
        store=NewsEvidenceStore(tmp_path),
        symbols=["MSFT"],
        now=NOW,
    )

    assert result["status"] == "partial"
    assert result["stored_count"] == 1
    assert result["rejected_count"] == 1
    assert result["failures"][0]["stage"] == "item:1"


def test_provider_failure_does_not_raise_or_write(tmp_path) -> None:
    def fetch(url, headers, timeout):
        raise TimeoutError("provider timed out")

    provider = JsonNewsProvider(
        name="Test Wire",
        endpoint="https://news.example.test/v1/articles",
        fetch_json=fetch,
    )
    store = NewsEvidenceStore(tmp_path)
    result = collect_governed_news(
        provider=provider,
        store=store,
        symbols=["MSFT"],
        now=NOW,
    )

    assert result["status"] == "provider-failed"
    assert result["stored_count"] == 0
    assert store.records() == []
    assert result["paper_only"] is True


def test_provider_requires_https() -> None:
    try:
        JsonNewsProvider(name="Unsafe", endpoint="http://example.test/news")
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("HTTP provider endpoint was accepted")
