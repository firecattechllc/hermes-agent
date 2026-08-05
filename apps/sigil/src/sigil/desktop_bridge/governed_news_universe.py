"""Governed rolling news collection across Sigil's eligible asset universe."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sigil.asset_catalog import AssetCatalogService

from .governed_news_alpaca import AlpacaNewsProvider
from .governed_news_ingestion import collect_governed_news
from .governed_news_store import NewsEvidenceStore

SYMBOLS_PER_BATCH = 50
DEFAULT_BATCHES_PER_RUN = 10
MAX_BATCHES_PER_RUN = 20
CURSOR_FILENAME = "governed-news-universe-cursor.json"

ProviderFactory = Callable[[], AlpacaNewsProvider]


def _cursor_path(state_directory: Path) -> Path:
    return state_directory / CURSOR_FILENAME


def _load_cursor(state_directory: Path) -> int:
    path = _cursor_path(state_directory)
    if not path.exists():
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    value = payload.get("cursor") if isinstance(payload, dict) else 0
    return value if isinstance(value, int) and value >= 0 else 0


def _write_cursor(
    state_directory: Path,
    *,
    cursor: int,
    total_symbols: int,
) -> None:
    state_directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "cursor": cursor,
        "total_symbols": total_symbols,
        "paper_only": True,
        "execution_authority": False,
        "broker_submission_attempted": False,
    }

    handle, temporary_name = tempfile.mkstemp(
        prefix=".news-universe-",
        suffix=".json",
        dir=state_directory,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, _cursor_path(state_directory))
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _eligible_symbols(
    catalog_service: AssetCatalogService,
) -> list[str]:
    state, snapshot, _metadata = catalog_service.store.load()
    if snapshot is None:
        raise RuntimeError(f"asset catalog unavailable: {state}")

    return sorted(
        {
            asset.symbol
            for asset in snapshot.normalized_assets
            if asset.proposal_eligible and asset.tradable
        }
    )


def collect_alpaca_universe_news(
    *,
    state_directory: Path,
    batches_per_run: int = DEFAULT_BATCHES_PER_RUN,
    provider_factory: ProviderFactory = AlpacaNewsProvider,
) -> dict[str, Any]:
    if batches_per_run < 1 or batches_per_run > MAX_BATCHES_PER_RUN:
        raise ValueError(f"batches_per_run must be between 1 and {MAX_BATCHES_PER_RUN}")

    catalog_service = AssetCatalogService(state_directory)
    symbols = _eligible_symbols(catalog_service)

    if not symbols:
        return {
            "status": "empty-universe",
            "total_symbols": 0,
            "processed_symbols": 0,
            "next_cursor": 0,
            "cycle_complete": True,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

    starting_cursor = _load_cursor(state_directory)
    if starting_cursor >= len(symbols):
        starting_cursor = 0

    request_capacity = batches_per_run * SYMBOLS_PER_BATCH
    selected = symbols[starting_cursor : starting_cursor + request_capacity]

    if len(selected) < request_capacity and starting_cursor > 0:
        selected.extend(symbols[: request_capacity - len(selected)])

    selected = selected[:request_capacity]
    provider = provider_factory()
    store = NewsEvidenceStore(state_directory)

    batch_results: list[dict[str, Any]] = []
    received_count = 0
    stored_count = 0
    duplicate_count = 0
    rejected_count = 0
    failures: list[dict[str, Any]] = []

    for batch_number, offset in enumerate(
        range(0, len(selected), SYMBOLS_PER_BATCH),
        start=1,
    ):
        batch = selected[offset : offset + SYMBOLS_PER_BATCH]

        try:
            result = collect_governed_news(
                provider=provider,
                store=store,
                symbols=batch,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            failures.append(
                {
                    "batch": batch_number,
                    "symbols": batch,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            )
            batch_results.append(
                {
                    "batch": batch_number,
                    "status": "failed",
                    "symbol_count": len(batch),
                }
            )
            continue

        received_count += int(result.get("received_count", 0))
        stored_count += int(result.get("stored_count", 0))
        duplicate_count += int(result.get("duplicate_count", 0))
        rejected_count += int(result.get("rejected_count", 0))
        failures.extend(result.get("failures", []))

        batch_results.append(
            {
                "batch": batch_number,
                "status": result.get("status", "unknown"),
                "symbol_count": len(batch),
                "received_count": result.get("received_count", 0),
                "stored_count": result.get("stored_count", 0),
                "duplicate_count": result.get("duplicate_count", 0),
                "rejected_count": result.get("rejected_count", 0),
            }
        )

    next_cursor = (starting_cursor + len(selected)) % len(symbols)
    cycle_complete = next_cursor <= starting_cursor and bool(selected)

    _write_cursor(
        state_directory,
        cursor=next_cursor,
        total_symbols=len(symbols),
    )

    if failures and stored_count:
        status = "partial"
    elif failures:
        status = "failed"
    else:
        status = "complete"

    return {
        "status": status,
        "mode": "rolling-governed-universe",
        "total_symbols": len(symbols),
        "starting_cursor": starting_cursor,
        "processed_symbols": len(selected),
        "batch_count": len(batch_results),
        "symbols_per_batch": SYMBOLS_PER_BATCH,
        "next_cursor": next_cursor,
        "coverage_percent": round(
            (next_cursor / len(symbols)) * 100,
            2,
        ),
        "cycle_complete": cycle_complete,
        "received_count": received_count,
        "stored_count": stored_count,
        "duplicate_count": duplicate_count,
        "rejected_count": rejected_count,
        "failures": failures,
        "batches": batch_results,
        "news_intelligence": store.projection(),
        "advisory_only": True,
        "affects_candidate_eligibility": False,
        "execution_authority": False,
        "broker_submission_attempted": False,
        "paper_only": True,
    }
