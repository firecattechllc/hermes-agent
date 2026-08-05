"""Failure-isolated ingestion of provider news into governed evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .governed_news_providers import NewsProvider, ProviderBatch
from .governed_news_store import NewsEvidenceStore

MAX_FAILURE_MESSAGE_LENGTH = 500


def _timestamp(now: datetime | None) -> str:
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    return observed_at.isoformat().replace("+00:00", "Z")


def _failure(provider: str, stage: str, error: Exception) -> dict[str, str]:
    message = str(error).strip() or error.__class__.__name__
    return {
        "provider": provider,
        "stage": stage,
        "error_type": error.__class__.__name__,
        "message": message[:MAX_FAILURE_MESSAGE_LENGTH],
    }


def _stage_item(batch: ProviderBatch, item: dict[str, Any]) -> dict[str, Any]:
    staged = dict(item)
    staged.setdefault("source", batch.provider)
    if not str(staged.get("source_url", "")).strip():
        raise ValueError("provider item source_url is required")
    staged.pop("execution_authority", None)
    staged.pop("broker_submission", None)
    return staged


def collect_governed_news(
    *,
    provider: NewsProvider,
    store: NewsEvidenceStore,
    symbols: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect one provider batch and persist valid records independently."""
    provider_name = str(getattr(provider, "name", "unknown-provider"))
    observed_at = _timestamp(now)
    try:
        batch = provider.collect(symbols)
    except Exception as error:  # noqa: BLE001 - Provider isolation is an explicit runtime boundary.
        return {
            "status": "provider-failed",
            "provider": provider_name,
            "requested_symbols": sorted(set(symbols)),
            "received_count": 0,
            "stored_count": 0,
            "duplicate_count": 0,
            "rejected_count": 0,
            "failures": [_failure(provider_name, "collect", error)],
            "rate_limit": {"limit": None, "remaining": None, "reset": None},
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

    stored = 0
    duplicates = 0
    failures: list[dict[str, str]] = []
    for index, raw_item in enumerate(batch.items):
        try:
            staged = _stage_item(batch, raw_item)
            result = store.ingest(staged, received_at=observed_at)
        except Exception as error:  # noqa: BLE001 - Malformed provider items are isolated.
            failure = _failure(batch.provider, f"item:{index}", error)
            failures.append(failure)
            continue
        if result["status"] == "stored":
            stored += 1
        elif result["status"] == "duplicate":
            duplicates += 1

    rejected = len(failures)
    if failures and not stored and not duplicates:
        status = "batch-rejected"
    elif failures:
        status = "partial"
    else:
        status = "complete"

    return {
        "status": status,
        "provider": batch.provider,
        "requested_symbols": sorted(
            {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
        ),
        "request_url": batch.request_url,
        "received_count": len(batch.items),
        "stored_count": stored,
        "duplicate_count": duplicates,
        "rejected_count": rejected,
        "failures": failures,
        "rate_limit": batch.rate_limit,
        "news_intelligence": store.projection(),
        "execution_authority": False,
        "broker_submission_attempted": False,
        "paper_only": True,
    }
