"""Governed runtime supervision and health monitoring.

This boundary observes running executions, detects staleness or degradation,
and publishes health events to Mission Control. It never cancels, retries,
or promotes autonomously.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .execution import ExecutionOutcome
from .execution_planning import RoleExecutionPlan
from .runtime_execution import (
    GovernedRuntimeExecutionCoordinator,
    RuntimeExecutionRecord,
    RuntimeExecutionState,
)
from .runtime_supervision_store import (
    DEFAULT_STALENESS_THRESHOLD_SECONDS,
    RuntimeSupervisionStore,
    SupervisionStatus,
)
from .runtime_supervision_visibility import (
    RuntimeSupervisionVisibilityService,
)


RUNTIME_SUPERVISION_SCHEMA_VERSION = 1


class SupervisionOutcome(str, Enum):
    HEALTHY = "healthy"
    STALE = "stale"
    DEGRADED = "degraded"
    RECOVERED = "recovered"


class SupervisionEvent(BaseModel):
    """One immutable supervision observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = RUNTIME_SUPERVISION_SCHEMA_VERSION
    supervision_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    execution_id: str = Field(..., min_length=1, max_length=128)
    revision: int = Field(..., ge=1)
    status: SupervisionStatus
    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    observed_at: int = Field(..., ge=0)
    heartbeat_age_seconds: int = Field(..., ge=0)
    heartbeat_threshold_seconds: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=1024)


class _RuntimeExecutionStore(Protocol):
    def get(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[RuntimeExecutionRecord]: ...


class _RuntimeExecutionVisibility(Protocol):
    def get_summary(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[RuntimeExecutionRecord]: ...


class GovernedRuntimeSupervisionCoordinator:
    """Orchestrate health observations and publish to Mission Control."""

    def __init__(
        self,
        *,
        executions: _RuntimeExecutionStore,
        supervisions: RuntimeSupervisionStore,
        visibility: Optional[RuntimeSupervisionVisibilityService] = None,
    ) -> None:
        self._executions = executions
        self._supervisions = supervisions
        self._visibility = visibility

    def observe_execution(
        self,
        *,
        project_id: str,
        execution_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        correlation_id: str,
        timestamp: int,
        threshold_seconds: int = DEFAULT_STALENESS_THRESHOLD_SECONDS,
    ) -> SupervisionEvent:
        """Record one health observation for a running execution."""
        execution = self._executions.get(project_id, execution_id)
        if execution is None:
            raise RuntimeSupervisionError("runtime execution not found")
        if execution.state != RuntimeExecutionState.RUNNING:
            raise RuntimeSupervisionError("only running executions can be supervised")
        if execution.started_at is None:
            raise RuntimeSupervisionError("execution has not started")

        # Validate identity binding
        if (
            execution.project_id != project_id
            or execution.assignment_id != plan.assignment_id
            or execution.plan_id != plan.plan_id
            or execution.role_id != plan.role_id
            or execution.agent_id != plan.agent_id
        ):
            raise RuntimeSupervisionError("execution identity mismatch")

        # Compute health state
        last_heartbeat_at = execution.last_heartbeat_at
        heartbeat_age = 0
        if last_heartbeat_at is not None:
            heartbeat_age = timestamp - last_heartbeat_at
        else:
            heartbeat_age = timestamp - execution.started_at

        if heartbeat_age < 0:
            raise RuntimeSupervisionError("heartbeat timestamp mismatch")

        status = SupervisionStatus.HEALTHY
        if heartbeat_age >= threshold_seconds:
            status = SupervisionStatus.STALE
            reason = f"heartbeat age {heartbeat_age}s exceeds {threshold_seconds}s threshold"
        else:
            reason = f"healthy heartbeat age {heartbeat_age}s"

        # If a previous supervision record exists, check for recovery
        previous = self._supervisions.get_latest(project_id, execution_id)
        was_stale = previous and previous.status == SupervisionStatus.STALE
        if was_stale and status != SupervisionStatus.STALE:
            status = SupervisionStatus.RECOVERED
            reason = f"recovered from staleness (age {heartbeat_age}s)"

        # Record in append-only journal
        supervision = self._supervisions.observe(
            project_id=project_id,
            execution_id=execution_id,
            status=status,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=execution.fingerprint,
            observed_at=timestamp,
            last_heartbeat_at=last_heartbeat_at,
            started_at=execution.started_at,
            heartbeat_threshold_seconds=threshold_seconds,
            reason=reason,
        )

        event = SupervisionEvent(
            supervision_id=f"supervision_{supervision.checksum[:24]}",
            project_id=project_id,
            execution_id=execution_id,
            revision=supervision.revision,
            status=supervision.status,
            actor_id=actor_id,
            correlation_id=correlation_id,
            causation_id=supervision.causation_id,
            observed_at=supervision.observed_at,
            heartbeat_age_seconds=heartbeat_age,
            heartbeat_threshold_seconds=threshold_seconds,
            reason=reason,
        )

        if self._visibility:
            try:
                self._visibility.publish(supervision)
            except Exception as exc:
                raise RuntimeSupervisionPublicationError(event, "Mission Control") from exc

        return event

    def get_status(
        self,
        project_id: str,
        execution_id: str,
    ) -> Optional[SupervisionStatus]:
        """Return the latest supervision status for an execution, or None."""
        record = self._supervisions.get_latest(project_id, execution_id)
        return record.status if record else None

    def list_stale_executions(
        self,
        project_id: str,
    ) -> Tuple[str, ...]:
        """Return execution IDs currently marked STALE."""
        records = self._supervisions.list_executions(project_id, status=SupervisionStatus.STALE)
        return tuple(record.execution_id for record in records)


class RuntimeSupervisionError(RuntimeError):
    """Fail-closed supervision violation or invariant failure."""


class RuntimeSupervisionPublicationError(RuntimeSupervisionError):
    """Supervision event persisted but Mission Control publication failed."""

    def __init__(self, event: SupervisionEvent, target: str) -> None:
        super().__init__(
            f"supervision event persisted but {target} publication failed; reconcile"
        )
        self.event = event
        self.target = target
