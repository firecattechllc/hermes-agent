"""Step 5 specialized-agent execution-loop certification tests."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles import (
    Assignment,
    AssignmentStatus,
    BuiltinRole,
    DeterministicDryRunAdapter,
    ExecutionAction,
    ExecutionEvidence,
    ExecutionOutcome,
    ExecutionVisibilityService,
    FailureCategory,
    GovernedExecutionService,
    LaunchContract,
    LaunchContractStatus,
    LaunchEnvironment,
    LaunchPolicy,
    LaunchValidationResult,
    LaunchWorkspace,
    LaunchWorkspaceMode,
    PolicyDecision,
    RoleExecutionPlanner,
    RuntimeHandoffService,
    RuntimeSessionService,
    builtin_agent_roles,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from pydantic import ValidationError


ROLES = {role.role_id: role for role in builtin_agent_roles()}


def _artifacts(role_id: str):
    modifies = role_id in {"builder", "documentation"}
    assignment = Assignment(
        assignment_id=f"assign_{role_id}",
        project_id="hermes-platform",
        role_id=role_id,
        assigned_agent_id=f"agent_{role_id}",
        status=AssignmentStatus.ACTIVE,
        instructions=f"Perform governed {role_id} work.",
        created_at=100,
        updated_at=100,
    )
    contract = LaunchContract(
        contract_id=f"launch_{role_id}",
        project_id=assignment.project_id,
        assignment_id=assignment.assignment_id,
        role_id=role_id,
        agent_id=assignment.assigned_agent_id,
        status=LaunchContractStatus.READY,
        instructions=assignment.instructions,
        created_at=101,
        workspace=LaunchWorkspace(
            mode=(
                LaunchWorkspaceMode.ISOLATED_WRITE
                if modifies
                else LaunchWorkspaceMode.READ_ONLY
            ),
            repository_root="/repo/hermes-platform",
            workspace_id=(f"workspace_{role_id}" if modifies else None),
        ),
        policy=LaunchPolicy(
            risk_level="medium",
            modifies_repository=modifies,
            human_approved=role_id in {"security", "release"},
            allowed_paths=(
                ("hermes_cli",)
                if role_id == "builder"
                else (("docs",) if role_id == "documentation" else ())
            ),
        ),
        environment=LaunchEnvironment(runtime="hermes-agent"),
    )
    receipt = RuntimeHandoffService(DeterministicDryRunAdapter()).dry_run(
        contract,
        LaunchValidationResult(contract_id=contract.contract_id, valid=True),
        requested_at=102,
    )
    sessions = RuntimeSessionService()
    session = sessions.mark_ready(
        sessions.create(contract, receipt, created_at=103),
        ready_at=104,
    )
    plan = RoleExecutionPlanner().create(
        assignment, ROLES[role_id], contract, created_at=105
    )
    return assignment, contract, receipt, session, plan


def _evidence(
    action: ExecutionAction,
    *,
    successful: bool = True,
    decision: PolicyDecision = PolicyDecision.ALLOWED,
) -> ExecutionEvidence:
    evidence_types = {
        ExecutionAction.PLAN: "plan",
        ExecutionAction.MODIFY_IMPLEMENTATION: "change_summary",
        ExecutionAction.REVIEW: "review_findings",
        ExecutionAction.VERIFY: "test_result",
        ExecutionAction.SECURITY_ASSESS: "security_decision",
        ExecutionAction.MODIFY_DOCUMENTATION: "documentation_change",
        ExecutionAction.ASSESS_RELEASE: "approval",
        ExecutionAction.PROMOTE: "approval",
    }
    return ExecutionEvidence(
        evidence_id=f"evidence_{action.value}",
        evidence_type=evidence_types[action],
        action=action,
        attempted=f"Attempted governed {action.value}",
        output_summary=f"Bounded {action.value} summary",
        timestamp=107,
        successful=successful,
        policy_decision=decision,
        reason=("unsafe promotion" if decision == PolicyDecision.DENIED else None),
    )


def test_planner_produces_deterministic_execution_plan() -> None:
    assignment, contract, _, _, plan = _artifacts("planner")
    repeated = RoleExecutionPlanner().create(
        assignment, ROLES["planner"], contract, created_at=105
    )
    assert plan == repeated
    assert plan.allowed_actions == (ExecutionAction.PLAN,)
    assert plan.allowed_next_roles == ("builder",)
    assert plan.steps[0].required_evidence == ("plan",)


def test_builder_receives_only_authorized_work() -> None:
    _, _, _, _, plan = _artifacts("builder")
    assert set(plan.allowed_actions) == {
        ExecutionAction.MODIFY_IMPLEMENTATION,
        ExecutionAction.VERIFY,
    }
    RoleExecutionPlanner.require_action(plan, ExecutionAction.MODIFY_IMPLEMENTATION)
    with pytest.raises(PermissionError, match="not authorized"):
        RoleExecutionPlanner.require_action(plan, ExecutionAction.PROMOTE)


def test_reviewer_cannot_modify_implementation() -> None:
    _, _, _, _, plan = _artifacts("reviewer")
    assert plan.allowed_actions == (ExecutionAction.REVIEW,)
    with pytest.raises(PermissionError, match="not authorized"):
        RoleExecutionPlanner.require_action(plan, ExecutionAction.MODIFY_IMPLEMENTATION)


@pytest.mark.parametrize(
    ("role_id", "action", "evidence_name"),
    [
        ("tester", ExecutionAction.VERIFY, "test_result"),
        ("documentation", ExecutionAction.MODIFY_DOCUMENTATION, "documentation_change"),
    ],
)
def test_roles_record_required_completion_evidence(
    role_id, action, evidence_name
) -> None:
    _, contract, receipt, ready, plan = _artifacts(role_id)
    service = GovernedExecutionService()
    running = service.start(ready, plan, started_at=106)
    terminal, result = service.complete(
        running,
        plan,
        contract,
        receipt,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary=f"{role_id} completed",
        evidence=(_evidence(action),),
        completed_at=108,
    )
    assert terminal.state.value == "succeeded"
    assert result.evidence[0].output_summary
    assert evidence_name in plan.steps[0].required_evidence


def test_security_can_block_unsafe_promotion() -> None:
    _, contract, receipt, ready, plan = _artifacts("security")
    service = GovernedExecutionService()
    running = service.start(ready, plan, started_at=106)
    terminal, result = service.complete(
        running,
        plan,
        contract,
        receipt,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Security assessment blocked promotion",
        evidence=(
            _evidence(
                ExecutionAction.SECURITY_ASSESS,
                successful=False,
                decision=PolicyDecision.DENIED,
            ),
        ),
        completed_at=108,
    )
    assert terminal.state.value == "policy_denied"
    assert result.outcome == ExecutionOutcome.POLICY_DENIED
    assert result.failure_category == FailureCategory.POLICY
    assert result.blocking_reasons


def test_release_cannot_promote_without_approvals_and_evidence() -> None:
    _, contract, receipt, ready, plan = _artifacts("release")
    service = GovernedExecutionService()
    running = service.start(ready, plan, started_at=106)
    _, result = service.complete(
        running,
        plan,
        contract,
        receipt,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Promotion evaluated",
        evidence=(_evidence(ExecutionAction.ASSESS_RELEASE),),
        completed_at=108,
        approvals=("review", "verification"),
    )
    assert result.outcome == ExecutionOutcome.POLICY_DENIED
    assert "missing release requirements" in result.blocking_reasons[0]


def test_failed_session_is_auditable_and_retry_is_not_automatic() -> None:
    _, contract, receipt, ready, plan = _artifacts("tester")
    service = GovernedExecutionService()
    running = service.start(ready, plan, started_at=106)
    terminal, result = service.complete(
        running,
        plan,
        contract,
        receipt,
        outcome=ExecutionOutcome.FAILED,
        failure_category=FailureCategory.ENVIRONMENT,
        summary="Test environment exited unexpectedly",
        evidence=(_evidence(ExecutionAction.VERIFY, successful=False),),
        completed_at=108,
    )
    assert terminal.state.value == "failed"
    assert result.retry.eligible is True
    assert result.retry.requires_approval is True
    assert result.retry.automatic is False
    with pytest.raises(ValueError, match="only running"):
        service.complete(
            terminal,
            plan,
            contract,
            receipt,
            outcome=ExecutionOutcome.FAILED,
            failure_category=FailureCategory.EXECUTION,
            summary="illegal second terminal result",
            evidence=(),
            completed_at=109,
        )


def test_success_requires_declared_evidence_and_evidence_rejects_secrets() -> None:
    _, contract, receipt, ready, plan = _artifacts("tester")
    service = GovernedExecutionService()
    running = service.start(ready, plan, started_at=106)

    with pytest.raises(ValueError, match="missing required evidence"):
        service.complete(
            running,
            plan,
            contract,
            receipt,
            outcome=ExecutionOutcome.SUCCEEDED,
            summary="Unsupported success",
            evidence=(),
            completed_at=108,
        )

    with pytest.raises(ValidationError, match="must not contain secrets"):
        ExecutionEvidence(
            evidence_id="evidence_secret",
            evidence_type="test_result",
            action=ExecutionAction.VERIFY,
            attempted="API_KEY=do-not-store",
            output_summary="bounded output",
            timestamp=107,
            successful=False,
        )


def test_full_flow_is_visible_in_mission_control(tmp_path) -> None:
    assignment, contract, receipt, ready, plan = _artifacts("tester")
    execution = GovernedExecutionService()
    running = execution.start(ready, plan, started_at=106)
    terminal, result = execution.complete(
        running,
        plan,
        contract,
        receipt,
        outcome=ExecutionOutcome.SUCCEEDED,
        summary="Verification complete",
        evidence=(_evidence(ExecutionAction.VERIFY),),
        completed_at=108,
    )
    visibility = ExecutionVisibilityService(
        MissionControlService(
            store=MissionControlStore(root=tmp_path / "mission_control")
        )
    )
    published = visibility.publish(terminal, plan, result)
    replayed = visibility.list_records(
        assignment.project_id, assignment_id=assignment.assignment_id
    )
    assert replayed == (published,)
    assert published.session_state == "succeeded"
    assert published.outcome == "succeeded"
    assert published.role_id == "tester"
    assert published.evidence_summaries == ("Bounded verify summary",)
    assert published.retry_eligible is False
