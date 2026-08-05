from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sigil.market_universe import (
    SourceInstrument,
    UniverseStore,
    UniverseValidationError,
    reconcile_sources,
    search_instruments,
    universe_projection,
)

NOW = "2026-07-27T12:00:00Z"


def source(index: int = 1, **changes: object) -> SourceInstrument:
    record = SourceInstrument(
        source_id="NASDAQ",
        source_record_id=f"ROW_{index:05d}",
        observed_at=NOW,
        symbol=f"T{index:05d}",
        name=f"Synthetic Company {index:05d}",
        exchange="XNAS",
        asset_class="equity",
        status="active",
        broker_tradable=True,
        actively_researched=index % 2 == 0,
        proposal_eligible=index % 4 == 0,
        aliases=(f"OLD{index:05d}",),
        figi=f"BBG{index:09d}",
        sector="Technology",
        industry="Software",
    )
    return replace(record, **changes)


def test_normalizes_identity_aliases_classification_and_tiers() -> None:
    snapshot = reconcile_sources(
        [source(4, symbol=" ab.c ", aliases=("abc", "AB.C"))],
        generated_at=NOW,
    )
    item = snapshot.instruments[0]
    assert item.symbol == "AB.C"
    assert item.aliases == ("AB.C", "ABC")
    assert item.instrument_id.startswith("SIGIL-")
    assert item.asset_class == "equity"
    assert item.lifecycle_status == "active"
    assert item.monitoring_tier == "proposal_eligible"
    assert item.reconciliation_status == "validated"
    assert item.evidence[0].digest


def test_conflicts_and_lifecycle_exclusions_fail_closed() -> None:
    conflicted = reconcile_sources(
        [
            source(1, source_id="NASDAQ", source_record_id="ONE"),
            source(1, source_id="BROKER", source_record_id="TWO", symbol="OTHER"),
            source(2, status="delisted", proposal_eligible=True),
        ],
        generated_at=NOW,
    )
    conflict = next(item for item in conflicted.instruments if item.figi == "BBG000000001")
    delisted = next(item for item in conflicted.instruments if item.figi == "BBG000000002")
    assert conflict.reconciliation_status == "conflicted"
    assert conflict.conflict_fields == ("symbol",)
    assert conflict.monitoring_tier == "excluded"
    assert conflict.proposal_eligible is False
    assert delisted.lifecycle_status == "delisted"
    assert "lifecycle_delisted" in delisted.exclusion_reasons
    assert delisted.proposal_eligible is False


def test_universes_remain_separate_and_search_is_bounded() -> None:
    snapshot = reconcile_sources(
        [
            source(1, broker_tradable=False, actively_researched=False),
            source(2, actively_researched=False),
            source(3, actively_researched=True),
            source(4),
        ],
        generated_at=NOW,
    )
    projection = universe_projection(snapshot)
    assert projection["master_count"] == 4
    assert projection["broker_tradable_count"] == 3
    assert projection["actively_researched_count"] == 2
    assert projection["proposal_eligible_count"] == 1
    result = search_instruments(snapshot, query="company", universe="master", limit=2)
    assert result["total"] == 4
    assert len(result["results"]) == 2
    assert result["has_more"] is True
    with pytest.raises(UniverseValidationError, match="between 1 and 100"):
        search_instruments(snapshot, limit=101)


def test_checksummed_persistence_round_trip_and_tamper_detection(tmp_path) -> None:
    snapshot = reconcile_sources([source(4)], generated_at=NOW)
    store = UniverseStore((tmp_path / "universe.json").resolve())
    store.write(snapshot)
    assert store.read() == snapshot
    value = json.loads(store.path.read_text())
    value["payload"]["source_record_count"] = 99
    store.path.write_text(json.dumps(value))
    with pytest.raises(UniverseValidationError, match="checksum"):
        store.read()


def test_rejects_duplicate_evidence_and_invalid_geography() -> None:
    with pytest.raises(UniverseValidationError, match="duplicate source"):
        reconcile_sources([source(1), source(1)], generated_at=NOW)
    with pytest.raises(UniverseValidationError, match="outside policy"):
        reconcile_sources([source(1, country="GB", currency="GBP")], generated_at=NOW)


def test_deterministic_synthetic_capacity_with_10_000_source_records() -> None:
    records = [source(index) for index in range(1, 10_001)]
    forward = reconcile_sources(records, generated_at=NOW)
    reverse = reconcile_sources(reversed(records), generated_at=NOW)
    assert forward.snapshot_id == reverse.snapshot_id
    assert len(forward.instruments) == 10_000
    projection = universe_projection(forward)
    assert projection["master_count"] == 10_000
    assert projection["target_capacity_validated"] is True
    assert projection["broker_tradable_count"] == 10_000
    assert projection["actively_researched_count"] == 5_000
    assert projection["proposal_eligible_count"] == 2_500
