"""Mission Control launch-visibility tests."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchWorkspace,
    LaunchWorkspaceMode,
)
from hermes_cli.agent_roles.launch_validation import (
    LaunchValidationCode,
    LaunchValidationIssue,
    LaunchValidationResult,
    LaunchValidationSeverity,
)
from hermes_cli.agent_roles.launch_visibility import (
    CONTRACT_EVENT_TYPE,
    VALIDATION_EVENT_TYPE,
    LaunchVisibilityAdapter,
    LaunchVisibilityService,
)
from hermes_cli.mission_control.service import (
    MissionControlService,
)
from hermes_cli.mission_control.store import MissionControlStore


def _contract(
    *,
    contract_id: str = "launch_1",
    project_id: str = "hermes-platform",
    status: LaunchContractStatus = LaunchContractStatus.READY,
    blocked_reasons: tuple[str, ...] = (),
) -> LaunchContract:
    return LaunchContract(
        contract_id=contract_id,
        project_id=project_id,
        assignment_id="assign_1",
        role_id="builder",
        agent_id="agent_codex",
        backlog_item_id="backlog_1",
        status=status,
        instructions="Expose launch visibility.",
        created_at=100,
        correlation_id="corr_1",
        causation_id="assign_1",
        blocked_reasons=blocked_reasons,
        workspace=LaunchWorkspace(
            mode=LaunchWorkspaceMode.ISOLATED_WRITE,
            repository_root="/repo/hermes-platform",
            workspace_id="workspace_assign_1",
            base_ref="step5-specialized-agent-roles",
        ),
        policy=LaunchPolicy(
            risk_level="medium",
            modifies_repository=True,
            human_approved=False,
            allowed_paths=("hermes_cli",),
            required_capabilities=("code-change",),
        ),
        environment=LaunchEnvironment(
            runtime="hermes-agent",
            engine="codex",
            provider="openai-codex",
            model="auto",
        ),
    )


def _validation(
    contract_id: str = "launch_1",
    *,
    valid: bool = True,
) -> LaunchValidationResult:
    issues = ()

    if not valid:
        issues = (
            LaunchValidationIssue(
                code=LaunchValidationCode.RUNTIME_MISMATCH,
                severity=LaunchValidationSeverity.ERROR,
                message="runtime mismatch",
                field="environment.runtime",
            ),
        )

    return LaunchValidationResult(
        contract_id=contract_id,
        valid=valid,
        issues=issues,
    )


def _service(tmp_path) -> LaunchVisibilityService:
    mission_control = MissionControlService(
        store=MissionControlStore(
            root=tmp_path / "mission_control"
        )
    )
    return LaunchVisibilityService(mission_control)


def test_adapter_creates_contract_then_validation_events() -> None:
    events = LaunchVisibilityAdapter().to_events(
        _contract(),
        _validation(),
        timestamp=200,
    )

    assert tuple(event.event_type for event in events) == (
        CONTRACT_EVENT_TYPE,
        VALIDATION_EVENT_TYPE,
    )
    assert events[0].timestamp == 200
    assert events[1].timestamp == 200
    assert events[1].causation_id == events[0].event_id


def test_adapter_rejects_contract_id_mismatch() -> None:
    with pytest.raises(
        ValueError,
        match="contract_id does not match",
    ):
        LaunchVisibilityAdapter().to_events(
            _contract(),
            _validation("launch_other"),
        )


def test_publish_stores_and_projects_ready_contract(
    tmp_path,
) -> None:
    visibility = _service(tmp_path)

    record = visibility.publish(
        _contract(),
        _validation(),
        timestamp=200,
    )

    assert record.contract_id == "launch_1"
    assert record.contract_status == "ready"
    assert record.validation_valid is True
    assert record.runtime == "hermes-agent"
    assert record.engine == "codex"
    assert record.modifies_repository is True


def test_published_events_receive_monotonic_sequences(
    tmp_path,
) -> None:
    mission_control = MissionControlService(
        store=MissionControlStore(
            root=tmp_path / "mission_control"
        )
    )
    visibility = LaunchVisibilityService(mission_control)

    visibility.publish(
        _contract(),
        _validation(),
        timestamp=200,
    )

    events = mission_control.get_events("hermes-platform")

    assert [event.sequence for event in events] == [1, 2]


def test_list_records_replays_persisted_visibility(
    tmp_path,
) -> None:
    visibility = _service(tmp_path)

    visibility.publish(
        _contract(contract_id="launch_1"),
        _validation("launch_1"),
        timestamp=200,
    )
    visibility.publish(
        _contract(contract_id="launch_2"),
        _validation("launch_2"),
        timestamp=201,
    )

    records = visibility.list_records("hermes-platform")

    assert tuple(record.contract_id for record in records) == (
        "launch_1",
        "launch_2",
    )


def test_list_records_filters_by_contract_id(
    tmp_path,
) -> None:
    visibility = _service(tmp_path)

    visibility.publish(
        _contract(contract_id="launch_1"),
        _validation("launch_1"),
    )
    visibility.publish(
        _contract(contract_id="launch_2"),
        _validation("launch_2"),
    )

    records = visibility.list_records(
        "hermes-platform",
        contract_id="launch_2",
    )

    assert len(records) == 1
    assert records[0].contract_id == "launch_2"


def test_blocked_contract_and_failed_validation_are_visible(
    tmp_path,
) -> None:
    visibility = _service(tmp_path)

    record = visibility.publish(
        _contract(
            status=LaunchContractStatus.BLOCKED,
            blocked_reasons=("Provider unavailable",),
        ),
        _validation(valid=False),
    )

    assert record.contract_status == "blocked"
    assert record.validation_valid is False
    assert record.blocked_reasons == (
        "Provider unavailable",
    )
    assert tuple(
        issue.code for issue in record.validation_issues
    ) == (LaunchValidationCode.RUNTIME_MISMATCH,)


def test_visibility_is_project_scoped(tmp_path) -> None:
    visibility = _service(tmp_path)

    visibility.publish(
        _contract(
            contract_id="launch_a",
            project_id="project-a",
        ),
        _validation("launch_a"),
    )
    visibility.publish(
        _contract(
            contract_id="launch_b",
            project_id="project-b",
        ),
        _validation("launch_b"),
    )

    assert tuple(
        record.contract_id
        for record in visibility.list_records("project-a")
    ) == ("launch_a",)

    assert tuple(
        record.contract_id
        for record in visibility.list_records("project-b")
    ) == ("launch_b",)


def test_visibility_record_is_immutable(tmp_path) -> None:
    record = _service(tmp_path).publish(
        _contract(),
        _validation(),
    )

    with pytest.raises(Exception):
        record.validation_valid = False


def test_adapter_fails_closed_on_malformed_payload() -> None:
    contract_event, validation_event = (
        LaunchVisibilityAdapter().to_events(
            _contract(),
            _validation(),
        )
    )
    contract_event.payload = {
        "contract": "not-a-mapping",
    }

    with pytest.raises(
        ValueError,
        match="payload is malformed",
    ):
        LaunchVisibilityAdapter().from_events(
            (contract_event, validation_event)
        )


def test_unpaired_contract_is_not_displayed() -> None:
    contract_event, _ = LaunchVisibilityAdapter().to_events(
        _contract(),
        _validation(),
    )

    records = LaunchVisibilityAdapter().from_events(
        (contract_event,)
    )

    assert records == ()
