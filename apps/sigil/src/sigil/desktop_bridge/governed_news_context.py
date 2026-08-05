"""Advisory-only governed news context for production research cycles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .governed_news import VALID_SENTIMENTS
from .governed_news_store import NewsEvidenceStore

MAXIMUM_NEWS_AGE = timedelta(hours=72)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(UTC)


def production_news_context(
    store: NewsEvidenceStore,
    symbols: tuple[str, ...],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Project news evidence without changing candidate eligibility or authority."""

    normalized_symbols = tuple(
        sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    )

    if len(normalized_symbols) > 25:
        raise ValueError("production news context is bounded to 25 symbols")

    records = store.records()
    by_symbol: dict[str, dict[str, Any]] = {}

    total_matching_headlines = 0
    stale_headlines = 0
    invalid_timestamp_headlines = 0

    for symbol in normalized_symbols:
        matching = [
            item
            for item in records
            if symbol in {str(value).strip().upper() for value in item.get("symbols", [])}
        ]

        usable: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []

        for item in matching:
            published_at = _parse_timestamp(item.get("published_at"))
            if published_at is None or published_at > now.astimezone(UTC):
                invalid.append(item)
            elif now.astimezone(UTC) - published_at > MAXIMUM_NEWS_AGE:
                stale.append(item)
            else:
                usable.append(item)

        total_matching_headlines += len(matching)
        stale_headlines += len(stale)
        invalid_timestamp_headlines += len(invalid)

        sentiment_counts = {
            sentiment: sum(
                1 for item in usable if str(item.get("sentiment", "unknown")).lower() == sentiment
            )
            for sentiment in sorted(VALID_SENTIMENTS)
        }

        sources = sorted(
            {
                str(item.get("source", "")).strip()
                for item in usable
                if str(item.get("source", "")).strip()
            }
        )

        directional = {
            sentiment for sentiment in ("bullish", "bearish") if sentiment_counts[sentiment] > 0
        }

        if len(directional) > 1:
            agreement = "conflicting"
        elif len(sources) >= 2 and directional:
            agreement = "corroborated"
        elif len(sources) >= 2:
            agreement = "multi-source-neutral"
        elif usable:
            agreement = "single-source"
        else:
            agreement = "no-current-evidence"

        latest = max(
            usable,
            key=lambda item: (
                str(item.get("published_at", "")),
                str(item.get("evidence_identity", "")),
            ),
            default=None,
        )

        risk_flags: list[str] = []
        if not matching:
            risk_flags.append("news_evidence_missing")
        elif not usable:
            risk_flags.append("news_evidence_not_current")

        if invalid:
            risk_flags.append("news_timestamp_invalid")

        if stale:
            risk_flags.append("stale_news_present")

        if agreement == "conflicting":
            risk_flags.append("directional_news_conflict")

        if sentiment_counts["bearish"] > sentiment_counts["bullish"]:
            risk_flags.append("bearish_news_majority")

        by_symbol[symbol] = {
            "status": "ready" if usable else "unavailable",
            "headline_count": len(matching),
            "current_headline_count": len(usable),
            "stale_headline_count": len(stale),
            "invalid_timestamp_count": len(invalid),
            "sources": sources,
            "source_count": len(sources),
            "sentiment_counts": sentiment_counts,
            "agreement": agreement,
            "latest_published_at": (
                latest.get("published_at") if isinstance(latest, dict) else None
            ),
            "latest_evidence_identity": (
                latest.get("evidence_identity") if isinstance(latest, dict) else None
            ),
            "risk_flags": sorted(set(risk_flags)),
            "advisory_only": True,
            "affects_candidate_eligibility": False,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

    current_symbols = sum(
        1 for context in by_symbol.values() if context["current_headline_count"] > 0
    )

    return {
        "status": (
            "ready"
            if current_symbols
            else "empty"
            if not total_matching_headlines
            else "stale_or_invalid"
        ),
        "symbols_requested": list(normalized_symbols),
        "symbol_count": len(normalized_symbols),
        "symbols_with_current_news": current_symbols,
        "matching_headline_count": total_matching_headlines,
        "stale_headline_count": stale_headlines,
        "invalid_timestamp_count": invalid_timestamp_headlines,
        "maximum_age_hours": int(MAXIMUM_NEWS_AGE.total_seconds() // 3600),
        "by_symbol": by_symbol,
        "advisory_only": True,
        "affects_candidate_eligibility": False,
        "execution_authority": False,
        "broker_submission_available": False,
        "broker_submission_attempted": False,
        "paper_only": True,
    }
