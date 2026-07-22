"""Step 14 governed runtime supervision certification."""

from __future__ import annotations

import pytest

from hermes_cli.agent_roles.runtime_supervision import (
    GovernedRuntimeSupervisionCoordinator,
    RuntimeSupervisionError,
    RuntimeSupervisionPublicationError,
    SupervisionEvent,
    SupervisionOutcome,
    RUNTIME_SUPERVISION_SCHEMA_VERSION,
)
from hermes_cli.agent_roles.runtime_supervision_store import (
    RuntimeSupervisionStore,
    SupervisionStatus,
)
from hermes_cli.agent_roles.runtime_supervision_visibility import (
    RuntimeSupervisionVisibilityAdapter,
    RuntimeSupervisionVisibilityRecord,
    RuntimeSupervisionVisibilityService,
    RUNTIME_SUPERVISION_VISIBILITY_EVENT,
)
from hermes_cli.agent_roles.runtime_execution import (
    GovernedRuntimeExecutionCoordinator,
    RuntimeExecutionRecord,
    RuntimeExecutionState,
)
from hermes_cli.agent_roles.runtime_execution_store import RuntimeExecutionStore
from hermes_cli.agent_roles.execution_planning import (
    ExecutionAction,
    ExecutionPlanStep,
    RoleExecutionPlan,
)
from hermes_cli.agent_roles.execution import ExecutionOutcome, ExecutionResult
from hermes_cli.agent_roles.runtime_session import (
    RuntimeSession,
    RuntimeSessionEvent,
    RuntimeSessionState,
    RuntimeSessionTransition,
)
from hermes_cli.agent_roles.models import AssignmentStatus
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.store import MissionControlStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_path(tmp_path):
    import os
    os.umask(0o077)
    return tmp_path


@pytest.fixture
def execution_store(tmp_path):
    return RuntimeExecutionStore(tmp_path / "runtime-executions")


@pytest.fixture
def supervision_store(tmp_path):
    return RuntimeSupervisionStore(tmp_path / "runtime-supervisions")


@pytest.fixture
def mission_control(tmp_path):
    return MissionControlService(
        store=MissionControlStore(tmp_path / "mission-control")
    )


@pytest.fixture
def visibility_service(mission_control):
    return RuntimeSupervisionVisibilityService(mission_control)


@pytest.fixture
def supervisor(execution_store, supervision_store, visibility_service):
    return GovernedRuntimeSupervisionCoordinator(
        executions=execution_store,
        supervisions=supervision_store,
        visibility=visibility_service,
    )


@pytest.fixture
def plan():
    return RoleExecutionPlan(
        project_id="proj001",
        assignment_id="assign001",
        plan_id="plan001",
        role_id="role001",
        agent_id="agent001",
        contract_id="contract001",
        responsibilities=("Review runtime health without executing work",),
        allowed_actions=(ExecutionAction.REVIEW,),
        allowed_next_roles=(),
        steps=(ExecutionPlanStep(
            sequence=1,
            action=ExecutionAction.REVIEW,
            responsibility="Review runtime health without executing work",
            required_evidence=("runtime_health",),
        ),),
        created_at=1000,
    )


@pytest.fixture
def execution(plan):
    session = RuntimeSession(
        session_id="sess001",
        project_id="proj001",
        assignment_id="assign001",
        role_id="role001",
        agent_id="agent001",
        contract_id="contract001",
        receipt_id="receipt001",
        request_fingerprint="re" * 32,
        adapter_name="test-adapter",
        adapter_version="1",
        runtime="python",
        state=RuntimeSessionState.RUNNING,
        created_at=1000,
        updated_at=1000,
        execution_started=True,
        events=(
            RuntimeSessionEvent(
                transition=RuntimeSessionTransition.CREATED,
                state=RuntimeSessionState.CREATED,
                timestamp=1000,
                reason="session created",
            ),
            RuntimeSessionEvent(
                transition=RuntimeSessionTransition.MARKED_READY,
                state=RuntimeSessionState.READY,
                timestamp=1000,
                reason="session ready",
            ),
            RuntimeSessionEvent(
                transition=RuntimeSessionTransition.STARTED,
                state=RuntimeSessionState.RUNNING,
                timestamp=1000,
                reason="session started",
            ),
        ),
    )
    return RuntimeExecutionRecord(
        schema_version=1,
        execution_id="exec001",
        revision=2,
        state=RuntimeExecutionState.RUNNING,
        project_id="proj001",
        workflow_id="workflow001",
        workflow_version=1,
        run_id="run001",
        node_run_id="node001",
        dispatch_id="dispatch001",
        dispatch_fingerprint="dp" * 32,
        intent_id="intent001",
        intent_fingerprint="in" * 32,
        claim_id="claim001",
        session_id="sess001",
        contract_id="contract001",
        receipt_id="receipt001",
        assignment_id="assign001",
        plan_id="plan001",
        role_id="role001",
        agent_id="agent001",
        authorization_id="auth001",
        runtime="python",
        repository_root="/tmp/repo",
        engine=None,
        provider=None,
        model=None,
        actor_id="agent001",
        correlation_id="run001",
        causation_id="cause001",
        attempt=1,
        created_at=1000,
        updated_at=1000,
        started_at=1000,
        completed_at=None,
        last_heartbeat_at=1000,
        reason="started",
        result=None,
        session=session,
        evidence_refs=(),
    )


def persist_running(execution_store, execution):
    ready_session = RuntimeSession.model_validate({
        **execution.session.model_dump(mode="python"),
        "state": RuntimeSessionState.READY,
        "execution_started": False,
        "events": execution.session.events[:2],
    })
    ready = RuntimeExecutionRecord.model_validate({
        **execution.model_dump(mode="python"),
        "revision": 1,
        "state": RuntimeExecutionState.READY,
        "started_at": None,
        "last_heartbeat_at": None,
        "reason": "ready",
        "session": ready_session,
    })
    running = RuntimeExecutionRecord.model_validate({
        **execution.model_dump(mode="python"),
        "causation_id": ready.fingerprint,
    })
    execution_store.create(ready)
    execution_store.append(running, expected_revision=1)
    return running


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------

class TestRuntimeSupervisionStore:
    def test_create_observe(self, supervision_store):
        record = supervision_store.observe(
            project_id="proj001",
            execution_id="exec001",
            status=SupervisionStatus.HEALTHY,
            actor_id="agent001",
            correlation_id="corr001",
            causation_id="cause001",
            observed_at=2000,
            last_heartbeat_at=1000,
            started_at=1000,
            heartbeat_threshold_seconds=600,
            reason="healthy",
        )
        assert record.journal_sequence == 1
        assert record.project_id == "proj001"
        assert record.execution_id == "exec001"
        assert record.revision == 1
        assert record.status == SupervisionStatus.HEALTHY
        assert record.heartbeat_age_seconds == 1000

    def test_duplicate_observe_increments_revision(self, supervision_store):
        supervision_store.observe(
            project_id="proj001",
            execution_id="exec001",
            status=SupervisionStatus.HEALTHY,
            actor_id="agent001",
            correlation_id="corr001",
            causation_id="cause001",
            observed_at=2000,
            last_heartbeat_at=1000,
            started_at=1000,
            heartbeat_threshold_seconds=600,
            reason="healthy",
        )
        record2 = supervision_store.observe(
            project_id="proj001",
            execution_id="exec001",
            status=SupervisionStatus.STALE,
            actor_id="agent001",
            correlation_id="corr001",
            causation_id="cause002",
            observed_at=3000,
            last_heartbeat_at=1000,
            started_at=1000,
            heartbeat_threshold_seconds=600,
            reason="stale",
        )
        assert record2.revision == 2
        assert record2.heartbeat_age_seconds == 2000

    def test_get_latest(self, supervision_store):
        supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.HEALTHY, actor_id="a",
            correlation_id="c", causation_id="d",
            observed_at=1000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="h",
        )
        supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.STALE, actor_id="a",
            correlation_id="c", causation_id="d",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="s",
        )
        latest = supervision_store.get_latest("proj001", "exec001")
        assert latest is not None
        assert latest.status == SupervisionStatus.STALE
        assert latest.revision == 2

    def test_list_executions_filtered_by_status(self, supervision_store):
        supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.HEALTHY, actor_id="a",
            correlation_id="c", causation_id="d",
            observed_at=1000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="h",
        )
        supervision_store.observe(
            project_id="proj001", execution_id="exec002",
            status=SupervisionStatus.STALE, actor_id="a",
            correlation_id="c", causation_id="d",
            observed_at=1000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="s",
        )
        stale = supervision_store.list_executions(
            "proj001", status=SupervisionStatus.STALE
        )
        assert len(stale) == 1
        assert stale[0].execution_id == "exec002"

    def test_torn_tail_recovery(self, supervision_store, tmp_path):
        import os
        supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.HEALTHY, actor_id="a",
            correlation_id="c", causation_id="d",
            observed_at=1000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="h",
        )
        journal_path = tmp_path / "runtime-supervisions" / "proj001" / "runtime-supervisions.jsonl"
        with journal_path.open("ab") as f:
            f.write(b'{"corrupt": true')
        latest = supervision_store.get_latest("proj001", "exec001")
        assert latest is not None
        assert latest.status == SupervisionStatus.HEALTHY

    def test_heartbeat_before_start_rejected(self, supervision_store):
        with pytest.raises(ValueError, match="heartbeat timestamp is before"):
            supervision_store.observe(
                project_id="proj001", execution_id="exec001",
                status=SupervisionStatus.HEALTHY, actor_id="a",
                correlation_id="c", causation_id="d",
                observed_at=1000, last_heartbeat_at=1000, started_at=1001,
                heartbeat_threshold_seconds=600, reason="bad",
            )

    def test_observed_before_heartbeat_rejected(self, supervision_store):
        with pytest.raises(ValueError, match="observed_at cannot precede"):
            supervision_store.observe(
                project_id="proj001", execution_id="exec001",
                status=SupervisionStatus.HEALTHY, actor_id="a",
                correlation_id="c", causation_id="d",
                observed_at=500, last_heartbeat_at=1000, started_at=0,
                heartbeat_threshold_seconds=600, reason="bad",
            )


# ---------------------------------------------------------------------------
# Supervision coordinator tests
# ---------------------------------------------------------------------------

class TestGovernedRuntimeSupervisionCoordinator:
    def test_observe_healthy(self, supervisor, execution_store, execution, plan):
        persist_running(execution_store, execution)
        event = supervisor.observe_execution(
            project_id="proj001",
            execution_id="exec001",
            plan=plan,
            actor_id="agent001",
            correlation_id="corr001",
            timestamp=1500,
            threshold_seconds=600,
        )
        assert event.status == SupervisionStatus.HEALTHY
        assert event.heartbeat_age_seconds == 500
        assert "healthy" in event.reason.lower()

    def test_observe_stale(self, supervisor, execution_store, execution, plan):
        persist_running(execution_store, execution)
        event = supervisor.observe_execution(
            project_id="proj001",
            execution_id="exec001",
            plan=plan,
            actor_id="agent001",
            correlation_id="corr001",
            timestamp=2000,
            threshold_seconds=600,
        )
        assert event.status == SupervisionStatus.STALE
        assert event.heartbeat_age_seconds == 1000
        assert "exceeds" in event.reason.lower()

    def test_observe_recovered(self, supervisor, execution_store, supervision_store, execution, plan):
        persist_running(execution_store, execution)
        # First observation: stale
        supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.STALE, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=1500, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="stale",
        )
        # Second observation: healthy (recovered)
        event = supervisor.observe_execution(
            project_id="proj001",
            execution_id="exec001",
            plan=plan,
            actor_id="agent001",
            correlation_id="corr001",
            timestamp=1500,
            threshold_seconds=600,
        )
        assert event.status == SupervisionStatus.RECOVERED
        assert "recovered" in event.reason.lower()

    def test_observe_non_running_execution_rejected(self, supervisor, execution_store, execution, plan):
        ready_session = RuntimeSession.model_validate({
            **execution.session.model_dump(mode="python"),
            "state": RuntimeSessionState.READY,
            "execution_started": False,
            "events": execution.session.events[:2],
        })
        ready = RuntimeExecutionRecord.model_validate({
            **execution.model_dump(mode="python"),
            "revision": 1,
            "state": RuntimeExecutionState.READY,
            "started_at": None,
            "last_heartbeat_at": None,
            "reason": "ready",
            "session": ready_session,
        })
        execution_store.create(ready)
        with pytest.raises(RuntimeSupervisionError, match="only running executions"):
            supervisor.observe_execution(
                project_id="proj001",
                execution_id="exec001",
                plan=plan,
                actor_id="agent001",
                correlation_id="corr001",
                timestamp=2000,
                threshold_seconds=600,
            )

    def test_observe_execution_not_found_rejected(self, supervisor, plan):
        with pytest.raises(RuntimeSupervisionError, match="runtime execution not found"):
            supervisor.observe_execution(
                project_id="proj001",
                execution_id="nonexistent",
                plan=plan,
                actor_id="agent001",
                correlation_id="corr001",
                timestamp=2000,
                threshold_seconds=600,
            )

    def test_observe_identity_mismatch_rejected(self, supervisor, execution_store, execution, plan):
        persist_running(execution_store, execution)
        bad_plan = RoleExecutionPlan.model_validate({
            **plan.model_dump(mode="python"),
            "assignment_id": "wrong",
        })
        with pytest.raises(RuntimeSupervisionError, match="execution identity mismatch"):
            supervisor.observe_execution(
                project_id="proj001",
                execution_id="exec001",
                plan=bad_plan,
                actor_id="agent001",
                correlation_id="corr001",
                timestamp=2000,
                threshold_seconds=600,
            )

    def test_get_status(self, supervisor, execution_store, supervision_store, execution, plan):
        persist_running(execution_store, execution)
        supervisor.observe_execution(
            project_id="proj001",
            execution_id="exec001",
            plan=plan,
            actor_id="agent001",
            correlation_id="corr001",
            timestamp=1500,
            threshold_seconds=600,
        )
        status = supervisor.get_status("proj001", "exec001")
        assert status == SupervisionStatus.HEALTHY

    def test_get_status_not_found(self, supervisor):
        status = supervisor.get_status("proj001", "nonexistent")
        assert status is None

    def test_list_stale_executions(self, supervisor, execution_store, supervision_store, execution, plan):
        persist_running(execution_store, execution)
        supervisor.observe_execution(
            project_id="proj001",
            execution_id="exec001",
            plan=plan,
            actor_id="agent001",
            correlation_id="corr001",
            timestamp=2000,
            threshold_seconds=600,
        )
        stale = supervisor.list_stale_executions("proj001")
        assert "exec001" in stale


# ---------------------------------------------------------------------------
# Visibility tests
# ---------------------------------------------------------------------------

class TestRuntimeSupervisionVisibility:
    def test_visibility_adapter_to_event(self, supervision_store):
        record = supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.STALE, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="stale",
        )
        adapter = RuntimeSupervisionVisibilityAdapter()
        event = adapter.to_event(record)
        assert event.event_type == RUNTIME_SUPERVISION_VISIBILITY_EVENT
        assert event.project_id == "proj001"
        assert event.task_id == "exec001"
        assert event.severity == "warning"
        assert event.correlation_id == "corr001"
        assert event.causation_id == "cause001"

    def test_visibility_adapter_from_events(self, mission_control):
        adapter = RuntimeSupervisionVisibilityAdapter()
        event = TelemetryEvent(
            event_id="test_id",
            event_type=RUNTIME_SUPERVISION_VISIBILITY_EVENT,
            project_id="proj001",
            task_id="exec001",
            agent_id="agent001",
            timestamp=2000,
            severity="warning",
            correlation_id="corr001",
            causation_id="cause001",
            payload={
                "record": {
                    "project_id": "proj001",
                    "execution_id": "exec001",
                    "actor_id": "agent001",
                    "revision": 1,
                    "status": "stale",
                    "heartbeat_age_seconds": 1000,
                    "heartbeat_threshold_seconds": 600,
                },
                "source": "runtime_supervision",
                "source_idempotency_key": "runtime_supervision:exec001:1",
            },
        )
        mission_control.append_event_once(event)
        records = adapter.from_events(mission_control.get_events("proj001"))
        assert len(records) == 1
        assert records[0].execution_id == "exec001"
        assert records[0].status == "stale"

    def test_visibility_service_publish(self, supervision_store, mission_control):
        service = RuntimeSupervisionVisibilityService(mission_control)
        record = supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.DEGRADED, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="degraded",
        )
        projected = service.publish(record)
        assert projected.execution_id == "exec001"
        assert projected.status == "degraded"

    def test_visibility_service_list_records(self, supervision_store, mission_control):
        service = RuntimeSupervisionVisibilityService(mission_control)
        healthy_record = supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.HEALTHY, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="healthy",
        )
        stale_record = supervision_store.observe(
            project_id="proj001", execution_id="exec002",
            status=SupervisionStatus.STALE, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="stale",
        )
        service.publish(healthy_record)
        service.publish(stale_record)
        records = service.list_records("proj001")
        assert len(records) == 2
        ids = {r.execution_id for r in records}
        assert ids == {"exec001", "exec002"}

    def test_visibility_service_filter_by_status(self, supervision_store, mission_control):
        service = RuntimeSupervisionVisibilityService(mission_control)
        healthy_record = supervision_store.observe(
            project_id="proj001", execution_id="exec001",
            status=SupervisionStatus.HEALTHY, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="healthy",
        )
        stale_record = supervision_store.observe(
            project_id="proj001", execution_id="exec002",
            status=SupervisionStatus.STALE, actor_id="agent001",
            correlation_id="corr001", causation_id="cause001",
            observed_at=2000, last_heartbeat_at=1000, started_at=1000,
            heartbeat_threshold_seconds=600, reason="stale",
        )
        service.publish(healthy_record)
        service.publish(stale_record)
        stale = service.list_records("proj001", status=SupervisionStatus.STALE)
        assert len(stale) == 1
        assert stale[0].execution_id == "exec002"


# ---------------------------------------------------------------------------
# Schema version tests
# ---------------------------------------------------------------------------

class TestSchemaVersions:
    def test_supervision_schema_version(self):
        assert RUNTIME_SUPERVISION_SCHEMA_VERSION == 1

    def test_supervision_event_schema_version(self):
        event = SupervisionEvent(
            supervision_id="s1",
            project_id="proj001",
            execution_id="exec001",
            revision=1,
            status=SupervisionStatus.HEALTHY,
            actor_id="agent001",
            correlation_id="corr001",
            causation_id="cause001",
            observed_at=1000,
            heartbeat_age_seconds=0,
            heartbeat_threshold_seconds=600,
            reason="test",
        )
        assert event.schema_version == 1
