"""Structured desktop bridge operations for governed Alpaca paper execution."""

from __future__ import annotations

import os
from typing import Any

from sigil.asset_catalog.catalog import _configured_credentials
from sigil.autonomous_paper import (
    AlpacaPaperClient,
    GovernedPaperExecutionService,
    PaperExecutionStore,
)

from .runtime import _state_directory


def _service() -> GovernedPaperExecutionService:
    key, secret = _configured_credentials()
    key = key or os.environ.get("ALPACA_API_KEY")
    secret = secret or os.environ.get("ALPACA_SECRET_KEY")
    return GovernedPaperExecutionService(
        PaperExecutionStore(_state_directory()),
        AlpacaPaperClient(key, secret),
    )


def paper_execution_status() -> dict[str, Any]:
    status = _service().status()
    from .production_research import _service as _research_service

    research = _research_service().status()["progress"]
    total_eligible = int(research.get("total_eligible", 0))
    current_cursor = int(research.get("current_cursor", 0))
    progress = dict(status["progress"])
    progress.update(
        {
            "state": research.get("state", "collecting_market_data"),
            "scheduler_state": "scanning",
            "current_cursor": current_cursor,
            "current_batch": int(research.get("current_batch", 0)),
            "symbols_completed_cycle": current_cursor,
            "total_eligible_symbols": total_eligible,
            "coverage_percent": round((current_cursor / total_eligible) * 100, 2)
            if total_eligible
            else 0.0,
            "symbols_in_batch": list(research.get("symbols_in_batch", [])),
            "last_completed_symbol": (research.get("symbols_in_batch") or [None])[-1],
            "last_successful_research_at": research.get("last_completed_research"),
            "candidates_produced": int(research.get("candidates_produced", 0)),
            "proposals_produced": int(research.get("proposals_generated", 0)),
            "leading_rejection_reasons": dict(research.get("leading_rejection_reasons", {})),
            "next_cycle_at": research.get("next_cycle_at"),
        }
    )
    status["progress"] = progress
    return status


def paper_execution_activate() -> dict[str, Any]:
    from .production_research import _service as _research_service

    research = _research_service().status()
    if (
        research["shadow_mode"]
        or not research["paper_promotion_approved"]
        or not research["promotion"]["ready"]
    ):
        raise ValueError(
            "paper execution activation requires completed shadow promotion readiness"
        )
    return _service().activate()


def paper_execution_deactivate() -> dict[str, Any]:
    return _service().deactivate()


def paper_execution_pause() -> dict[str, Any]:
    return _service().pause()


def paper_execution_resume() -> dict[str, Any]:
    return _service().resume()


def paper_execution_emergency_stop() -> dict[str, Any]:
    return _service().pause(emergency=True)


def reconcile_paper_orders() -> dict[str, Any]:
    return _service().reconcile()


def paper_execution_collection(
    kind: str, payload: object = None
) -> dict[str, Any]:
    values = payload if isinstance(payload, dict) else {}
    return _service().recent(
        kind,
        offset=values.get("offset", 0),
        limit=values.get("limit", 50),
    )
