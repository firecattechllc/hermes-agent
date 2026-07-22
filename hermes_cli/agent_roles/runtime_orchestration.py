"""Governed orchestration across dispatch, runtime execution, and result finalization.

This boundary coordinates existing governed services only. It never executes a
provider, launches a worker, invents authority, authorizes progression, creates
a retry, or schedules a successor stage.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional, Protocol, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution import (
    ExecutionEvidence,
    ExecutionOutcome,
    FailureCategory,
)
from .execution_planning import RoleExecutionPlan
from .launch_validation import RuntimeCompatibility
from .runtime_execution import (
    GovernedRuntimeExecutionCoordinator,
    RuntimeExecutionRecord,
    RuntimeExecutionState,
    TERMINAL_RUNTIME_EXECUTION_STATES,
)
from .workflow import GovernedWorkflow
from .workflow_dispatch import (
    GovernedWorkflowDispatchCoordinator,
    WorkflowDispatchOutcome,
    WorkflowDispatchStatus,
)
from .workflow_result import WorkflowResultCoordinator
from .workflow_scheduling import WorkflowExecutionIntent


RUNTIME_ORCHESTRATION_SCHEMA_VERSION = 1
RUNTIME_ORCHESTRATION_JOURNAL = "runtime-orchestrations.jsonl"


class RuntimeOrchestrationState(str, Enum):
    PREPARED = "prepared"
    READY = "ready"
    RUNNING = "running"
    TERMINAL = "terminal"
    FINALIZED = "finalized"


class RuntimeOrchestrationRecord(BaseModel):
    """One immutable revision of a governed orchestration lifecycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = RUNTIME_ORCHESTRATION_SCHEMA_VERSION
    orchestration_id: str = Field(..., min_length=1, max_length=128)
    revision: int = Field(..., ge=1)
    state: RuntimeOrchestrationState

    project_id: str = Field(..., min_length=1, max_length=128)
    workflow_id: str = Field(..., min_length=1, max_length=128)
    workflow_version: int = Field(..., ge=1)
    run_id: str = Field(..., min_length=1, max_length=128)

    intent_id: str = Field(..., min_length=1, max_length=128)
    dispatch_id: str = Field(..., min_length=1, max_length=128)
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    result_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    assignment_id: str = Field(..., min_length=1, max_length=128)
    plan_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)

    actor_id: str = Field(..., min_length=1, max_length=256)
    correlation_id: str = Field(..., min_length=1, max_length=128)
    causation_id: str = Field(..., min_length=1, max_length=256)
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=2048)
    evidence_refs: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeOrchestrationRecord":
        if self.schema_version != RUNTIME_ORCHESTRATION_SCHEMA_VERSION:
            raise ValueError("unsupported runtime orchestration schema version")
        if self.updated_at < self.created_at:
            raise ValueError("runtime orchestration timestamp regressed")
        if self.correlation_id != self.run_id:
            raise ValueError("runtime orchestration correlation must equal run_id")

        requires_execution = {
            RuntimeOrchestrationState.READY,
            RuntimeOrchestrationState.RUNNING,
            RuntimeOrchestrationState.TERMINAL,
            RuntimeOrchestrationState.FINALIZED,
        }
        if self.state in requires_execution and self.execution_id is None:
            raise ValueError("runtime orchestration state requires execution identity")

        requires_result = {
            RuntimeOrchestrationState.TERMINAL,
            RuntimeOrchestrationState.FINALIZED,
        }
        if self.state in requires_result and self.result_id is None:
            raise ValueError("terminal orchestration state requires result identity")

        return self

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class RuntimeOrchestrationError(RuntimeError):
    """Fail-closed orchestration boundary violation."""


class RuntimeOrchestrationPublicationError(RuntimeOrchestrationError):
    """Orchestration state persisted but visibility publication failed."""

    def __init__(self, record: RuntimeOrchestrationRecord) -> None:
        super().__init__(
            "runtime orchestration persisted but Mission Control publication "
            "failed; reconcile"
        )
        self.record = record


class _RuntimeOrchestrationVisibility(Protocol):
    def publish(self, record: RuntimeOrchestrationRecord): ...


class _WorkflowScheduling(Protocol):
    def get(
        self,
        project_id: str,
        intent_id: str,
    ) -> Optional[WorkflowExecutionIntent]: ...

    def get_revision(
        self,
        project_id: str,
        intent_id: str,
        version: int,
    ) -> Optional[WorkflowExecutionIntent]: ...


def _safe_project_id(project_id: str) -> str:
    value = project_id.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid runtime orchestration project_id")
    return value


class RuntimeOrchestrationJournalRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    journal_sequence: int = Field(..., ge=1)
    project_id: str
    orchestration_id: str
    revision: int = Field(..., ge=1)
    record: RuntimeOrchestrationRecord
    checksum: str = Field(..., min_length=64, max_length=64)

    @staticmethod
    def calculate_checksum(
        sequence: int,
        record: RuntimeOrchestrationRecord,
    ) -> str:
        payload = {
            "journal_sequence": sequence,
            "project_id": record.project_id,
            "orchestration_id": record.orchestration_id,
            "revision": record.revision,
            "record": record.model_dump(mode="json"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def _validate_record(self) -> "RuntimeOrchestrationJournalRecord":
        if (
            self.project_id != self.record.project_id
            or self.orchestration_id != self.record.orchestration_id
            or self.revision != self.record.revision
            or self.checksum
            != self.calculate_checksum(self.journal_sequence, self.record)
        ):
            raise ValueError("runtime orchestration journal association mismatch")
        return self


class RuntimeOrchestrationStore:
    """Append-only, project-isolated orchestration revision storage."""

    def __init__(self, root: Path, *, capacity: int = 1024) -> None:
        if capacity < 1 or capacity > 100_000:
            raise ValueError(
                "runtime orchestration capacity must be between 1 and 100000"
            )
        self.root = Path(root)
        self.capacity = capacity
        self._thread_lock = threading.RLock()

    def journal_path(self, project_id: str) -> Path:
        return (
            self.root
            / _safe_project_id(project_id)
            / RUNTIME_ORCHESTRATION_JOURNAL
        )

    @contextmanager
    def _write_lock(self, project_id: str) -> Iterator[None]:
        path = self.journal_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        lock_path = path.with_suffix(".lock")
        with self._thread_lock:
            with lock_path.open("a+", encoding="utf-8") as handle:
                os.chmod(lock_path, 0o600)
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def create(
        self,
        record: RuntimeOrchestrationRecord,
    ) -> RuntimeOrchestrationRecord:
        if (
            record.revision != 1
            or record.state != RuntimeOrchestrationState.PREPARED
        ):
            raise ValueError(
                "runtime orchestration creation requires prepared revision 1"
            )
        with self._write_lock(record.project_id):
            records = self._read_unlocked(
                record.project_id,
                recover_torn_tail=True,
            )
            matches = tuple(
                item.record
                for item in records
                if item.orchestration_id == record.orchestration_id
                or item.record.dispatch_id == record.dispatch_id
            )
            if matches:
                if len(matches) == 1 and matches[0] == record:
                    return record
                raise ValueError("runtime orchestration identity collision")
            return self._append_unlocked(records, record)

    def append(
        self,
        record: RuntimeOrchestrationRecord,
        *,
        expected_revision: int,
    ) -> RuntimeOrchestrationRecord:
        with self._write_lock(record.project_id):
            records = self._read_unlocked(
                record.project_id,
                recover_torn_tail=True,
            )
            history = tuple(
                item.record
                for item in records
                if item.orchestration_id == record.orchestration_id
            )
            if not history:
                raise KeyError(record.orchestration_id)
            current = history[-1]
            if current.revision != expected_revision:
                raise ValueError("runtime orchestration revision conflict")
            if record.revision != expected_revision + 1:
                raise ValueError(
                    "runtime orchestration revision must be contiguous"
                )
            self._validate_transition(current, record)
            return self._append_unlocked(records, record)

    def get(
        self,
        project_id: str,
        orchestration_id: str,
    ) -> Optional[RuntimeOrchestrationRecord]:
        history = self.history(project_id, orchestration_id)
        return history[-1] if history else None

    def find_by_dispatch(
        self,
        project_id: str,
        dispatch_id: str,
    ) -> Optional[RuntimeOrchestrationRecord]:
        matches = tuple(
            record
            for record in self.list(project_id)
            if record.dispatch_id == dispatch_id.strip()
        )
        if len(matches) > 1:
            raise ValueError("multiple orchestrations for one dispatch")
        return matches[0] if matches else None

    def history(
        self,
        project_id: str,
        orchestration_id: str,
    ) -> Tuple[RuntimeOrchestrationRecord, ...]:
        return tuple(
            item.record
            for item in self._read(project_id)
            if item.orchestration_id == orchestration_id.strip()
        )

    def list(
        self,
        project_id: str,
    ) -> Tuple[RuntimeOrchestrationRecord, ...]:
        latest: dict[str, RuntimeOrchestrationRecord] = {}
        for item in self._read(project_id):
            latest[item.orchestration_id] = item.record
        return tuple(latest[key] for key in sorted(latest))

    @staticmethod
    def _validate_transition(
        previous: RuntimeOrchestrationRecord,
        current: RuntimeOrchestrationRecord,
    ) -> None:
        immutable = {
            key
            for key in type(previous).model_fields
            if key
            not in {
                "revision",
                "state",
                "workflow_version",
                "execution_id",
                "result_id",
                "actor_id",
                "causation_id",
                "updated_at",
                "reason",
                "evidence_refs",
            }
        }
        if any(
            getattr(previous, key) != getattr(current, key)
            for key in immutable
        ):
            raise ValueError(
                "runtime orchestration authority changed across revisions"
            )

        allowed = {
            RuntimeOrchestrationState.PREPARED: {
                RuntimeOrchestrationState.READY,
            },
            RuntimeOrchestrationState.READY: {
                RuntimeOrchestrationState.RUNNING,
            },
            RuntimeOrchestrationState.RUNNING: {
                RuntimeOrchestrationState.TERMINAL,
            },
            RuntimeOrchestrationState.TERMINAL: {
                RuntimeOrchestrationState.FINALIZED,
            },
        }
        if (
            previous.state not in allowed
            or current.state not in allowed[previous.state]
        ):
            raise ValueError("illegal runtime orchestration transition")
        if current.updated_at < previous.updated_at:
            raise ValueError("runtime orchestration timestamp regressed")
        if current.causation_id != previous.fingerprint:
            raise ValueError("runtime orchestration causation mismatch")
        if (
            previous.evidence_refs
            != current.evidence_refs[: len(previous.evidence_refs)]
        ):
            raise ValueError("runtime orchestration evidence history changed")

    def _append_unlocked(
        self,
        records: Tuple[RuntimeOrchestrationJournalRecord, ...],
        record: RuntimeOrchestrationRecord,
    ) -> RuntimeOrchestrationRecord:
        if len(records) >= self.capacity:
            raise OverflowError("runtime orchestration capacity reached")
        sequence = len(records) + 1
        journal = RuntimeOrchestrationJournalRecord(
            journal_sequence=sequence,
            project_id=record.project_id,
            orchestration_id=record.orchestration_id,
            revision=record.revision,
            record=record,
            checksum=RuntimeOrchestrationJournalRecord.calculate_checksum(
                sequence,
                record,
            ),
        )
        line = json.dumps(
            journal.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        path = self.journal_path(record.project_id)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.chmod(path, 0o600)
            remaining = memoryview((line + "\n").encode())
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError(
                        "runtime orchestration journal write made no progress"
                    )
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def _read(
        self,
        project_id: str,
    ) -> Tuple[RuntimeOrchestrationJournalRecord, ...]:
        with self._write_lock(project_id):
            return self._read_unlocked(
                project_id,
                recover_torn_tail=True,
            )

    def _read_unlocked(
        self,
        project_id: str,
        *,
        recover_torn_tail: bool = False,
    ) -> Tuple[RuntimeOrchestrationJournalRecord, ...]:
        path = self.journal_path(project_id)
        if not path.exists():
            return ()

        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            boundary = raw.rfind(b"\n") + 1
            if recover_torn_tail:
                fd = os.open(path, os.O_WRONLY)
                try:
                    os.ftruncate(fd, boundary)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            raw = raw[:boundary]

        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError(
                "corrupt runtime orchestration journal encoding"
            ) from exc

        records = []
        previous_by_orchestration: dict[
            str,
            RuntimeOrchestrationRecord,
        ] = {}

        for line_number, line in enumerate(lines, 1):
            try:
                journal = RuntimeOrchestrationJournalRecord.model_validate_json(
                    line
                )
            except Exception as exc:
                raise ValueError(
                    "corrupt runtime orchestration journal at "
                    f"line {line_number}"
                ) from exc

            if journal.journal_sequence != line_number:
                raise ValueError(
                    "runtime orchestration journal sequence mismatch"
                )

            previous = previous_by_orchestration.get(
                journal.orchestration_id
            )
            if previous is None:
                if (
                    journal.revision != 1
                    or journal.record.state
                    != RuntimeOrchestrationState.PREPARED
                ):
                    raise ValueError(
                        "runtime orchestration journal must begin prepared"
                    )
            else:
                if journal.revision != previous.revision + 1:
                    raise ValueError(
                        "runtime orchestration journal revision gap"
                    )
                self._validate_transition(previous, journal.record)

            previous_by_orchestration[journal.orchestration_id] = (
                journal.record
            )
            records.append(journal)

        return tuple(records)


class GovernedRuntimeOrchestrationCoordinator:
    """Compose existing governed boundaries and persist their linkage."""

    def __init__(
        self,
        *,
        dispatch: GovernedWorkflowDispatchCoordinator,
        runtime: GovernedRuntimeExecutionCoordinator,
        results: WorkflowResultCoordinator,
        scheduling: _WorkflowScheduling,
        orchestrations: RuntimeOrchestrationStore,
        visibility: Optional[_RuntimeOrchestrationVisibility] = None,
    ) -> None:
        self._dispatch = dispatch
        self._runtime = runtime
        self._results = results
        self._scheduling = scheduling
        self._orchestrations = orchestrations
        self._visibility = visibility

    @staticmethod
    def orchestration_id_for(
        outcome: WorkflowDispatchOutcome,
    ) -> str:
        seed = (
            f"{outcome.project_id}|{outcome.workflow_id}|"
            f"{outcome.run_id}|{outcome.dispatch_id}"
        )
        return "runtime_orchestration_" + hashlib.sha256(
            seed.encode()
        ).hexdigest()[:24]

    def prepare(
        self,
        *,
        project_id: str,
        intent_id: str,
        expected_claim_id: str,
        plan: RoleExecutionPlan,
        compatibility: RuntimeCompatibility,
        repository_root: str,
        runtime: str,
        actor_id: str,
        timestamp: int,
        base_ref: Optional[str] = None,
        engine: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        environment: Tuple[Tuple[str, str], ...] = (),
    ) -> RuntimeOrchestrationRecord:
        outcome = self._dispatch.prepare(
            project_id=project_id,
            intent_id=intent_id,
            expected_claim_id=expected_claim_id,
            plan=plan,
            compatibility=compatibility,
            repository_root=repository_root,
            runtime=runtime,
            actor_id=actor_id,
            timestamp=timestamp,
            base_ref=base_ref,
            engine=engine,
            provider=provider,
            model=model,
            environment=environment,
        )
        if (
            outcome.status != WorkflowDispatchStatus.PREPARED
            or outcome.session is None
            or outcome.contract is None
            or outcome.receipt is None
        ):
            raise RuntimeOrchestrationError(
                "prepared governed dispatch is required"
            )

        intent = self._scheduling.get_revision(
            project_id,
            outcome.intent_id,
            outcome.intent_version,
        )
        if intent is None:
            raise RuntimeOrchestrationError(
                "durable dispatch scheduling revision is required"
            )
        if (
            intent.version != outcome.intent_version
            or intent.fingerprint != outcome.intent_fingerprint
            or intent.project_id != outcome.project_id
            or intent.workflow_id != outcome.workflow_id
            or intent.run_id != outcome.run_id
            or intent.intent_id != outcome.intent_id
            or intent.assignment_id != outcome.assignment_id
            or intent.plan_id != outcome.plan_id
            or intent.role_id != outcome.role_id
            or intent.agent_id != outcome.agent_id
            or intent.claim_id != outcome.claim_id
            or intent.claimed_by != outcome.claimed_by
        ):
            raise RuntimeOrchestrationError(
                "dispatch scheduling provenance mismatch"
            )

        existing = self._orchestrations.find_by_dispatch(
            project_id,
            outcome.dispatch_id,
        )
        if existing is not None:
            self._validate_plan(existing, plan)
            self._publish(existing)
            return existing

        record = RuntimeOrchestrationRecord(
            orchestration_id=self.orchestration_id_for(outcome),
            revision=1,
            state=RuntimeOrchestrationState.PREPARED,
            project_id=outcome.project_id,
            workflow_id=outcome.workflow_id,
            workflow_version=intent.workflow_version,
            run_id=outcome.run_id,
            intent_id=outcome.intent_id,
            dispatch_id=outcome.dispatch_id,
            assignment_id=outcome.assignment_id,
            plan_id=outcome.plan_id,
            role_id=outcome.role_id,
            agent_id=outcome.agent_id,
            actor_id=actor_id,
            correlation_id=outcome.run_id,
            causation_id=outcome.dispatch_id,
            created_at=timestamp,
            updated_at=timestamp,
            reason="prepared dispatch entered governed orchestration",
            evidence_refs=(outcome.dispatch_id,),
        )
        persisted = self._orchestrations.create(record)
        self._publish(persisted)
        return persisted

    def admit(
        self,
        *,
        project_id: str,
        orchestration_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        timestamp: int,
    ) -> RuntimeOrchestrationRecord:
        current = self._require_current(
            project_id,
            orchestration_id,
            RuntimeOrchestrationState.PREPARED,
            plan,
        )
        execution = self._runtime.admit(
            project_id=project_id,
            dispatch_id=current.dispatch_id,
            plan=plan,
            actor_id=actor_id,
            timestamp=timestamp,
        )
        return self._transition_from_execution(
            current,
            execution,
            RuntimeOrchestrationState.READY,
            timestamp,
            "runtime execution admitted",
        )

    def start(
        self,
        *,
        project_id: str,
        orchestration_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        timestamp: int,
    ) -> RuntimeOrchestrationRecord:
        current = self._require_current(
            project_id,
            orchestration_id,
            RuntimeOrchestrationState.READY,
            plan,
        )
        assert current.execution_id is not None
        execution = self._runtime.start(
            project_id=project_id,
            execution_id=current.execution_id,
            plan=plan,
            actor_id=actor_id,
            timestamp=timestamp,
        )
        return self._transition_from_execution(
            current,
            execution,
            RuntimeOrchestrationState.RUNNING,
            timestamp,
            "runtime execution explicitly started",
        )

    def complete(
        self,
        *,
        project_id: str,
        orchestration_id: str,
        plan: RoleExecutionPlan,
        actor_id: str,
        timestamp: int,
        outcome: ExecutionOutcome,
        summary: str,
        evidence: Tuple[ExecutionEvidence, ...],
        failure_category: FailureCategory = FailureCategory.NONE,
        blocking_reasons: Tuple[str, ...] = (),
        approvals: Tuple[str, ...] = (),
    ) -> RuntimeOrchestrationRecord:
        current = self._require_current(
            project_id,
            orchestration_id,
            RuntimeOrchestrationState.RUNNING,
            plan,
        )
        assert current.execution_id is not None
        execution = self._runtime.complete(
            project_id=project_id,
            execution_id=current.execution_id,
            plan=plan,
            actor_id=actor_id,
            timestamp=timestamp,
            outcome=outcome,
            summary=summary,
            evidence=evidence,
            failure_category=failure_category,
            blocking_reasons=blocking_reasons,
            approvals=approvals,
        )
        if (
            execution.state not in TERMINAL_RUNTIME_EXECUTION_STATES
            or execution.result is None
        ):
            raise RuntimeOrchestrationError(
                "terminal runtime execution result is required"
            )
        return self._transition_from_execution(
            current,
            execution,
            RuntimeOrchestrationState.TERMINAL,
            timestamp,
            "terminal runtime result recorded",
        )

    def finalize(
        self,
        *,
        project_id: str,
        orchestration_id: str,
        plan: RoleExecutionPlan,
        timestamp: int,
        next_role_id: Optional[str] = None,
    ) -> RuntimeOrchestrationRecord:
        current = self._require_current(
            project_id,
            orchestration_id,
            RuntimeOrchestrationState.TERMINAL,
            plan,
        )
        assert current.execution_id is not None
        workflow = self._results.record_result(
            project_id=project_id,
            execution_id=current.execution_id,
            plan=plan,
            timestamp=timestamp,
            next_role_id=next_role_id,
        )
        self._validate_finalized_workflow(current, workflow)
        record = RuntimeOrchestrationRecord.model_validate(
            {
                **current.model_dump(mode="python"),
                "revision": current.revision + 1,
                "state": RuntimeOrchestrationState.FINALIZED,
                "workflow_version": workflow.version,
                "actor_id": plan.agent_id,
                "causation_id": current.fingerprint,
                "updated_at": timestamp,
                "reason": "terminal result finalized into governed workflow",
                "evidence_refs": tuple(
                    dict.fromkeys(
                        current.evidence_refs
                        + (
                            current.result_id or "",
                            workflow.fingerprint,
                        )
                    )
                ),
            }
        )
        persisted = self._orchestrations.append(
            record,
            expected_revision=current.revision,
        )
        self._publish(persisted)
        return persisted

    def reconcile(
        self,
        project_id: str,
        orchestration_id: str,
    ) -> RuntimeOrchestrationRecord:
        record = self._orchestrations.get(project_id, orchestration_id)
        if record is None:
            raise RuntimeOrchestrationError(
                "runtime orchestration not found"
            )
        self._publish(record)
        return record

    def _require_current(
        self,
        project_id: str,
        orchestration_id: str,
        state: RuntimeOrchestrationState,
        plan: RoleExecutionPlan,
    ) -> RuntimeOrchestrationRecord:
        current = self._orchestrations.get(
            project_id,
            orchestration_id,
        )
        if current is None:
            raise RuntimeOrchestrationError(
                "runtime orchestration not found"
            )
        self._validate_plan(current, plan)
        if current.state != state:
            raise RuntimeOrchestrationError(
                f"runtime orchestration must be {state.value}"
            )
        return current

    @staticmethod
    def _validate_plan(
        record: RuntimeOrchestrationRecord,
        plan: RoleExecutionPlan,
    ) -> None:
        if (
            record.project_id != plan.project_id
            or record.assignment_id != plan.assignment_id
            or record.plan_id != plan.plan_id
            or record.role_id != plan.role_id
            or record.agent_id != plan.agent_id
        ):
            raise RuntimeOrchestrationError(
                "runtime orchestration plan association mismatch"
            )

    def _transition_from_execution(
        self,
        current: RuntimeOrchestrationRecord,
        execution: RuntimeExecutionRecord,
        state: RuntimeOrchestrationState,
        timestamp: int,
        reason: str,
    ) -> RuntimeOrchestrationRecord:
        if (
            execution.project_id != current.project_id
            or execution.workflow_id != current.workflow_id
            or execution.run_id != current.run_id
            or execution.dispatch_id != current.dispatch_id
            or execution.assignment_id != current.assignment_id
            or execution.plan_id != current.plan_id
            or execution.role_id != current.role_id
            or execution.agent_id != current.agent_id
        ):
            raise RuntimeOrchestrationError(
                "runtime execution association mismatch"
            )

        result_id = (
            execution.result.result_id
            if execution.result is not None
            else current.result_id
        )
        refs = current.evidence_refs + (
            execution.execution_id,
            execution.fingerprint,
        )
        if result_id is not None:
            refs += (result_id,)

        record = RuntimeOrchestrationRecord.model_validate(
            {
                **current.model_dump(mode="python"),
                "revision": current.revision + 1,
                "state": state,
                "execution_id": execution.execution_id,
                "result_id": result_id,
                "actor_id": execution.actor_id,
                "causation_id": current.fingerprint,
                "updated_at": timestamp,
                "reason": reason,
                "evidence_refs": tuple(dict.fromkeys(refs)),
            }
        )
        persisted = self._orchestrations.append(
            record,
            expected_revision=current.revision,
        )
        self._publish(persisted)
        return persisted

    @staticmethod
    def _validate_finalized_workflow(
        current: RuntimeOrchestrationRecord,
        workflow: GovernedWorkflow,
    ) -> None:
        stage = workflow.stages[workflow.current_stage]
        if (
            workflow.project_id != current.project_id
            or workflow.workflow_id != current.workflow_id
            or current.result_id is None
            or stage.result_id != current.result_id
        ):
            raise RuntimeOrchestrationError(
                "finalized workflow association mismatch"
            )

    def _publish(
        self,
        record: RuntimeOrchestrationRecord,
    ) -> None:
        if self._visibility is None:
            return
        try:
            self._visibility.publish(record)
        except Exception as exc:
            raise RuntimeOrchestrationPublicationError(record) from exc
