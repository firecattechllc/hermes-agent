from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigil.ai import (
    CLAUDE_PROVIDER_ID,
    ClaudeInspectionFailure,
    ClaudeInspectionFinding,
    ClaudeInspectionReport,
    ClaudeInspectionStoreConflictError,
    ClaudeInspectionStoreCorruptionError,
    ClaudeInspectionStoreError,
    DurableClaudeInspectionStore,
    claude_inspection_status,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
NOW = "2026-08-02T21:00:00Z"


def report(
    inspection_id: str = "inspection-001",
    *,
    severity: str = "medium",
    failure: ClaudeInspectionFailure | None = None,
) -> ClaudeInspectionReport:
    findings = (
        ()
        if failure is not None
        else (
            ClaudeInspectionFinding(
                finding_id="finding-001",
                severity=severity,
                category="routing",
                summary="External admission remains explicit.",
                evidence_references=(DIGEST_B,),
                recommendation="Retain governed admission.",
            ),
        )
    )
    return ClaudeInspectionReport(
        inspection_id=inspection_id,
        target_revision="4dd1b789d",
        target_digest=DIGEST_A,
        provider_id=CLAUDE_PROVIDER_ID,
        model_id="claude-sonnet-governed",
        findings=findings,
        limitations=("Bounded sanitized material only.",),
        report_digest="sha256:" + inspection_id.encode().hex().ljust(64, "0")[:64],
        completed_at=NOW,
        failure=failure,
    )


def test_store_round_trip_and_hash_chain(tmp_path: Path) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    first = report()
    second = report("inspection-002", severity="high")

    assert store.append(first) == first
    assert store.append(second) == second
    assert store.read_reports() == (first, second)

    lines = store.path.read_text().splitlines()
    first_envelope = json.loads(lines[0])
    second_envelope = json.loads(lines[1])
    assert first_envelope["sequence"] == 1
    assert second_envelope["sequence"] == 2
    assert second_envelope["previous_entry_hash"] == first_envelope["entry_hash"]


def test_duplicate_identity_is_rejected(tmp_path: Path) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.append(report())
    with pytest.raises(ClaudeInspectionStoreConflictError):
        store.append(report())


def test_corruption_and_hash_tampering_fail_closed(tmp_path: Path) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.append(report())

    envelope = json.loads(store.path.read_text())
    envelope["report"]["provider_id"] = "tampered-provider"
    store.path.write_text(json.dumps(envelope) + "\n")

    with pytest.raises(ClaudeInspectionStoreCorruptionError):
        store.read_reports()


def test_truncated_tail_recovery_is_explicit(tmp_path: Path) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    first = report()
    store.append(first)
    with store.path.open("ab") as output:
        output.write(b'{"truncated":')

    with pytest.raises(
        ClaudeInspectionStoreCorruptionError,
        match="truncated tail",
    ):
        store.read_reports(recover_truncated_tail=False)

    assert store.read_reports(recover_truncated_tail=True) == (first,)


def test_store_rejects_relative_and_unsafe_roots(tmp_path: Path) -> None:
    with pytest.raises(ClaudeInspectionStoreError):
        DurableClaudeInspectionStore(Path("relative"))
    unsafe = tmp_path / "unsafe"
    target = tmp_path / "target"
    target.mkdir()
    unsafe.symlink_to(target, target_is_directory=True)
    with pytest.raises(ClaudeInspectionStoreError):
        DurableClaudeInspectionStore(unsafe)


def test_status_projection_is_sanitized_and_read_only(tmp_path: Path) -> None:
    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.append(report())
    store.append(
        report(
            "inspection-002",
            severity="critical",
            failure=ClaudeInspectionFailure.CONTRACT_VIOLATION,
        )
    )

    status = claude_inspection_status(tmp_path.resolve())

    assert status["state"] == "ready"
    assert status["store_health"] == "healthy"
    assert status["report_count"] == 2
    assert status["successful_report_count"] == 1
    assert status["failed_report_count"] == 1
    assert status["latest_report"]["failure"] == "contract_violation"
    assert status["latest_report"]["finding_count"] == 0
    assert status["paper_only"] is True
    assert status["broker_submission"] is False
    assert status["execution_authorized"] is False
    assert status["approval_authority"] is False
    assert status["portfolio_mutation"] is False
    assert status["tool_execution"] is False
    serialized = json.dumps(status)
    assert "recommendation" not in serialized
    assert "summary" not in serialized


def test_empty_and_corrupt_status_fail_closed(tmp_path: Path) -> None:
    empty = claude_inspection_status(tmp_path.resolve())
    assert empty["state"] == "empty"
    assert empty["report_count"] == 0

    store = DurableClaudeInspectionStore(tmp_path.resolve())
    store.path.write_text("corrupt\n")
    invalid = claude_inspection_status(tmp_path.resolve())
    assert invalid["state"] == "invalid"
    assert invalid["store_health"] == "corrupt"
    assert invalid["report_count"] == 0
    assert invalid["approval_authority"] is False
