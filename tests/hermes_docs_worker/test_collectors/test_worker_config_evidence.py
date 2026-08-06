from __future__ import annotations

import json

from hermes_docs_worker.collectors import worker_config_evidence
from hermes_docs_worker.status import StatusValue


def test_validity_fact_always_present(worker_config) -> None:
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["validity"].status == StatusValue.VERIFIED


def test_no_path_configured_is_unknown(worker_config) -> None:
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["local_test_evidence"].status == StatusValue.UNKNOWN


def test_missing_test_evidence_file_is_unknown(worker_config, tmp_path) -> None:
    object.__setattr__(worker_config, "hermes_test_evidence_path", tmp_path / "missing.json")
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["local_test_evidence"].status == StatusValue.UNKNOWN


def test_passed_test_evidence_is_verified(worker_config, tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"passed": True, "run_at": "2026-08-06"}), encoding="utf-8")
    object.__setattr__(worker_config, "hermes_test_evidence_path", path)
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["local_test_evidence"].status == StatusValue.VERIFIED


def test_failed_test_evidence_is_degraded(worker_config, tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"passed": False, "run_at": "2026-08-06"}), encoding="utf-8")
    object.__setattr__(worker_config, "hermes_test_evidence_path", path)
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["local_test_evidence"].status == StatusValue.DEGRADED


def test_malformed_test_evidence_is_unknown_not_a_crash(worker_config, tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("not json", encoding="utf-8")
    object.__setattr__(worker_config, "hermes_test_evidence_path", path)
    facts = {f.label: f for f in worker_config_evidence.collect(worker_config, now=0)}
    assert facts["local_test_evidence"].status == StatusValue.UNKNOWN
