"""Read-only Alpha 1.5 market-universe projection for Mission Control."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sigil.market_universe import (
    SourceInstrument,
    UniverseValidationError,
    reconcile_sources,
    search_instruments,
    universe_projection,
)

from .universe import US_LISTED_SCREENING_UNIVERSE

CATALOG_OBSERVED_AT = "2026-07-27T00:00:00Z"


@lru_cache(maxsize=1)
def _snapshot():
    records = tuple(
        SourceInstrument(
            source_id="SIGIL_SEED",
            source_record_id=f"SEED_{item['symbol']}",
            observed_at=CATALOG_OBSERVED_AT,
            symbol=item["symbol"],
            name=item["name"],
            exchange="US",
            asset_class="equity",
            status="active",
            broker_tradable=True,
            actively_researched=True,
            proposal_eligible=True,
            aliases=(item["symbol"],),
            sector=item["sector"],
        )
        for item in US_LISTED_SCREENING_UNIVERSE
    )
    return reconcile_sources(records, generated_at=CATALOG_OBSERVED_AT)


def market_universe_status() -> dict[str, object]:
    projection = universe_projection(_snapshot())
    projection.update(
        {
            "catalog_source": "Sigil bounded Alpha 1.4 seed catalog",
            "catalog_scope": "12 validated demonstration equities",
            "capacity_certification": "deterministic synthetic suite validates 10,000 source records",
            "coverage_limitation": (
                "Real 8,000–12,000 instrument coverage requires licensed or "
                "credentialed provider asset catalogs; synthetic records are "
                "never projected as real instruments."
            ),
        }
    )
    return projection


def market_universe_search(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise UniverseValidationError("market universe query requires a payload object")
    allowed = {
        "query", "universe", "asset_class", "lifecycle_status",
        "monitoring_tier", "limit", "offset",
    }
    if set(payload) - allowed:
        raise UniverseValidationError("market universe query contains unsupported filters")
    return search_instruments(
        _snapshot(),
        query=str(payload.get("query", "")),
        universe=str(payload.get("universe", "master")),
        asset_class=payload.get("asset_class"),
        lifecycle_status=payload.get("lifecycle_status"),
        monitoring_tier=payload.get("monitoring_tier"),
        limit=payload.get("limit", 50),
        offset=payload.get("offset", 0),
    )
