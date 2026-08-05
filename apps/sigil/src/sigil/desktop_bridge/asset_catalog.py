"""Bounded desktop bridge for the governed Alpaca paper asset catalog."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sigil.asset_catalog import AssetCatalogService, ResearchUniverseScheduler

from .runtime import _state_directory


def _service() -> AssetCatalogService:
    return AssetCatalogService(_state_directory())


def asset_catalog_status() -> dict[str, Any]:
    return _service().status()


def asset_catalog_refresh() -> dict[str, Any]:
    return _service().refresh()


def asset_catalog_snapshot(payload: object = None) -> dict[str, Any]:
    values = payload if isinstance(payload, dict) else {}
    return _service().snapshot(
        offset=values.get("offset", 0),
        limit=values.get("limit", 50),
    )


def asset_catalog_statistics() -> dict[str, Any]:
    return _service().status()


def asset_catalog_sample() -> dict[str, Any]:
    return _service().snapshot(offset=0, limit=12)


def asset_catalog_exclusions() -> dict[str, Any]:
    return _service().exclusions()


def research_universe_status(*, advance: bool = False) -> dict[str, Any]:
    state, snapshot, _metadata = _service().store.load()
    if snapshot is None:
        return {
            "environment": "paper",
            "broker_submission": False,
            "revision": "catalog-unavailable",
            "status": state,
            "catalog_total": 0,
            "proposal_eligible": 0,
            "research_queued": 0,
            "research_completed_current_cycle": 0,
            "research_deferred": 0,
            "research_failed": 0,
            "research_coverage_percent": 0.0,
            "current_batch": 0,
            "next_cursor": 0,
            "last_completed_symbol": None,
            "cycle_started_at": None,
            "cycle_completed_at": None,
            "batch_size": 25,
        }
    scheduler = ResearchUniverseScheduler(Path(_state_directory()))
    return scheduler.next_batch(snapshot) if advance else scheduler.status(snapshot)
