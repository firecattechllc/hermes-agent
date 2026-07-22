"""Immutable governed runtime-session tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchWorkspace,
    LaunchWorkspaceMode,
)
from hermes_cli.agent_roles.launch_validation import (
    LaunchValidationResult,
)
from hermes_cli.agent_roles.runtime_handoff import (
    DeterministicDryRunAdapter,
    RuntimeHandoffMode,
    RuntimeHandoffReceipt,
    RuntimeHandoffService,
    RuntimeHandoffStatus,
)
from hermes_cli.agent_roles.runtime_session import (
    RuntimeSession,
    RuntimeSessionEvent,
    RuntimeSessionFactory,
    RuntimeSessionService,
    RuntimeSessionState,
    RuntimeSessionTransition,
)


def _contract(
    *,
    contract_id: str = "launch_1",
    runtime: str = "hermes-agent",
) -> LaunchContract:
    return LaunchContract(
        contract_id=contract_id,
        project_id="hermes-platform",
        assignment_id="assign_1",
        role_id="builder",
        agent_id="agent_codex",
        backlog_item_id="backlog_1",
        status=LaunchContractStatus.READY,
        instructions="Create governed runtime session.",
        created_at=100,
        correlation_id="corr_1",
        causation_id="assign_1",
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
            runtime=runtime,
            engine="codex",
            provider="openai-codex",
            model="auto",
        ),
    )


def _validation(
    contract_id: str = "launch_1",
) -> LaunchValidationResult:
    return LaunchValidationResult(
        contract_id=contract_id,
        valid=True,
    )


def _receipt(
    contract: LaunchContract | None = None,
) -> RuntimeHandoffReceipt:
    contract = contract or _contract()

    return RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        contract,
        _validation(contract.contract_id),
        requested_at=200,
    )


def test_accepted_receipt_creates_created_session() -> None:
    contract = _contract()
    receipt = _receipt(contract)

    session = RuntimeSessionService().create(
        contract,
        receipt,
        created_at=300,
    )

    assert session.state == RuntimeSessionState.CREATED
    assert session.contract_id == contract.contract_id
    assert session.receipt_id == receipt.receipt_id
    assert session.workspace_id == "workspace_assign_1"
    assert session.execution_started is False
    assert session.process_id is None
    assert len(session.events) == 1
    assert session.events[0].transition == (
        RuntimeSessionTransition.CREATED
    )


def test_session_id_is_deterministic() -> None:
    contract = _contract()
    receipt = _receipt(contract)
    service = RuntimeSessionService()

    first = service.create(
        contract,
        receipt,
        created_at=300,
    )
    second = service.create(
        contract,
        receipt,
        created_at=300,
    )

    assert first.session_id == second.session_id
    assert first == second


def test_session_id_changes_for_different_receipt() -> None:
    contract = _contract()
    first_receipt = _receipt(contract)
    second_receipt = first_receipt.model_copy(
        update={
            "receipt_id": "handoff_other",
        }
    )

    factory = RuntimeSessionFactory()

    first = factory.create(
        contract,
        first_receipt,
        created_at=300,
    )
    second = factory.create(
        contract,
        second_receipt,
        created_at=300,
    )

    assert first.session_id != second.session_id


def test_contract_id_mismatch_is_rejected() -> None:
    contract = _contract()
    receipt = _receipt(contract).model_copy(
        update={
            "contract_id": "launch_other",
        }
    )

    with pytest.raises(
        ValueError,
        match="contract_id does not match",
    ):
        RuntimeSessionService().create(
            contract,
            receipt,
            created_at=300,
        )


def test_runtime_mismatch_is_rejected() -> None:
    contract = _contract()
    receipt = _receipt(contract).model_copy(
        update={
            "runtime": "other-runtime",
        }
    )

    with pytest.raises(
        ValueError,
        match="runtime does not match",
    ):
        RuntimeSessionService().create(
            contract,
            receipt,
            created_at=300,
        )


def test_rejected_receipt_cannot_create_session() -> None:
    contract = _contract()
    receipt = RuntimeHandoffReceipt(
        receipt_id="handoff_rejected",
        request_fingerprint="a" * 64,
        contract_id=contract.contract_id,
        runtime=contract.environment.runtime,
        mode=RuntimeHandoffMode.DRY_RUN,
        status=RuntimeHandoffStatus.REJECTED,
        accepted=False,
        reasons=("validation failed",),
        adapter_name="deterministic-dry-run",
        adapter_version="1",
        created_at=200,
    )

    with pytest.raises(
        ValueError,
        match="requires accepted handoff receipt",
    ):
        RuntimeSessionService().create(
            contract,
            receipt,
            created_at=300,
        )


def test_created_session_can_be_marked_ready() -> None:
    contract = _contract()
    receipt = _receipt(contract)
    service = RuntimeSessionService()
    created = service.create(
        contract,
        receipt,
        created_at=300,
    )

    ready = service.mark_ready(
        created,
        ready_at=301,
    )

    assert ready.state == RuntimeSessionState.READY
    assert ready.updated_at == 301
    assert len(ready.events) == 2
    assert ready.events[-1].transition == (
        RuntimeSessionTransition.MARKED_READY
    )
    assert ready.events[-1].state == (
        RuntimeSessionState.READY
    )


def test_mark_ready_does_not_mutate_created_session() -> None:
    contract = _contract()
    receipt = _receipt(contract)
    service = RuntimeSessionService()
    created = service.create(
        contract,
        receipt,
        created_at=300,
    )
    before = created.model_dump()

    ready = service.mark_ready(
        created,
        ready_at=301,
    )

    assert created.model_dump() == before
    assert created.state == RuntimeSessionState.CREATED
    assert ready is not created


def test_ready_session_cannot_be_marked_ready_again() -> None:
    contract = _contract()
    receipt = _receipt(contract)
    service = RuntimeSessionService()
    created = service.create(
        contract,
        receipt,
        created_at=300,
    )
    ready = service.mark_ready(
        created,
        ready_at=301,
    )

    with pytest.raises(
        ValueError,
        match="only created runtime sessions",
    ):
        service.mark_ready(
            ready,
            ready_at=302,
        )


def test_ready_timestamp_cannot_move_backward() -> None:
    contract = _contract()
    receipt = _receipt(contract)
    created = RuntimeSessionService().create(
        contract,
        receipt,
        created_at=300,
    )

    with pytest.raises(
        ValueError,
        match="cannot precede",
    ):
        RuntimeSessionService().mark_ready(
            created,
            ready_at=299,
        )


def test_session_is_immutable() -> None:
    contract = _contract()
    session = RuntimeSessionService().create(
        contract,
        _receipt(contract),
        created_at=300,
    )

    with pytest.raises(Exception):
        session.state = RuntimeSessionState.READY


def test_pre_execution_session_cannot_claim_execution() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot report execution",
    ):
        RuntimeSession(
            session_id="session_invalid",
            project_id="hermes-platform",
            contract_id="launch_1",
            assignment_id="assign_1",
            role_id="builder",
            agent_id="agent_codex",
            receipt_id="handoff_1",
            request_fingerprint="a" * 64,
            adapter_name="deterministic-dry-run",
            adapter_version="1",
            runtime="hermes-agent",
            workspace_id="workspace_assign_1",
            state=RuntimeSessionState.CREATED,
            created_at=300,
            updated_at=300,
            execution_started=True,
            events=(
                RuntimeSessionEvent(
                    transition=(
                        RuntimeSessionTransition.CREATED
                    ),
                    state=RuntimeSessionState.CREATED,
                    timestamp=300,
                    reason="created",
                ),
            ),
        )


def test_session_events_must_be_chronological() -> None:
    with pytest.raises(
        ValidationError,
        match="must be chronological",
    ):
        RuntimeSession(
            session_id="session_invalid",
            project_id="hermes-platform",
            contract_id="launch_1",
            assignment_id="assign_1",
            role_id="builder",
            agent_id="agent_codex",
            receipt_id="handoff_1",
            request_fingerprint="a" * 64,
            adapter_name="deterministic-dry-run",
            adapter_version="1",
            runtime="hermes-agent",
            state=RuntimeSessionState.READY,
            created_at=300,
            updated_at=300,
            events=(
                RuntimeSessionEvent(
                    transition=(
                        RuntimeSessionTransition.CREATED
                    ),
                    state=RuntimeSessionState.CREATED,
                    timestamp=300,
                    reason="created",
                ),
                RuntimeSessionEvent(
                    transition=(
                        RuntimeSessionTransition.MARKED_READY
                    ),
                    state=RuntimeSessionState.READY,
                    timestamp=299,
                    reason="ready",
                ),
            ),
        )


def test_ready_session_requires_ready_transition() -> None:
    with pytest.raises(ValidationError):
        RuntimeSession(
            session_id="session_invalid",
            project_id="hermes-platform",
            contract_id="launch_1",
            assignment_id="assign_1",
            role_id="builder",
            agent_id="agent_codex",
            receipt_id="handoff_1",
            request_fingerprint="a" * 64,
            adapter_name="deterministic-dry-run",
            adapter_version="1",
            runtime="hermes-agent",
            state=RuntimeSessionState.READY,
            created_at=300,
            updated_at=301,
            events=(
                RuntimeSessionEvent(
                    transition=(
                        RuntimeSessionTransition.CREATED
                    ),
                    state=RuntimeSessionState.CREATED,
                    timestamp=300,
                    reason="created",
                ),
                RuntimeSessionEvent(
                    transition=(
                        RuntimeSessionTransition.CREATED
                    ),
                    state=RuntimeSessionState.READY,
                    timestamp=301,
                    reason="invalid transition",
                ),
            ),
        )
