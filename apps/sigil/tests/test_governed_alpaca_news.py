from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from sigil.desktop_bridge import governed_news_alpaca
from sigil.desktop_bridge.governed_news_alpaca import (
    ALPACA_ENABLED_ENV,
    ALPACA_KEY_ENV,
    ALPACA_SECRET_ENV,
    AlpacaNewsProvider,
)
from sigil.desktop_bridge.governed_news_alpaca_collection import collect_alpaca_news
from sigil.desktop_bridge.governed_news_store import NewsEvidenceStore

NOW = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)


def alpaca_response() -> dict[str, object]:
    return {
        "news": [
            {
                "id": 123,
                "headline": "Microsoft expands cloud capacity",
                "summary": "The company announced additional capacity.",
                "author": "Example Author",
                "created_at": "2026-07-30T03:55:00Z",
                "updated_at": "2026-07-30T03:56:00Z",
                "url": "https://example.com/microsoft-cloud",
                "symbols": ["MSFT"],
                "source": "Example Wire",
            }
        ],
        "next_page_token": None,
    }


def credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        lambda: {
            "SIGIL_ALPACA_API_KEY_ID": "test-key",
            "SIGIL_ALPACA_API_SECRET_KEY": "test-secret",
        },
    )


def test_alpaca_provider_maps_official_response(monkeypatch) -> None:
    credentials(monkeypatch)
    captured: dict[str, object] = {}

    def fetch(url, headers, timeout):
        captured.update(url=url, headers=dict(headers), timeout=timeout)
        return alpaca_response(), {
            "X-RateLimit-Limit": "200",
            "X-RateLimit-Remaining": "199",
        }

    provider = AlpacaNewsProvider(limit=5, lookback_minutes=30, fetch_json=fetch)
    batch = provider.collect(["msft"])

    parsed = urlparse(str(captured["url"]))
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "data.alpaca.markets"
    assert query["symbols"] == ["MSFT"]
    assert query["sort"] == ["desc"]
    assert query["limit"] == ["5"]
    assert captured["headers"]["APCA-API-KEY-ID"] == "test-key"
    assert captured["headers"]["APCA-API-SECRET-KEY"] == "test-secret"
    assert batch.items[0]["headline"] == "Microsoft expands cloud capacity"
    assert batch.items[0]["sentiment"] == "unknown"
    assert batch.items[0]["confidence"] == "0"
    assert batch.rate_limit["remaining"] == 199


def test_alpaca_provider_requires_both_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        dict,
    )

    for name in (
        ALPACA_KEY_ENV,
        ALPACA_SECRET_ENV,
        "SIGIL_ALPACA_API_KEY_ID",
        "SIGIL_ALPACA_API_SECRET_KEY",
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    provider = AlpacaNewsProvider(fetch_json=lambda *_: (alpaca_response(), {}))

    try:
        provider.collect(["MSFT"])
    except RuntimeError as error:
        assert "complete key/secret pair" in str(error)
    else:
        raise AssertionError("Alpaca provider accepted missing credentials")


def test_collection_is_disabled_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(ALPACA_ENABLED_ENV, raising=False)
    result = collect_alpaca_news(
        store=NewsEvidenceStore(tmp_path),
        symbols=["MSFT"],
        now=NOW,
    )

    assert result["status"] == "disabled"
    assert result["stored_count"] == 0
    assert result["execution_authority"] is False
    assert result["broker_submission_attempted"] is False


def test_enabled_collection_writes_governed_evidence(monkeypatch, tmp_path) -> None:
    credentials(monkeypatch)
    monkeypatch.setenv(ALPACA_ENABLED_ENV, "true")

    provider = AlpacaNewsProvider(fetch_json=lambda *_: (alpaca_response(), {}))
    store = NewsEvidenceStore(tmp_path)
    result = collect_alpaca_news(
        store=store,
        symbols=["MSFT"],
        now=NOW,
        provider=provider,
    )

    assert result["status"] == "complete"
    assert result["stored_count"] == 1
    assert len(store.records()) == 1
    record = store.records()[0]
    assert record["source"] == "Example Wire"
    assert record["execution_authority"] is False
    assert record["broker_submission_attempted"] is False


def test_alpaca_limits_are_bounded() -> None:
    for invalid in (0, 51):
        try:
            AlpacaNewsProvider(limit=invalid)
        except ValueError as error:
            assert "between 1 and 50" in str(error)
        else:
            raise AssertionError(f"invalid Alpaca limit {invalid} was accepted")
