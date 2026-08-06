from __future__ import annotations

from pathlib import Path

import pytest

from hermes_docs_worker.evidence import (
    EvidenceFact,
    EvidenceRetentionStore,
    EvidenceSnapshot,
    make_fact,
    make_run_id,
    now_epoch,
)
from hermes_docs_worker.status import StatusValue


def test_evidence_fact_rejects_secret_in_detail() -> None:
    with pytest.raises(ValueError):
        EvidenceFact(
            category="c", label="l", status=StatusValue.VERIFIED,
            detail="api_key=abcdef1234567890", source="s", collected_at=0,
        )


def test_make_fact_redacts_and_never_raises() -> None:
    fact = make_fact(
        category="c", label="l", status=StatusValue.VERIFIED,
        detail="password: hunter2hunter2", source="s", collected_at=0,
    )
    assert "hunter2hunter2" not in fact.detail
    assert "[REDACTED]" in fact.detail


def test_make_fact_falls_back_to_unknown_if_still_secret_after_redaction() -> None:
    # A pathological case: a marker phrase with no clean removable value
    # shape still trips contains_secret() after redact_text() does its
    # pass -- make_fact must fall back rather than raise.
    fact = make_fact(
        category="c", label="l", status=StatusValue.DEPLOYED,
        detail="this text mentions bearer  in a way redact_text won't strip",
        source="s", collected_at=0,
    )
    assert fact.status == StatusValue.UNKNOWN
    assert "bearer" not in fact.detail.lower()
    assert "REDACTED" in fact.detail


def test_snapshot_groups_facts_by_key() -> None:
    facts = (
        EvidenceFact(category="a", label="x", status=StatusValue.VERIFIED, source="s", collected_at=0),
        EvidenceFact(category="a", label="x", status=StatusValue.BLOCKED, source="s2", collected_at=0),
        EvidenceFact(category="a", label="y", status=StatusValue.UNKNOWN, source="s", collected_at=0),
    )
    snapshot = EvidenceSnapshot(run_id="r1", collected_at=0, facts=facts)
    grouped = snapshot.facts_by_key()
    assert len(grouped[("a", "x")]) == 2
    assert len(grouped[("a", "y")]) == 1


def test_make_run_id_is_deterministic_for_a_given_epoch() -> None:
    assert make_run_id(0) == make_run_id(0)
    assert make_run_id(0) != make_run_id(3600)


def test_retention_store_round_trip(tmp_path: Path) -> None:
    store = EvidenceRetentionStore(tmp_path / "state")
    snapshot = EvidenceSnapshot(run_id="r1", collected_at=now_epoch(), facts=())
    store.append(snapshot, retention_days=30, max_files=10)
    assert store.latest().run_id == "r1"
    assert len(store.read_all()) == 1


def test_retention_store_prunes_by_max_files(tmp_path: Path) -> None:
    store = EvidenceRetentionStore(tmp_path / "state")
    base = now_epoch()
    for i in range(5):
        store.append(
            EvidenceSnapshot(run_id=f"r{i}", collected_at=base + i, facts=()),
            retention_days=30, max_files=3,
        )
    records = store.read_all()
    assert len(records) == 3
    assert [r.run_id for r in records] == ["r2", "r3", "r4"]


def test_retention_store_prunes_by_age(tmp_path: Path) -> None:
    import time

    store = EvidenceRetentionStore(tmp_path / "state")
    old = EvidenceSnapshot(run_id="old", collected_at=0, facts=())
    fresh = EvidenceSnapshot(run_id="fresh", collected_at=int(time.time()), facts=())
    store.append(old, retention_days=30, max_files=100)
    store.append(fresh, retention_days=30, max_files=100)
    records = store.read_all()
    assert [r.run_id for r in records] == ["fresh"]
