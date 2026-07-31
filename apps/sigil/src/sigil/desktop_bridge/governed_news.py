"""Deterministic, research-only news intelligence for the Sigil runtime."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

VALID_SENTIMENTS = frozenset({"bullish", "bearish", "neutral", "mixed", "unknown"})
MAX_HEADLINE_LENGTH = 500
MAX_SUMMARY_LENGTH = 2_000
MAX_SYMBOLS = 25


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _confidence(value: object) -> str:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("confidence must be a decimal between 0 and 1") from error
    if not result.is_finite() or result < 0 or result > 1:
        raise ValueError("confidence must be a decimal between 0 and 1")
    return str(result.normalize())


def empty_news_intelligence() -> dict[str, Any]:
    return {
        "status": "empty",
        "mode": "research-only",
        "last_collected_at": None,
        "latest_evidence_identity": None,
        "headline_count": 0,
        "symbol_count": 0,
        "sentiment_counts": {"bullish": 0, "bearish": 0, "neutral": 0, "mixed": 0, "unknown": 0},
        "headlines": [],
        "execution_authority": False,
        "broker_submission_available": False,
        "broker_submission_attempted": False,
        "paper_only": True,
    }


def normalize_news_item(payload: object, *, received_at: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("news payload must be an object")
    if payload.get("execution_authority") is True or payload.get("broker_submission") is True:
        raise ValueError("news intelligence cannot grant execution authority")
    headline = str(payload.get("headline", "")).strip()
    if not headline:
        raise ValueError("headline is required")
    if len(headline) > MAX_HEADLINE_LENGTH:
        raise ValueError("headline is too long")
    source = str(payload.get("source", "")).strip()
    source_url = str(payload.get("source_url", "")).strip()
    if not source or not source_url:
        raise ValueError("source and source_url are required")
    if not source_url.startswith(("https://", "http://")):
        raise ValueError("source_url must be an HTTP(S) URL")
    published_at = _timestamp(payload.get("published_at"), "published_at")
    normalized_received_at = _timestamp(received_at, "received_at")
    sentiment = str(payload.get("sentiment", "unknown")).strip().lower()
    if sentiment not in VALID_SENTIMENTS:
        raise ValueError("sentiment is unsupported")
    raw_symbols = payload.get("symbols", [])
    if not isinstance(raw_symbols, list):
        raise TypeError("symbols must be a list")
    symbols = sorted({str(symbol).strip().upper() for symbol in raw_symbols if str(symbol).strip()})
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError("news item references too many symbols")
    summary = str(payload.get("summary", "")).strip()
    if len(summary) > MAX_SUMMARY_LENGTH:
        raise ValueError("summary is too long")
    body = {
        "headline": headline,
        "summary": summary,
        "source": source,
        "source_url": source_url,
        "published_at": published_at,
        "received_at": normalized_received_at,
        "symbols": symbols,
        "sentiment": sentiment,
        "confidence": _confidence(payload.get("confidence", 0)),
        "paper_only": True,
        "execution_authority": False,
        "broker_submission_attempted": False,
    }
    return {**body, "evidence_identity": _digest(body)}


def build_news_intelligence(items: list[dict[str, Any]]) -> dict[str, Any]:
    projection = empty_news_intelligence()
    if not items:
        return projection
    ordered = sorted(
        items, key=lambda item: (item["published_at"], item["evidence_identity"]), reverse=True
    )
    counts = dict(projection["sentiment_counts"])
    symbols: set[str] = set()
    for item in ordered:
        sentiment = str(item["sentiment"])
        counts[sentiment] = counts.get(sentiment, 0) + 1
        symbols.update(str(symbol) for symbol in item["symbols"])
    projection.update(
        {
            "status": "ready",
            "last_collected_at": max(str(item["received_at"]) for item in ordered),
            "latest_evidence_identity": ordered[0]["evidence_identity"],
            "headline_count": len(ordered),
            "symbol_count": len(symbols),
            "sentiment_counts": counts,
            "headlines": ordered,
        }
    )
    return projection


def _consensus_projection(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe source agreement without converting research into authority."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for symbol in item["symbols"]:
            by_symbol.setdefault(str(symbol), []).append(item)

    result: dict[str, Any] = {}
    directional = {"bullish", "bearish"}
    for symbol, symbol_items in sorted(by_symbol.items()):
        sources = {str(item["source"]).strip().lower() for item in symbol_items}
        sentiments = [str(item["sentiment"]) for item in symbol_items]
        directional_values = {value for value in sentiments if value in directional}
        if len(directional_values) > 1:
            agreement = "conflicting"
        elif len(sources) >= 2 and directional_values:
            agreement = "corroborated"
        elif len(sources) >= 2:
            agreement = "multi-source-neutral"
        else:
            agreement = "single-source"
        result[symbol] = {
            "agreement": agreement,
            "source_count": len(sources),
            "headline_count": len(symbol_items),
            "sentiments": {
                value: sentiments.count(value)
                for value in sorted(VALID_SENTIMENTS)
                if sentiments.count(value)
            },
            "execution_authority": False,
        }
    return result


def advisory_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a deterministic, model-free research summary."""
    projection = build_news_intelligence(items)
    consensus = _consensus_projection(items)
    return {
        "status": projection["status"],
        "headline_count": projection["headline_count"],
        "symbol_count": projection["symbol_count"],
        "sentiment_counts": projection["sentiment_counts"],
        "symbol_consensus": consensus,
        "summary": (
            "No governed news evidence is available."
            if not items
            else (
                f"{projection['headline_count']} governed headlines cover "
                f"{projection['symbol_count']} symbols."
            )
        ),
        "advisory_only": True,
        "execution_authority": False,
        "broker_submission_attempted": False,
        "paper_only": True,
    }
