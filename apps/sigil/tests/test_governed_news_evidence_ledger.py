import json
from datetime import UTC, datetime

import pytest

from sigil.desktop_bridge.governed_news import advisory_summary
from sigil.desktop_bridge.governed_news_bridge import (
    governed_news_advisory_summary,
    governed_news_ingest,
    governed_news_status,
    governed_news_timeline,
)
from sigil.desktop_bridge.governed_news_store import NewsEvidenceStore


NOW = datetime(2026, 7, 30, 3, 15, tzinfo=UTC)


def payload(
    *,
    headline: str,
    source: str = "Example Wire",
    symbol: str = "MSFT",
    sentiment: str = "bullish",
) -> dict[str, object]:
    return {
        "headline": headline,
        "summary": "Governed evidence summary.",
        "source": source,
        "source_url": f"https://example.com/{headline.lower().replace(' ', '-')}",
        "published_at": "2026-07-30T03:10:00Z",
        "symbols": [symbol],
        "sentiment": sentiment,
        "confidence": "0.8",
    }


def test_store_appends_hash_chained_evidence_and_deduplicates(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)
    first = store.ingest(payload(headline="Guidance raised"), received_at="2026-07-30T03:15:00Z")
    duplicate = store.ingest(
        payload(headline="Guidance raised"),
        received_at="2026-07-30T03:15:00Z",
    )

    assert first["status"] == "stored"
    assert duplicate["status"] == "duplicate"
    assert len(store.records()) == 1
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert len(json.loads(lines[0])["sha256"]) == 64


def test_store_detects_tampered_ledger(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)
    store.ingest(payload(headline="Guidance raised"), received_at="2026-07-30T03:15:00Z")
    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    envelope["record"]["headline"] = "Tampered"
    store.path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="integrity validation failed"):
        store.records()


def test_symbol_timeline_is_bounded_and_advisory(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)
    store.ingest(payload(headline="First", symbol="NVDA"), received_at="2026-07-30T03:15:00Z")
    store.ingest(payload(headline="Second", symbol="MSFT"), received_at="2026-07-30T03:16:00Z")

    timeline = store.symbol_timeline("nvda")

    assert timeline["symbol"] == "NVDA"
    assert timeline["headline_count"] == 1
    assert timeline["execution_authority"] is False
    assert timeline["broker_submission_attempted"] is False


def test_consensus_distinguishes_corroboration_and_conflict(tmp_path) -> None:
    store = NewsEvidenceStore(tmp_path)
    store.ingest(payload(headline="One", source="Wire A"), received_at="2026-07-30T03:15:00Z")
    store.ingest(payload(headline="Two", source="Wire B"), received_at="2026-07-30T03:16:00Z")

    summary = advisory_summary(store.records())
    assert summary["symbol_consensus"]["MSFT"]["agreement"] == "corroborated"

    store.ingest(
        payload(headline="Three", source="Wire C", sentiment="bearish"),
        received_at="2026-07-30T03:17:00Z",
    )
    summary = advisory_summary(store.records())
    assert summary["symbol_consensus"]["MSFT"]["agreement"] == "conflicting"
    assert summary["execution_authority"] is False


def test_bridge_persists_and_projects_news(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path))

    stored = governed_news_ingest(payload(headline="Bridge evidence"), now=NOW)
    status = governed_news_status()
    timeline = governed_news_timeline("MSFT")
    summary = governed_news_advisory_summary()

    assert stored["status"] == "stored"
    assert status["headline_count"] == 1
    assert timeline["headline_count"] == 1
    assert summary["advisory_only"] is True
    assert summary["broker_submission_attempted"] is False
