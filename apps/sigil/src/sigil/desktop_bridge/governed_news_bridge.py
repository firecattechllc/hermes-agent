"""Desktop bridge entry points for governed news intelligence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .governed_news import advisory_summary
from .governed_news_store import NewsEvidenceStore
from .runtime import _state_directory


def _store() -> NewsEvidenceStore:
    return NewsEvidenceStore(_state_directory())


def governed_news_status() -> dict[str, Any]:
    return _store().projection()


def governed_news_ingest(payload: object, *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    return _store().ingest(
        payload,
        received_at=observed_at.isoformat().replace("+00:00", "Z"),
    )


def governed_news_timeline(symbol: object) -> dict[str, Any]:
    return _store().symbol_timeline(symbol)


def governed_news_advisory_summary() -> dict[str, Any]:
    return advisory_summary(_store().records())


def governed_news_collect(
    provider: object,
    symbols: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    from .governed_news_ingestion import collect_governed_news

    return collect_governed_news(
        provider=provider,
        store=_store(),
        symbols=symbols,
        now=now,
    )

def governed_alpaca_news_collect(
    symbols: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    from .governed_news_alpaca_collection import collect_alpaca_news

    return collect_alpaca_news(
        store=_store(),
        symbols=symbols,
        now=now,
    )

