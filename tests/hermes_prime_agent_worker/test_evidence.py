from __future__ import annotations

import json
import time

from hermes_prime_agent_worker.evidence import EvidenceRecord, EvidenceStore


def _record(**overrides) -> EvidenceRecord:
    defaults = dict(
        category="task",
        action="run_task",
        status="succeeded",
        correlation_id="corr-1",
    )
    defaults.update(overrides)
    return EvidenceRecord.build(**defaults)


def test_append_and_read_all(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    store.append(_record())
    store.append(_record(action="stop_agent"))
    entries = store.read_all()
    assert len(entries) == 2
    assert entries[0]["sequence"] == 1
    assert entries[1]["sequence"] == 2
    assert entries[1]["previous_record_hash"] == entries[0]["entry_hash"]


def test_verify_chain_true_when_untampered(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    for i in range(5):
        store.append(_record(action=f"action-{i}"))
    assert store.verify_chain() is True


def test_verify_chain_detects_tampering(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    store.append(_record())
    store.append(_record(action="second"))

    ledger_path = tmp_path / "evidence" / "evidence.jsonl"
    lines = ledger_path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["record"]["status"] = "denied"  # forge a historical entry
    lines[0] = json.dumps(tampered, sort_keys=True)
    ledger_path.write_text("\n".join(lines) + "\n")

    store2 = EvidenceStore(tmp_path / "evidence")
    assert store2.verify_chain() is False


def test_secrets_are_redacted_in_detail(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    entry = store.append(
        _record(detail="Authorization: Bearer sk-abcdefghijklmnopqrstuvwx")
    )
    assert "sk-abcdefghijklmnopqrstuvwx" not in json.dumps(entry)
    assert "[REDACTED]" in entry["record"]["detail"]


def test_retention_prunes_by_max_files(tmp_path):
    store = EvidenceStore(tmp_path / "evidence", max_files=3, retention_days=365)
    for i in range(10):
        store.append(_record(action=f"action-{i}"))
    entries = store.read_all()
    assert len(entries) == 3
    assert entries[-1]["record"]["action"] == "action-9"


def test_retention_prunes_by_age(tmp_path):
    store = EvidenceStore(tmp_path / "evidence", max_files=1000, retention_days=30)
    old_timestamp = int(time.time()) - (40 * 86_400)
    store.append(_record(**{"correlation_id": "old"}))
    # Manually age the first entry to simulate an old record, then append a
    # fresh one to trigger pruning.
    ledger_path = tmp_path / "evidence" / "evidence.jsonl"
    lines = ledger_path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["record"]["timestamp"] = old_timestamp
    ledger_path.write_text(json.dumps(entry, sort_keys=True) + "\n")

    store.append(_record(correlation_id="new"))
    entries = store.read_all()
    assert all(e["record"]["correlation_id"] != "old" for e in entries)
