"""Bounded runtime projection and read-only instrument search."""

from __future__ import annotations

from .engine import TARGET_MAXIMUM, TARGET_MINIMUM
from .models import UniverseSnapshot, UniverseValidationError

MAX_QUERY_LIMIT = 100


def universe_projection(snapshot: UniverseSnapshot) -> dict[str, object]:
    instruments = snapshot.instruments
    validated = tuple(item for item in instruments if item.reconciliation_status == "validated")
    return {
        "schema_version": snapshot.schema_version,
        "policy_version": snapshot.policy_version,
        "snapshot_id": snapshot.snapshot_id,
        "generated_at": snapshot.generated_at,
        "source_record_count": snapshot.source_record_count,
        "master_count": len(validated),
        "broker_tradable_count": sum(item.broker_tradable for item in validated),
        "actively_researched_count": sum(item.actively_researched for item in validated),
        "proposal_eligible_count": sum(item.proposal_eligible for item in validated),
        "conflicted_count": sum(item.reconciliation_status == "conflicted" for item in instruments),
        "excluded_count": sum(item.reconciliation_status == "excluded" for item in instruments),
        "target_minimum": TARGET_MINIMUM,
        "target_maximum": TARGET_MAXIMUM,
        "target_capacity_validated": TARGET_MINIMUM <= len(validated) <= TARGET_MAXIMUM,
        "broker_submission_available": False,
        "execution_authorized": False,
    }


def search_instruments(
    snapshot: UniverseSnapshot,
    *,
    query: str = "",
    universe: str = "master",
    asset_class: str | None = None,
    lifecycle_status: str | None = None,
    monitoring_tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    if universe not in {"master", "broker_tradable", "actively_researched", "proposal_eligible", "excluded"}:
        raise UniverseValidationError("universe filter is invalid")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise UniverseValidationError("limit must be between 1 and 100")
    if isinstance(offset, bool) or offset < 0:
        raise UniverseValidationError("offset must be non-negative")
    needle = query.strip().casefold()
    rows = []
    for item in snapshot.instruments:
        membership = {
            "master": item.reconciliation_status == "validated",
            "broker_tradable": item.broker_tradable,
            "actively_researched": item.actively_researched,
            "proposal_eligible": item.proposal_eligible,
            "excluded": item.reconciliation_status != "validated",
        }
        if not membership[universe]:
            continue
        if asset_class and item.asset_class != asset_class:
            continue
        if lifecycle_status and item.lifecycle_status != lifecycle_status:
            continue
        if monitoring_tier and item.monitoring_tier != monitoring_tier:
            continue
        searchable = " ".join((item.symbol, item.name, item.exchange, *item.aliases)).casefold()
        if needle and needle not in searchable:
            continue
        rows.append(item)
    rows.sort(key=lambda item: (item.symbol, item.exchange, item.instrument_id))
    return {
        "query": query.strip(),
        "universe": universe,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(rows),
        "results": [item.to_dict() for item in rows[offset : offset + limit]],
        "broker_submission_available": False,
        "execution_authorized": False,
    }
