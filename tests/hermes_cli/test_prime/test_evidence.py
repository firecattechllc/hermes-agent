from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from hermes_cli.prime.evidence import (
    EvidenceRecord,
    EvidenceStorageError,
    ExternalEvidenceLink,
    ExternalEvidenceSystem,
    PrimeEvidenceStore,
    SensitivityTier,
)


def _now() -> int:
    return int(time.time())


def test_evidence_id_is_content_addressed_and_deterministic() -> None:
    now = _now()
    a = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_x",
        provenance="native:x",
        timestamp=now,
        redacted_summary="registered",
    )
    b = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_x",
        provenance="native:x",
        timestamp=now,
        redacted_summary="registered",
    )
    assert a.evidence_id == b.evidence_id
    assert a.content_hash == b.content_hash


def test_different_content_produces_different_evidence_id() -> None:
    now = _now()
    a = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_x",
        provenance="native:x",
        timestamp=now,
        redacted_summary="registered",
    )
    b = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_y",  # different subject
        provenance="native:y",
        timestamp=now,
        redacted_summary="registered",
    )
    assert a.evidence_id != b.evidence_id


def test_unsupported_schema_version_rejected_at_construction() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        EvidenceRecord(
            evidence_id="pevd_x",
            schema_version=99,
            kind="prime_identity_registered",
            producer_identity_id="prime",
            provenance="native:x",
            timestamp=now,
            redacted_summary="x",
            content_hash="0" * 64,
        )


def test_external_evidence_link_references_existing_stores_without_copying() -> None:
    link = ExternalEvidenceLink(
        system=ExternalEvidenceSystem.SIGIL_CERTIFICATION,
        reference="certification/claude-review/sigil-v3.6-independent-review.md",
        content_hash="a" * 64,
    )
    record = EvidenceRecord.build(
        kind="prime_fleet_certified",
        producer_identity_id="prime",
        subject_identity_id=None,
        provenance="prime_fleet_certification",
        timestamp=_now(),
        redacted_summary="certified",
        external_links=(link,),
        sensitivity=SensitivityTier.INTERNAL,
    )
    assert record.external_links[0].system == ExternalEvidenceSystem.SIGIL_CERTIFICATION


def test_store_is_append_only_and_hash_chained(tmp_path: Path) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path)
    now = _now()
    first = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_x",
        provenance="native:x",
        timestamp=now,
        redacted_summary="one",
    )
    second = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_y",
        provenance="native:y",
        timestamp=now,
        redacted_summary="two",
    )
    store.append(first)
    store.append(second)

    records = store.read_all()
    assert len(records) == 2
    assert records[0]["sequence"] == 1
    assert records[1]["sequence"] == 2
    assert records[1]["previous_record_hash"] == records[0]["entry_hash"]
    assert store.verify_chain() is True


def test_store_detects_tampered_entry(tmp_path: Path) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path)
    now = _now()
    record = EvidenceRecord.build(
        kind="prime_identity_registered",
        producer_identity_id="prime",
        subject_identity_id="fid_node_x",
        provenance="native:x",
        timestamp=now,
        redacted_summary="one",
    )
    store.append(record)

    # Tamper with the on-disk journal directly, simulating an attacker
    # editing history without recomputing the hash chain.
    lines = store.evidence_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["record"]["redacted_summary"] = "tampered"
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    store.evidence_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceStorageError):
        store.read_all()


def test_store_detects_broken_linkage(tmp_path: Path) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path)
    now = _now()
    for i in range(3):
        store.append(
            EvidenceRecord.build(
                kind="prime_identity_registered",
                producer_identity_id="prime",
                subject_identity_id=f"fid_node_{i}",
                provenance=f"native:{i}",
                timestamp=now,
                redacted_summary=f"record {i}",
            )
        )

    lines = store.evidence_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["previous_record_hash"] = "f" * 64  # break the chain
    lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    store.evidence_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(EvidenceStorageError):
        store.read_all()


def test_store_rejects_relative_state_root() -> None:
    with pytest.raises(EvidenceStorageError):
        PrimeEvidenceStore(state_root=Path("relative/path"))


def test_empty_store_reads_cleanly(tmp_path: Path) -> None:
    store = PrimeEvidenceStore(state_root=tmp_path)
    assert store.read_all() == ()
    assert store.verify_chain() is True
