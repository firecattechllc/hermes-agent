"""Step 6 governed multi-agent workflow certification tests."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.execution import (
    ExecutionOutcome,
    ExecutionResult,
    FailureCategory,
    RetryDecision,
)
from hermes_cli.agent_roles.execution_planning import (
    ExecutionAction,
    ExecutionPlanStep,
    RoleExecutionPlan,
)
from hermes_cli.agent_roles.workflow import (
    AuthorizationDecision,
    GovernedWorkflowService,
    WorkflowAuthorization,
    WorkflowDecision,
    WorkflowState,
)


def plan(role: str = "builder", assignment: str = "assign_1", plan_id: str = "plan_1") -> RoleExecutionPlan:
    return RoleExecutionPlan(
        plan_id=plan_id,
        project_id="project_1",
        assignment_id=assignment,
        contract_id=f"contract_{assignment}",
        role_id=role,
        agent_id=f"agent_{role}",
        responsibilities=("governed work",),
        allowed_actions=(ExecutionAction.VERIFY,),
        allowed_next_roles=("reviewer", "tester") if role == "builder" else (),
        steps=(ExecutionPlanStep(sequence=1, action=ExecutionAction.VERIFY, responsibility="verify"),),
        created_at=10,
    )


def result(p: RoleExecutionPlan, outcome: ExecutionOutcome = ExecutionOutcome.SUCCEEDED) -> ExecutionResult:
    category = FailureCategory.NONE if outcome == ExecutionOutcome.SUCCEEDED else FailureCategory.EXECUTION
    eligible = outcome == ExecutionOutcome.FAILED
    return ExecutionResult(
        result_id=f"result_{p.assignment_id}",
        project_id=p.project_id,
        assignment_id=p.assignment_id,
        contract_id=p.contract_id,
        receipt_id=f"receipt_{p.assignment_id}",
        session_id=f"session_{p.assignment_id}",
        plan_id=p.plan_id,
        role_id=p.role_id,
        agent_id=p.agent_id,
        outcome=outcome,
        failure_category=category,
        summary="bounded result",
        retry=RetryDecision(
            eligible=eligible,
            category=category,
            reason="explicit governance required",
            requires_approval=True,
            automatic=False,
        ),
        started_at=20,
        completed_at=30,
    )


def auth(wf, decision, *, disposition=AuthorizationDecision.APPROVED, role=None):
    return WorkflowAuthorization(
        authorization_id=f"auth_{wf.version}_{decision.value}",
        workflow_id=wf.workflow_id,
        project_id=wf.project_id,
        expected_version=wf.version,
        decision=decision,
        disposition=disposition,
        actor="human-reviewer",
        reason="explicit checkpoint approval",
        timestamp=50 + wf.version,
        to_role_id=role,
    )


def test_create_is_deterministic_and_immutable() -> None:
    service = GovernedWorkflowService()
    first = service.create(plan(), created_at=10)
    second = service.create(plan(), created_at=10)
    assert first == second
    assert first.state == WorkflowState.ACTIVE
    with pytest.raises(Exception):
        first.version = 2


def test_success_requires_authorization_before_role_advance() -> None:
    service = GovernedWorkflowService()
    p = plan()
    waiting = service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31, next_role_id="reviewer")
    assert waiting.state == WorkflowState.AWAITING_AUTHORIZATION
    assert waiting.proposed_decision == WorkflowDecision.ADVANCE
    assert len(waiting.stages) == 1
    advanced = service.authorize(
        waiting,
        auth(waiting, WorkflowDecision.ADVANCE, role="reviewer"),
        next_plan=plan(role="reviewer", assignment="assign_2", plan_id="plan_2"),
    )
    assert advanced.state == WorkflowState.ACTIVE
    assert advanced.stages[-1].role_id == "reviewer"
    assert advanced.authorizations[-1].actor == "human-reviewer"


def test_unauthorized_role_transition_fails_closed() -> None:
    service = GovernedWorkflowService()
    p = plan()
    with pytest.raises(PermissionError, match="exceeds"):
        service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31, next_role_id="release")


def test_success_cannot_skip_required_successor_roles() -> None:
    service = GovernedWorkflowService()
    p = plan()
    with pytest.raises(ValueError, match="must select"):
        service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31)


def test_stale_or_mismatched_authorization_is_rejected() -> None:
    service = GovernedWorkflowService()
    p = plan()
    waiting = service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31, next_role_id="reviewer")
    stale = auth(waiting, WorkflowDecision.ADVANCE, role="reviewer").model_copy(update={"expected_version": 1})
    with pytest.raises(ValueError, match="stale"):
        service.authorize(waiting, stale, next_plan=plan(role="reviewer", assignment="assign_2", plan_id="plan_2"))
    wrong = auth(waiting, WorkflowDecision.ADVANCE, role="tester")
    with pytest.raises(PermissionError, match="role"):
        service.authorize(waiting, wrong, next_plan=plan(role="tester", assignment="assign_2", plan_id="plan_2"))


def test_denied_advance_blocks_without_creating_stage() -> None:
    service = GovernedWorkflowService()
    p = plan()
    waiting = service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31, next_role_id="reviewer")
    denied = auth(waiting, WorkflowDecision.ADVANCE, disposition=AuthorizationDecision.DENIED, role="reviewer")
    blocked = service.authorize(waiting, denied)
    assert blocked.state == WorkflowState.BLOCKED
    assert len(blocked.stages) == 1


def test_retry_is_represented_but_never_automatic() -> None:
    service = GovernedWorkflowService()
    p = plan()
    waiting = service.record_result(service.create(p, created_at=10), p, result(p, ExecutionOutcome.FAILED), timestamp=31)
    assert waiting.proposed_decision == WorkflowDecision.RETRY
    assert len(waiting.stages) == 1
    retried = service.authorize(
        waiting,
        auth(waiting, WorkflowDecision.RETRY),
        next_plan=plan(assignment="assign_retry", plan_id="plan_retry"),
    )
    assert retried.stages[-1].role_id == "builder"
    assert len(retried.stages) == 2


def test_success_without_next_role_requires_promotion_authorization() -> None:
    service = GovernedWorkflowService()
    p = plan(role="release")
    waiting = service.record_result(service.create(p, created_at=10), p, result(p), timestamp=31)
    assert waiting.proposed_decision == WorkflowDecision.PROMOTE
    completed = service.authorize(waiting, auth(waiting, WorkflowDecision.PROMOTE))
    assert completed.state == WorkflowState.COMPLETED
    assert completed.events[-1].event_type == "promote_authorized"


def test_cross_project_result_is_rejected() -> None:
    service = GovernedWorkflowService()
    p = plan()
    foreign = result(p).model_copy(update={"project_id": "project_2"})
    with pytest.raises(ValueError, match="outside"):
        service.record_result(service.create(p, created_at=10), p, foreign, timestamp=31)


def test_audit_records_reject_secret_markers() -> None:
    workflow = GovernedWorkflowService().create(plan(role="release"), created_at=10)
    with pytest.raises(ValueError, match="secrets"):
        WorkflowAuthorization(
            authorization_id="auth_secret",
            workflow_id=workflow.workflow_id,
            project_id=workflow.project_id,
            expected_version=workflow.version,
            decision=WorkflowDecision.PROMOTE,
            disposition=AuthorizationDecision.APPROVED,
            actor="operator",
            reason="token=do-not-retain",
            timestamp=20,
        )
