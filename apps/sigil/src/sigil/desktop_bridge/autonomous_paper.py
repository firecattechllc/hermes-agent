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
    return _service().status()


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
