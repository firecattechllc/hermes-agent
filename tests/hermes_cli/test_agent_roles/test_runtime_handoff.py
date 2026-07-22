"""Governed runtime handoff tests."""

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
    LaunchValidationCode,
    LaunchValidationIssue,
    LaunchValidationResult,
    LaunchValidationSeverity,
)
from hermes_cli.agent_roles.runtime_handoff import (
    DeterministicDryRunAdapter,
    RuntimeAdapter,
    RuntimeHandoffMode,
    RuntimeHandoffReceipt,
    RuntimeHandoffRequest,
    RuntimeHandoffService,
    RuntimeHandoffStatus,
)


def _contract(
    *,
    contract_id: str = "launch_1",
    status: LaunchContractStatus = LaunchContractStatus.READY,
    blocked_reasons: tuple[str, ...] = (),
) -> LaunchContract:
    return LaunchContract(
        contract_id=contract_id,
        project_id="hermes-platform",
        assignment_id="assign_1",
        role_id="builder",
        agent_id="agent_codex",
        backlog_item_id="backlog_1",
        status=status,
        instructions="Perform governed runtime handoff.",
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


def test_request_rejects_contract_validation_mismatch() -> None:
    with pytest.raises(
        ValidationError,
        match="contract_id does not match",
    ):
        RuntimeHandoffRequest(
            contract=_contract(),
            validation=_validation("launch_other"),
            requested_at=200,
        )


def test_request_fingerprint_is_deterministic() -> None:
    first = RuntimeHandoffRequest(
        contract=_contract(),
        validation=_validation(),
        requested_at=200,
    )
    second = RuntimeHandoffRequest(
        contract=_contract(),
        validation=_validation(),
        requested_at=200,
    )

    assert first.request_fingerprint == (
        second.request_fingerprint
    )
    assert len(first.request_fingerprint) == 64


def test_request_fingerprint_changes_with_input() -> None:
    first = RuntimeHandoffRequest(
        contract=_contract(),
        validation=_validation(),
        requested_at=200,
    )
    second = RuntimeHandoffRequest(
        contract=_contract(),
        validation=_validation(),
        requested_at=201,
    )

    assert first.request_fingerprint != (
        second.request_fingerprint
    )


def test_dry_run_adapter_implements_protocol() -> None:
    adapter = DeterministicDryRunAdapter()

    assert isinstance(adapter, RuntimeAdapter)


def test_ready_valid_contract_is_accepted() -> None:
    receipt = RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        _contract(),
        _validation(),
        requested_at=200,
    )

    assert receipt.status == RuntimeHandoffStatus.ACCEPTED
    assert receipt.accepted is True
    assert receipt.reasons == ()
    assert receipt.mode == RuntimeHandoffMode.DRY_RUN
    assert receipt.execution_started is False
    assert receipt.process_id is None


def test_dry_run_receipt_is_deterministic() -> None:
    service = RuntimeHandoffService(
        DeterministicDryRunAdapter()
    )

    first = service.dry_run(
        _contract(),
        _validation(),
        requested_at=200,
    )
    second = service.dry_run(
        _contract(),
        _validation(),
        requested_at=200,
    )

    assert first == second
    assert first.receipt_id.startswith("handoff_")


def test_blocked_contract_is_rejected() -> None:
    receipt = RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        _contract(
            status=LaunchContractStatus.BLOCKED,
            blocked_reasons=("Provider unavailable",),
        ),
        _validation(),
        requested_at=200,
    )

    assert receipt.status == RuntimeHandoffStatus.REJECTED
    assert receipt.accepted is False
    assert receipt.reasons == (
        "launch contract is not ready",
        "launch contract contains blocked reasons",
    )


def test_failed_validation_is_rejected() -> None:
    receipt = RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        _contract(),
        _validation(valid=False),
        requested_at=200,
    )

    assert receipt.accepted is False
    assert receipt.reasons == (
        "launch contract validation failed",
        "launch validation contains error diagnostics",
    )


def test_receipt_is_immutable() -> None:
    receipt = RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        _contract(),
        _validation(),
        requested_at=200,
    )

    with pytest.raises(Exception):
        receipt.accepted = False


def test_dry_run_receipt_cannot_claim_execution() -> None:
    with pytest.raises(
        ValidationError,
        match="cannot report execution",
    ):
        RuntimeHandoffReceipt(
            receipt_id="handoff_invalid",
            request_fingerprint="a" * 64,
            contract_id="launch_1",
            runtime="hermes-agent",
            mode=RuntimeHandoffMode.DRY_RUN,
            status=RuntimeHandoffStatus.ACCEPTED,
            accepted=True,
            adapter_name="invalid-adapter",
            adapter_version="1",
            created_at=200,
            execution_started=True,
        )


def test_rejected_receipt_requires_reasons() -> None:
    with pytest.raises(
        ValidationError,
        match="requires at least one reason",
    ):
        RuntimeHandoffReceipt(
            receipt_id="handoff_invalid",
            request_fingerprint="a" * 64,
            contract_id="launch_1",
            runtime="hermes-agent",
            mode=RuntimeHandoffMode.DRY_RUN,
            status=RuntimeHandoffStatus.REJECTED,
            accepted=False,
            adapter_name="invalid-adapter",
            adapter_version="1",
            created_at=200,
        )


def test_service_rejects_wrong_contract_receipt() -> None:
    class WrongContractAdapter:
        adapter_name = "wrong-contract"
        adapter_version = "1"

        def handoff(
            self,
            request: RuntimeHandoffRequest,
        ) -> RuntimeHandoffReceipt:
            return RuntimeHandoffReceipt(
                receipt_id="handoff_wrong",
                request_fingerprint=(
                    request.request_fingerprint
                ),
                contract_id="launch_other",
                runtime=request.contract.environment.runtime,
                mode=RuntimeHandoffMode.DRY_RUN,
                status=RuntimeHandoffStatus.ACCEPTED,
                accepted=True,
                adapter_name=self.adapter_name,
                adapter_version=self.adapter_version,
                created_at=request.requested_at,
            )

    with pytest.raises(
        ValueError,
        match="different contract",
    ):
        RuntimeHandoffService(
            WrongContractAdapter()
        ).dry_run(
            _contract(),
            _validation(),
            requested_at=200,
        )


def test_service_rejects_wrong_request_receipt() -> None:
    class WrongRequestAdapter:
        adapter_name = "wrong-request"
        adapter_version = "1"

        def handoff(
            self,
            request: RuntimeHandoffRequest,
        ) -> RuntimeHandoffReceipt:
            return RuntimeHandoffReceipt(
                receipt_id="handoff_wrong",
                request_fingerprint="b" * 64,
                contract_id=request.contract.contract_id,
                runtime=request.contract.environment.runtime,
                mode=RuntimeHandoffMode.DRY_RUN,
                status=RuntimeHandoffStatus.ACCEPTED,
                accepted=True,
                adapter_name=self.adapter_name,
                adapter_version=self.adapter_version,
                created_at=request.requested_at,
            )

    with pytest.raises(
        ValueError,
        match="different request",
    ):
        RuntimeHandoffService(
            WrongRequestAdapter()
        ).dry_run(
            _contract(),
            _validation(),
            requested_at=200,
        )


def test_input_contract_and_validation_are_not_mutated() -> None:
    contract = _contract()
    validation = _validation()
    contract_before = contract.model_dump()
    validation_before = validation.model_dump()

    RuntimeHandoffService(
        DeterministicDryRunAdapter()
    ).dry_run(
        contract,
        validation,
        requested_at=200,
    )

    assert contract.model_dump() == contract_before
    assert validation.model_dump() == validation_before
