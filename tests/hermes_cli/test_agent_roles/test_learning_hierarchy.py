from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hermes_cli.agent_roles import (
    GovernedLearningHierarchy,
    InMemoryLearningHierarchyStore,
    LearningCapability,
    LearningDecisionState,
    LearningHierarchyStore,
    LearningHierarchyVisibilityService,
    LearningNodeRole,
    LearningNodeState,
    LearningRequest,
    LearningRoute,
    LessonPackage,
)
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def little_sister(
    *,
    available: bool = True,
    capabilities=(
        LearningCapability.LOCAL_MEMORY,
        LearningCapability.LOCAL_REASONING,
        LearningCapability.FINANCIAL_SENTIMENT,
        LearningCapability.REMOTE_GATEWAY,
    ),
):
    return LearningNodeState(
        node_id="titan",
        role=LearningNodeRole.LITTLE_SISTER,
        available=available,
        capabilities=capabilities,
        evidence_references=("inventory://titan/current",),
    )


def big_sister(
    *,
    available: bool = True,
    capabilities=(
        LearningCapability.BIG_SISTER_TEACHING,
        LearningCapability.CLOUD_SPECIALIST,
    ),
):
    return LearningNodeState(
        node_id="mac-hermes",
        role=LearningNodeRole.BIG_SISTER,
        available=available,
        capabilities=capabilities,
        evidence_references=("inventory://mac/current",),
    )


def request(**changes):
    values = {
        "request_id": "learn-31",
        "project_id": "hermes-platform",
        "task_id": "task-31",
        "task_type": "engineering",
        "objective_reference": "context://step31/objective",
        "required_capabilities": (
            LearningCapability.LOCAL_REASONING,
        ),
        "attempted_routes": (),
        "attempt_evidence_references": (),
        "budget_limit_micros": 0,
        "remote_gateway_permitted": True,
        "big_sister_escalation_permitted": True,
        "cloud_specialist_permitted": False,
        "cloud_specialist_requires_approval": True,
        "maximum_learning_depth": 6,
        "created_at": 100,
        "idempotency_key": "learning-31-idempotency",
    }
    values.update(changes)
    return LearningRequest(**values)


def decide(item=None, *, little=None, big=None, timestamp=101):
    return GovernedLearningHierarchy().decide(
        item or request(),
        little_sister=little or little_sister(),
        big_sister=big or big_sister(),
        timestamp=timestamp,
    )


def test_request_is_frozen_sanitized_and_deterministic():
    item = request()
    assert item.fingerprint == request().fingerprint

    with pytest.raises(ValidationError):
        item.task_type = "changed"

    with pytest.raises(ValidationError, match="sanitized reference"):
        request(objective_reference="raw task contents")

    with pytest.raises(ValidationError, match="sensitive"):
        request(objective_reference="context://prompt=private")


def test_local_memory_is_always_preferred_when_available():
    decision = decide()

    assert decision.selected_route is LearningRoute.LOCAL_MEMORY
    assert decision.state is LearningDecisionState.ROUTED
    assert not decision.requires_approval
    assert not decision.execution_permitted
    assert decision.fallback_chain[:2] == (
        LearningRoute.LOCAL_OLLAMA,
        LearningRoute.FREELLMAPI,
    )


def test_finbert_precedes_general_reasoning_for_financial_sentiment():
    decision = decide(
        request(
            required_capabilities=(
                LearningCapability.FINANCIAL_SENTIMENT,
            ),
            attempted_routes=(LearningRoute.LOCAL_MEMORY,),
            attempt_evidence_references=(
                "execution://local-memory/miss",
            ),
        )
    )

    assert decision.selected_route is LearningRoute.FINBERT


def test_route_progresses_local_then_gateway_then_big_sister():
    item = request(
        attempted_routes=(
            LearningRoute.LOCAL_MEMORY,
            LearningRoute.LOCAL_OLLAMA,
        ),
        attempt_evidence_references=(
            "execution://memory/miss",
            "execution://ollama/insufficient",
        ),
    )
    gateway = decide(item)
    assert gateway.selected_route is LearningRoute.FREELLMAPI

    sister = decide(
        item.model_copy(
            update={
                "attempted_routes": (
                    LearningRoute.LOCAL_MEMORY,
                    LearningRoute.LOCAL_OLLAMA,
                    LearningRoute.FREELLMAPI,
                )
            }
        )
    )
    assert sister.selected_route is LearningRoute.BIG_SISTER


def test_offline_big_sister_creates_deferred_lesson_not_system_failure():
    item = request(
        attempted_routes=(
            LearningRoute.LOCAL_MEMORY,
            LearningRoute.LOCAL_OLLAMA,
            LearningRoute.FREELLMAPI,
        ),
        attempt_evidence_references=(
            "execution://memory/miss",
            "execution://ollama/insufficient",
            "execution://gateway/unavailable",
        ),
    )
    decision = decide(item, big=big_sister(available=False))

    assert decision.selected_route is LearningRoute.DEFERRED_LESSON
    assert decision.state is LearningDecisionState.DEFERRED
    assert decision.lesson_request is not None
    assert (
        decision.lesson_request.requested_by
        is LearningNodeRole.LITTLE_SISTER
    )
    assert (
        decision.lesson_request.requested_from
        is LearningNodeRole.BIG_SISTER
    )
    assert not decision.execution_permitted


def test_cloud_specialist_never_bypasses_approval():
    item = request(
        cloud_specialist_permitted=True,
        attempted_routes=(
            LearningRoute.LOCAL_MEMORY,
            LearningRoute.LOCAL_OLLAMA,
            LearningRoute.FREELLMAPI,
            LearningRoute.BIG_SISTER,
        ),
        attempt_evidence_references=(
            "execution://memory/miss",
            "execution://ollama/miss",
            "execution://gateway/miss",
            "lesson://big-sister/unresolved",
        ),
    )
    decision = decide(item)

    assert (
        decision.selected_route
        is LearningRoute.CLOUD_SPECIALIST_APPROVAL_REQUIRED
    )
    assert decision.state is LearningDecisionState.APPROVAL_REQUIRED
    assert decision.requires_approval
    assert not decision.execution_permitted


def test_learning_depth_limit_fails_closed():
    item = request(
        maximum_learning_depth=3,
        attempted_routes=(
            LearningRoute.LOCAL_MEMORY,
            LearningRoute.LOCAL_OLLAMA,
            LearningRoute.FREELLMAPI,
        ),
    )
    decision = decide(item)

    assert decision.selected_route is LearningRoute.BLOCKED
    assert decision.state is LearningDecisionState.BLOCKED
    assert decision.reason_codes == ("learning_depth_limit",)


def test_node_roles_cannot_be_reversed():
    with pytest.raises(ValueError, match="little_sister"):
        GovernedLearningHierarchy().decide(
            request(),
            little_sister=big_sister(),
            big_sister=big_sister(),
            timestamp=101,
        )


def test_lesson_package_requires_instruction_verification_and_safety():
    package = LessonPackage(
        lesson_id="lesson-31",
        lesson_request_id="lesson-request-31",
        instruction_references=("memory://lesson/instructions",),
        verification_references=("test://lesson/verification",),
        safety_policy_references=("policy://learning/safety",),
        created_at=100,
    )
    assert package.version == 1

    with pytest.raises(ValidationError, match="verification"):
        LessonPackage(
            lesson_id="lesson-31",
            lesson_request_id="lesson-request-31",
            instruction_references=("memory://lesson/instructions",),
            verification_references=(),
            safety_policy_references=("policy://learning/safety",),
            created_at=100,
        )


def test_in_memory_store_is_idempotent_and_collision_safe():
    store = InMemoryLearningHierarchyStore()
    decision = decide()

    assert store.save(decision) == decision
    assert store.save(decision) == decision
    assert store.get(decision.decision_id) == decision

    conflicting = decision.model_copy(
        update={"reason_codes": ("different",)}
    )
    with pytest.raises(ValueError, match="collision"):
        store.save(conflicting)


def test_durable_store_round_trips_and_recovers_torn_tail(tmp_path):
    store = LearningHierarchyStore(tmp_path)
    decision = decide()

    assert store.save(decision) == decision
    assert store.list() == (decision,)

    with store.journal_path.open("ab") as handle:
        handle.write(b'{"torn":')

    assert store.list() == (decision,)
    assert store.journal_path.read_bytes().endswith(b"\n")


def test_visibility_is_registered_idempotent_and_decision_only(tmp_path):
    mission = MissionControlService(MissionControlStore(tmp_path))
    service = LearningHierarchyVisibilityService(mission)
    decision = decide()

    first = service.publish(decision)
    second = service.publish(decision)

    assert first == second
    assert first.selected_route is LearningRoute.LOCAL_MEMORY
    assert first.execution_state == "decision_only"
    assert mission.event_count(decision.project_id) == 1

    event = mission.get_events(decision.project_id)[0]
    assert isinstance(event, TelemetryEvent)
    assert event.event_type == "learning_hierarchy_recorded"


def test_learning_evidence_contains_no_raw_prompt_or_credentials():
    decision = decide(
        request(
            attempted_routes=(LearningRoute.LOCAL_MEMORY,),
            attempt_evidence_references=("execution://memory/miss",),
        )
    )
    encoded = json.dumps(decision.model_dump(mode="json")).lower()

    for forbidden in (
        "raw_prompt",
        "api_key",
        "authorization",
        "bearer ",
        "password",
        "private_key",
        "secret=",
        "token=",
    ):
        assert forbidden not in encoded
