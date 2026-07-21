from types import SimpleNamespace

import pytest

from hermes_cli.agent_roles.runtime_recovery_reporting import (
    RuntimeRecoveryReportingService,
)


class FakeVisibilityService:
    def __init__(self, records):
        self.records = tuple(records)
        self.calls = []

    def list_records(self, project_id):
        self.calls.append(project_id)
        return self.records


def reconciliation(**overrides):
    values = {
        "reconciliation_id": "reconciliation-1",
        "project_id": "project-1",
        "execution_id": "execution-1",
        "recovery_id": "recovery-1",
        "recovery_execution_id": "recovery-execution-1",
        "action": "retry",
        "recovery_execution_state": "completed",
        "reconciliation_state": "reconciled",
        "actor_id": "reconciler",
        "correlation_id": "correlation-1",
        "causation_id": "causation-1",
        "reason": "runtime recovered",
        "evidence_refs": ("evidence-a", "evidence-b"),
        "reconciled_at": 100,
        "source_recovery_checksum": "recovery-checksum",
        "source_recovery_execution_checksum": "execution-checksum",
        "checksum": "reconciliation-checksum",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def closure(**overrides):
    values = {
        "closure_id": "closure-1",
        "project_id": "project-1",
        "reconciliation_id": "reconciliation-1",
        "recovery_execution_id": "recovery-execution-1",
        "recovery_id": "recovery-1",
        "execution_id": "execution-1",
        "closure_state": "closed",
        "reason": "recovery lifecycle closed",
        "evidence_refs": ("evidence-b", "evidence-c"),
        "closed_at": 140,
        "checksum": "closure-checksum",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def service(reconciliations=None, closures=None):
    return RuntimeRecoveryReportingService(
        FakeVisibilityService(
            reconciliations
            if reconciliations is not None
            else [reconciliation()]
        ),
        FakeVisibilityService(
            closures if closures is not None else [closure()]
        ),
    )


def test_builds_terminal_report():
    report = service().get_report("project-1", "recovery-1")

    assert report.project_id == "project-1"
    assert report.recovery_id == "recovery-1"
    assert report.reconciliation_id == "reconciliation-1"
    assert report.closure_id == "closure-1"
    assert report.is_terminal is True
    assert report.requires_attention is False
    assert report.closure_latency == 40
    assert report.evidence_refs == (
        "evidence-a",
        "evidence-b",
        "evidence-c",
    )
    assert report.source_checksums == (
        "recovery-checksum",
        "execution-checksum",
        "reconciliation-checksum",
        "closure-checksum",
    )


def test_open_report_requires_attention():
    report = service(closures=[]).get_report(
        "project-1",
        "recovery-1",
    )

    assert report.closure_id is None
    assert report.closed_at is None
    assert report.closure_latency is None
    assert report.is_terminal is False
    assert report.requires_attention is True


def test_non_reconciled_state_requires_attention():
    report = service(
        reconciliations=[
            reconciliation(reconciliation_state="diverged")
        ],
        closures=[],
    ).get_report("project-1", "recovery-1")

    assert report.requires_attention is True


def test_reports_are_deterministic():
    first = service().get_report("project-1", "recovery-1")
    second = service().get_report("project-1", "recovery-1")

    assert first == second
    assert first.checksum == second.checksum
    assert first.report_id == second.report_id


def test_project_report_counts_lifecycle_states():
    reporting = service(
        reconciliations=[
            reconciliation(),
            reconciliation(
                reconciliation_id="reconciliation-2",
                recovery_id="recovery-2",
                recovery_execution_id="recovery-execution-2",
                execution_id="execution-2",
                reconciled_at=200,
                checksum="reconciliation-checksum-2",
            ),
        ],
        closures=[closure()],
    )

    report = reporting.project_report(
        "project-1",
        generated_at=300,
    )

    assert report.total_recoveries == 2
    assert report.terminal_recoveries == 1
    assert report.open_recoveries == 1
    assert report.attention_required == 1
    assert len(report.reports) == 2


def test_project_report_is_deterministic():
    reporting = service()

    first = reporting.project_report(
        "project-1",
        generated_at=300,
    )
    second = reporting.project_report(
        "project-1",
        generated_at=300,
    )

    assert first == second
    assert first.checksum == second.checksum


def test_filters_attention_reports():
    reporting = service(
        reconciliations=[
            reconciliation(),
            reconciliation(
                reconciliation_id="reconciliation-2",
                recovery_id="recovery-2",
                recovery_execution_id="recovery-execution-2",
                execution_id="execution-2",
                reconciled_at=200,
                reconciliation_state="diverged",
                checksum="reconciliation-checksum-2",
            ),
        ],
        closures=[closure()],
    )

    reports = reporting.list_reports(
        "project-1",
        requires_attention=True,
    )

    assert len(reports) == 1
    assert reports[0].recovery_id == "recovery-2"


def test_rejects_duplicate_closures():
    reporting = service(
        closures=[
            closure(),
            closure(closure_id="closure-2"),
        ]
    )

    with pytest.raises(
        ValueError,
        match="multiple closures",
    ):
        reporting.list_reports("project-1")


def test_rejects_project_provenance_mismatch():
    reporting = service(
        closures=[closure(project_id="project-2")]
    )

    with pytest.raises(
        ValueError,
        match="project provenance mismatch",
    ):
        reporting.list_reports("project-1")


def test_rejects_recovery_provenance_mismatch():
    reporting = service(
        closures=[closure(recovery_id="recovery-2")]
    )

    with pytest.raises(
        ValueError,
        match="recovery provenance mismatch",
    ):
        reporting.list_reports("project-1")


def test_rejects_closure_before_reconciliation():
    reporting = service(
        closures=[closure(closed_at=99)]
    )

    with pytest.raises(
        ValueError,
        match="predates reconciliation",
    ):
        reporting.list_reports("project-1")


@pytest.mark.parametrize("value", ["", " ", "\n"])
def test_rejects_empty_project_id(value):
    with pytest.raises(
        ValueError,
        match="project_id must not be empty",
    ):
        service().list_reports(value)


def test_missing_report_raises_key_error():
    with pytest.raises(
        KeyError,
        match="runtime recovery report not found",
    ):
        service().get_report("project-1", "missing")


def test_negative_generated_at_rejected():
    with pytest.raises(
        ValueError,
        match="generated_at must be non-negative",
    ):
        service().project_report(
            "project-1",
            generated_at=-1,
        )
