"""Controlled Alpaca News collection command for Sigil."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .governed_news_alpaca import AlpacaNewsProvider, alpaca_news_enabled
from .governed_news_ingestion import collect_governed_news
from .governed_news_store import NewsEvidenceStore


def collect_alpaca_news(
    *,
    store: NewsEvidenceStore,
    symbols: list[str],
    now: datetime | None = None,
    provider: AlpacaNewsProvider | None = None,
) -> dict[str, Any]:
    if not alpaca_news_enabled():
        return {
            "status": "disabled",
            "provider": "Alpaca News",
            "requested_symbols": sorted(
                {
                    str(symbol).strip().upper()
                    for symbol in symbols
                    if str(symbol).strip()
                }
            ),
            "received_count": 0,
            "stored_count": 0,
            "duplicate_count": 0,
            "rejected_count": 0,
            "failures": [],
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

    return collect_governed_news(
        provider=provider or AlpacaNewsProvider(),
        store=store,
        symbols=symbols,
        now=now,
    )
