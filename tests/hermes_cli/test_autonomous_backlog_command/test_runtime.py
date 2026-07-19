from types import SimpleNamespace

from hermes_cli import autonomous_backlog_commands as commands
from hermes_cli.autonomous_backlog import models as m


class FakeService:
    def __init__(self) -> None:
        self.create_kwargs = None
        self.complete_kwargs = None

    def create_item(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(
            item_id=kwargs.get("item_id") or "backlog_generated",
            project_id=kwargs["project_id"],
            status=m.BacklogStatus.CANDIDATE,
            priority=kwargs["priority"],
            risk_level=kwargs["risk_level"],
            title=kwargs["title"],
            version=1,
        )

    def complete_item(
        self,
        project_id,
        item_id,
        **kwargs,
    ):
        self.complete_kwargs = {
            "project_id": project_id,
            "item_id": item_id,
            **kwargs,
        }
        return SimpleNamespace(
            item_id=item_id,
            status=m.BacklogStatus.COMPLETED,
            version=4,
            evidence_refs=kwargs["evidence_refs"],
        )


def test_add_command_builds_human_source(
    monkeypatch,
    capsys,
) -> None:
    service = FakeService()
    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    args = SimpleNamespace(
        project="hermes-platform",
        item_id="backlog_1",
        title="Build CLI",
        description="Wire backlog commands.",
        source_type="human",
        source_ref=["manual:test"],
        priority="normal",
        risk="medium",
        depends_on=[],
        acceptance=["Tests pass."],
        capability=[],
        allow_path=["hermes_cli/"],
        deny_path=[],
        execution_policy="",
        correlation_id="",
        idempotency_key="create-backlog-1",
    )

    result = commands._cmd_add(args)

    assert result == 0
    assert service.create_kwargs is not None
    assert service.create_kwargs["project_id"] == "hermes-platform"
    assert service.create_kwargs["source"].source_type is (
        m.BacklogSourceType.HUMAN
    )
    assert service.create_kwargs["source"].source_refs == (
        "manual:test",
    )

    output = capsys.readouterr().out
    assert "backlog: created backlog_1" in output


def test_complete_passes_evidence_refs(
    monkeypatch,
    capsys,
) -> None:
    service = FakeService()
    monkeypatch.setattr(
        commands,
        "_get_service",
        lambda: service,
    )

    args = SimpleNamespace(
        project="hermes-platform",
        item="backlog_1",
        evidence=["pytest:passed"],
        expected_version=3,
        correlation_id="corr_1",
        idempotency_key="complete-backlog-1",
    )

    result = commands._cmd_complete(args)

    assert result == 0
    assert service.complete_kwargs == {
        "project_id": "hermes-platform",
        "item_id": "backlog_1",
        "evidence_refs": ["pytest:passed"],
        "actor": "cli",
        "expected_version": 3,
        "correlation_id": "corr_1",
        "idempotency_key": "complete-backlog-1",
    }

    output = capsys.readouterr().out
    assert "backlog: completed backlog_1" in output


def test_command_without_action_returns_error(
    capsys,
) -> None:
    result = commands.autonomous_backlog_command(
        SimpleNamespace()
    )

    assert result == 1
    assert (
        "autonomous-backlog: missing action"
        in capsys.readouterr().err
    )
