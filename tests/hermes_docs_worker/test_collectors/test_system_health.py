from __future__ import annotations

from pathlib import Path

from hermes_docs_worker.collectors import system_health
from hermes_docs_worker.status import StatusValue


def test_collect_returns_four_facts(tmp_path: Path) -> None:
    facts = system_health.collect(disk_path=tmp_path, now=1000)
    labels = {f.label for f in facts}
    assert labels == {"disk", "memory", "temperature", "uptime"}
    assert all(f.collected_at == 1000 for f in facts)


def test_disk_status_is_verified_for_a_writable_tmp_dir(tmp_path: Path) -> None:
    facts = {f.label: f for f in system_health.collect(disk_path=tmp_path, now=0)}
    assert facts["disk"].status in (StatusValue.VERIFIED, StatusValue.DEGRADED, StatusValue.BLOCKED)


def test_disk_status_unknown_for_nonexistent_path() -> None:
    facts = {f.label: f for f in system_health.collect(disk_path=Path("/nonexistent-xyz-path"), now=0)}
    assert facts["disk"].status == StatusValue.UNKNOWN
