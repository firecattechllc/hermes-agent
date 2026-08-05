"""Provider-neutral governed worker/job contract.

Stage 2 defines immutable job, admission, lifecycle, and result contracts only.
It does not dispatch work, activate integrations, access credentials, mutate
policy, or grant financial/execution authority.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    GovernedIntegrationRegistry,
    IntegrationRegistryEntry,
    LifecycleState,
)

WORKER_CONTRACT_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id)\s*[:=]|"
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|"
    r"[A-Za-z]:\\Users\\)"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)


class WorkerContractValidationError(ValueError):
    """A worker/job contract failed closed."""


class JobState(str, Enum):
    PROPOSED = "proposed"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPLETION_UNKNOWN = "completion_unknown"


class ResultState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    COMPLETION_UNKNOWN = "completion_unknown"


JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.PROPOSED: frozenset({JobState.ADMITTED, JobState.REJECTED}),
    JobState.ADMITTED: frozenset({JobState.QUEUED, JobState.CANCELLATION_REQUESTED}),
    JobState.REJECTED: frozenset(),
    JobState.QUEUED: frozenset(
        {JobState.RUNNING, JobState.CANCELLATION_REQUESTED, JobState.FAILED}
    ),
    JobState.RUNNING: frozenset(
        {
            JobState.SUCCEEDED,
            JobState.FAILED,
            JobState.CANCELLATION_REQUESTED,
            JobState.COMPLETION_UNKNOWN,
        }
    ),
    JobState.CANCELLATION_REQUESTED: frozenset(
        {JobState.CANCELLED, JobState.COMPLETION_UNKNOWN}
    ),
    JobState.CANCELLED: frozenset(),
    JobState.SUCCEEDED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.COMPLETION_UNKNOWN: frozenset(),
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)
    if _SECRET.search(serialized):
        raise WorkerContractValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise WorkerContractValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise WorkerContractValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise WorkerContractValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise WorkerContractValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise WorkerContractValidationError(f"{label} must be a SHA-256 identity")


def validate_job_transition(current: JobState, requested: JobState) -> None:
    if requested not in JOB_TRANSITIONS.get(current, frozenset()):
        raise WorkerContractValidationError(
            f"job transition {current.value} -> {requested.value} is denied"
        )


@dataclass(frozen=True, slots=True)
class JobBudget:
    maximum_cost_usd: str
    maximum_runtime_seconds: int
    maximum_attempts: int
    maximum_input_bytes: int
    maximum_output_bytes: int

    def __post_init__(self) -> None:
        try:
            cost = Decimal(self.maximum_cost_usd)
        except InvalidOperation as error:
            raise WorkerContractValidationError(
                "maximum cost must be an exact decimal"
            ) from error

        if cost < Decimal("0") or cost > Decimal("1000000"):
            raise WorkerContractValidationError("maximum cost is outside policy bounds")
        if not 1 <= self.maximum_runtime_seconds <= 86400:
            raise WorkerContractValidationError(
                "maximum runtime must be between 1 and 86400 seconds"
            )
        if not 1 <= self.maximum_attempts <= 10:
            raise WorkerContractValidationError(
                "maximum attempts must be between 1 and 10"
            )
        if not 1 <= self.maximum_input_bytes <= 100_000_000:
            raise WorkerContractValidationError("maximum input bytes are outside bounds")
        if not 1 <= self.maximum_output_bytes <= 100_000_000:
            raise WorkerContractValidationError("maximum output bytes are outside bounds")


@dataclass(frozen=True, slots=True)
class EvidenceRequirements:
    required: bool
    minimum_references: int
    required_kinds: tuple[str, ...]
    require_content_digests: bool
    require_provenance: bool

    def __post_init__(self) -> None:
        if self.minimum_references < 0 or self.minimum_references > 1000:
            raise WorkerContractValidationError(
                "minimum evidence references are outside bounds"
            )
        if self.required and self.minimum_references < 1:
            raise WorkerContractValidationError(
                "required evidence must require at least one reference"
            )
        for kind in self.required_kinds:
            _require_identifier(kind, "evidence kind")


@dataclass(frozen=True, slots=True)
class ApprovalRequirements:
    required: bool
    policy_revision: str
    approval_scope: tuple[str, ...]
    minimum_independent_approvers: int

    def __post_init__(self) -> None:
        if not self.policy_revision.strip():
            raise WorkerContractValidationError("approval policy revision is required")
        if not 0 <= self.minimum_independent_approvers <= 10:
            raise WorkerContractValidationError(
                "minimum independent approvers are outside bounds"
            )
        if self.required and self.minimum_independent_approvers < 1:
            raise WorkerContractValidationError(
                "required approval must require an independent approver"
            )
        for scope in self.approval_scope:
            _require_identifier(scope, "approval scope")


@dataclass(frozen=True, slots=True)
class GovernedWorkerJob:
    job_id: str
    correlation_id: str
    idempotency_key: str
    integration_id: str
    requested_capability: str
    requesting_actor_identity: str
    target_machine: str
    target_profile: str
    created_at: str
    deadline_at: str
    input_payload: Mapping[str, object]
    input_digest: str
    budget: JobBudget
    evidence_requirements: EvidenceRequirements
    approval_requirements: ApprovalRequirements
    state: JobState = JobState.PROPOSED
    schema_version: int = WORKER_CONTRACT_SCHEMA_VERSION
    contract_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()
        if self.contract_digest and self.contract_digest != expected:
            raise WorkerContractValidationError("worker job contract digest mismatch")
        if not self.contract_digest:
            object.__setattr__(self, "contract_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("contract_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != WORKER_CONTRACT_SCHEMA_VERSION:
            raise WorkerContractValidationError(
                "unsupported worker contract schema version"
            )
        for value, label in (
            (self.job_id, "job ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.integration_id, "integration ID"),
            (self.requested_capability, "requested capability"),
            (self.target_machine, "target machine"),
            (self.target_profile, "target profile"),
        ):
            _require_identifier(value, label)

        if not self.requesting_actor_identity.strip():
            raise WorkerContractValidationError(
                "requesting actor identity is required"
            )

        _require_timestamp(self.created_at, "created time")
        _require_timestamp(self.deadline_at, "deadline")
        _require_digest(self.input_digest, "input digest")

        expected_input_digest = f"sha256:{canonical_digest(dict(self.input_payload))}"
        if self.input_digest != expected_input_digest:
            raise WorkerContractValidationError("input payload digest mismatch")

        if not isinstance(self.state, JobState):
            raise WorkerContractValidationError("unknown job state")

        self.authority.validate()
        _validate_sanitized(self.digest_payload(), "worker job")


@dataclass(frozen=True, slots=True)
class JobAdmissionDecision:
    job_id: str
    contract_digest: str
    decided_at: str
    deciding_actor_identity: str
    admitted: bool
    rejection_code: str | None
    registry_revision: str
    integration_entry_digest: str | None
    approved_capability: str | None
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.job_id, "job ID")
        _require_digest(self.contract_digest, "contract digest")
        _require_timestamp(self.decided_at, "decision time")
        _require_digest(self.registry_revision, "registry revision")

        if not self.deciding_actor_identity.strip():
            raise WorkerContractValidationError(
                "deciding actor identity is required"
            )
        if self.admitted and self.rejection_code is not None:
            raise WorkerContractValidationError(
                "admitted decision cannot include a rejection code"
            )
        if not self.admitted and not self.rejection_code:
            raise WorkerContractValidationError(
                "rejected decision requires a rejection code"
            )
        if self.admitted:
            if self.integration_entry_digest is None:
                raise WorkerContractValidationError(
                    "admitted decision requires integration evidence"
                )
            _require_digest(
                self.integration_entry_digest,
                "integration entry digest",
            )
            if not self.approved_capability:
                raise WorkerContractValidationError(
                    "admitted decision requires an approved capability"
                )

        self.authority.validate()
        _validate_sanitized(asdict(self), "job admission decision")

    def validate_for(self, job: GovernedWorkerJob) -> None:
        if self.job_id != job.job_id or self.contract_digest != job.contract_digest:
            raise WorkerContractValidationError(
                "admission decision does not match worker job"
            )
        if self.deciding_actor_identity == job.requesting_actor_identity:
            raise WorkerContractValidationError(
                "worker job cannot self-admit"
            )


@dataclass(frozen=True, slots=True)
class WorkerUsage:
    attempt_count: int
    runtime_seconds: int
    input_bytes: int
    output_bytes: int
    cost_usd: str

    def __post_init__(self) -> None:
        try:
            cost = Decimal(self.cost_usd)
        except InvalidOperation as error:
            raise WorkerContractValidationError(
                "worker result cost must be an exact decimal"
            ) from error

        if min(
            self.attempt_count,
            self.runtime_seconds,
            self.input_bytes,
            self.output_bytes,
        ) < 0 or cost < Decimal("0"):
            raise WorkerContractValidationError(
                "worker usage values cannot be negative"
            )


@dataclass(frozen=True, slots=True)
class GovernedWorkerResult:
    job_id: str
    contract_digest: str
    result_state: ResultState
    completed_at: str
    worker_identity: str
    output_payload: Mapping[str, object]
    output_digest: str
    evidence_references: tuple[str, ...]
    audit_references: tuple[str, ...]
    usage: WorkerUsage
    error_code: str | None = None
    error_message: str | None = None
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.job_id, "job ID")
        _require_digest(self.contract_digest, "contract digest")
        _require_timestamp(self.completed_at, "completion time")

        if not self.worker_identity.strip():
            raise WorkerContractValidationError("worker identity is required")
        if not isinstance(self.result_state, ResultState):
            raise WorkerContractValidationError("unknown result state")

        expected_output_digest = (
            f"sha256:{canonical_digest(dict(self.output_payload))}"
        )
        if self.output_digest != expected_output_digest:
            raise WorkerContractValidationError("output payload digest mismatch")

        failed = self.result_state in {
            ResultState.FAILED,
            ResultState.COMPLETION_UNKNOWN,
        }
        if failed and not self.error_code:
            raise WorkerContractValidationError(
                "failed or unknown result requires an error code"
            )
        if not failed and self.error_code is not None:
            raise WorkerContractValidationError(
                "successful, partial, or cancelled result cannot include an error code"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "worker result")

    def validate_for(
        self,
        job: GovernedWorkerJob,
        admission: JobAdmissionDecision,
    ) -> None:
        admission.validate_for(job)
        if not admission.admitted:
            raise WorkerContractValidationError(
                "rejected jobs cannot produce worker results"
            )
        if self.job_id != job.job_id or self.contract_digest != job.contract_digest:
            raise WorkerContractValidationError(
                "worker result does not match job contract"
            )
        if self.usage.attempt_count > job.budget.maximum_attempts:
            raise WorkerContractValidationError("worker attempts exceeded job budget")
        if self.usage.runtime_seconds > job.budget.maximum_runtime_seconds:
            raise WorkerContractValidationError("worker runtime exceeded job budget")
        if self.usage.input_bytes > job.budget.maximum_input_bytes:
            raise WorkerContractValidationError("worker input exceeded job budget")
        if self.usage.output_bytes > job.budget.maximum_output_bytes:
            raise WorkerContractValidationError("worker output exceeded job budget")
        if Decimal(self.usage.cost_usd) > Decimal(job.budget.maximum_cost_usd):
            raise WorkerContractValidationError("worker cost exceeded job budget")
        if (
            job.evidence_requirements.required
            and len(self.evidence_references)
            < job.evidence_requirements.minimum_references
        ):
            raise WorkerContractValidationError(
                "worker result is missing required evidence"
            )


def _find_entry(
    registry: GovernedIntegrationRegistry,
    integration_id: str,
) -> IntegrationRegistryEntry | None:
    return next(
        (
            entry
            for entry in registry.entries
            if entry.integration_id == integration_id
        ),
        None,
    )


def evaluate_job_admission(
    job: GovernedWorkerJob,
    registry: GovernedIntegrationRegistry,
    *,
    deciding_actor_identity: str,
    decided_at: str,
) -> JobAdmissionDecision:
    """Evaluate one job without dispatching or activating an integration."""

    rejection_code: str | None = None
    entry = _find_entry(registry, job.integration_id)

    if entry is None:
        rejection_code = "unknown_integration"
    elif entry.lifecycle_state != LifecycleState.CERTIFIED:
        rejection_code = f"integration_{entry.lifecycle_state.value}"
    elif job.requested_capability not in entry.capabilities:
        rejection_code = "capability_not_declared"
    elif job.target_machine not in entry.approved_machines:
        rejection_code = "machine_not_approved"
    elif job.target_profile not in entry.approved_profiles:
        rejection_code = "profile_not_approved"

    admitted = rejection_code is None

    decision = JobAdmissionDecision(
        job_id=job.job_id,
        contract_digest=job.contract_digest,
        decided_at=decided_at,
        deciding_actor_identity=deciding_actor_identity,
        admitted=admitted,
        rejection_code=rejection_code,
        registry_revision=registry.revision,
        integration_entry_digest=entry.content_digest if admitted and entry else None,
        approved_capability=job.requested_capability if admitted else None,
    )
    decision.validate_for(job)
    return decision



@dataclass(frozen=True, slots=True)
class JobStateTransition:
    job_id: str
    contract_digest: str
    previous_state: JobState
    requested_state: JobState
    actor_identity: str
    occurred_at: str
    reason: str
    evidence_references: tuple[str, ...]
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.job_id, "job ID")
        _require_digest(self.contract_digest, "contract digest")
        _require_timestamp(self.occurred_at, "transition time")

        if not self.actor_identity.strip() or not self.reason.strip():
            raise WorkerContractValidationError(
                "job transition actor and reason are required"
            )

        validate_job_transition(self.previous_state, self.requested_state)
        self.authority.validate()
        _validate_sanitized(asdict(self), "job state transition")


class WorkerContractStorageError(RuntimeError):
    """Durable worker contract state failed closed."""


class DurableWorkerContractStore:
    """Atomic job snapshots and append-only hash-linked lifecycle evidence."""

    def __init__(self, state_root: Path) -> None:
        if not state_root.is_absolute() or state_root.is_symlink():
            raise WorkerContractStorageError(
                "worker state root must be an absolute non-symlink Path"
            )

        self.directory = state_root / "governed-worker-contract-v1"
        self.jobs_directory = self.directory / "jobs"
        self.evidence_path = self.directory / "job-lifecycle-evidence.jsonl"
        self.lock_path = self.directory / "worker-contract.lock"

    @contextmanager
    def _lock(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

        if self.directory.is_symlink():
            raise WorkerContractStorageError(
                "worker contract directory cannot be a symlink"
            )
        if self.lock_path.is_symlink():
            raise WorkerContractStorageError(
                "worker contract lock cannot be a symlink"
            )

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def save_job(self, job: GovernedWorkerJob) -> str:
        encoded = (
            json.dumps(
                job.digest_payload() | {"contract_digest": job.contract_digest},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            + "\n"
        )

        with self._lock():
            self.jobs_directory.mkdir(mode=0o700, parents=True, exist_ok=True)

            if self.jobs_directory.is_symlink():
                raise WorkerContractStorageError(
                    "worker jobs directory cannot be a symlink"
                )

            path = self.jobs_directory / f"{job.job_id}.json"

            if path.is_symlink():
                raise WorkerContractStorageError(
                    "worker job snapshot cannot be a symlink"
                )

            temporary = self.jobs_directory / f".{job.job_id}.{os.getpid()}.tmp"

            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())

                os.replace(temporary, path)

                descriptor = os.open(self.jobs_directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if temporary.exists():
                    temporary.unlink()

        return job.contract_digest

    def load_job(self, job_id: str) -> dict[str, object]:
        _require_identifier(job_id, "job ID")

        path = self.jobs_directory / f"{job_id}.json"

        if not path.exists():
            raise WorkerContractStorageError("worker job snapshot does not exist")
        if path.is_symlink():
            raise WorkerContractStorageError(
                "worker job snapshot cannot be a symlink"
            )
        if not self.lock_path.exists() or self.lock_path.is_symlink():
            raise WorkerContractStorageError(
                "worker contract read lock is unavailable"
            )

        with self.lock_path.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)

            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise WorkerContractStorageError(
                    "worker job snapshot is invalid"
                ) from error

        if not isinstance(payload, dict):
            raise WorkerContractStorageError(
                "worker job snapshot shape is invalid"
            )

        digest = payload.get("contract_digest")

        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise WorkerContractStorageError(
                "worker job snapshot digest is invalid"
            )

        body = dict(payload)
        body.pop("contract_digest", None)

        expected = f"sha256:{canonical_digest(body)}"

        if digest != expected:
            raise WorkerContractStorageError(
                "worker job snapshot integrity is invalid"
            )

        _validate_sanitized(payload, "stored worker job")
        return payload

    def append_transition(
        self,
        transition: JobStateTransition,
    ) -> dict[str, object]:
        with self._lock():
            if self.evidence_path.is_symlink():
                raise WorkerContractStorageError(
                    "worker lifecycle evidence path cannot be a symlink"
                )

            records = self._read_evidence_unlocked()

            base: dict[str, object] = {
                "sequence": len(records) + 1,
                "previous_record_hash": (
                    records[-1]["entry_hash"] if records else "0" * 64
                ),
                "transition": {
                    "job_id": transition.job_id,
                    "contract_digest": transition.contract_digest,
                    "previous_state": transition.previous_state.value,
                    "requested_state": transition.requested_state.value,
                    "actor_identity": transition.actor_identity,
                    "occurred_at": transition.occurred_at,
                    "reason": transition.reason,
                    "evidence_references": list(
                        transition.evidence_references
                    ),
                    "authority": asdict(transition.authority),
                },
                **asdict(AuthorityDenials()),
            }

            base["entry_hash"] = canonical_digest(base)

            with self.evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        base,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())

            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

            return base

    def read_evidence(self) -> tuple[dict[str, object], ...]:
        if not self.directory.exists() or not self.evidence_path.exists():
            return ()

        if not self.lock_path.exists() or self.lock_path.is_symlink():
            raise WorkerContractStorageError(
                "worker contract read lock is unavailable"
            )

        with self.lock_path.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_evidence_unlocked()

    def _read_evidence_unlocked(self) -> tuple[dict[str, object], ...]:
        if not self.evidence_path.exists():
            return ()

        try:
            records = tuple(
                json.loads(line)
                for line in self.evidence_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            )
        except (OSError, json.JSONDecodeError) as error:
            raise WorkerContractStorageError(
                "worker lifecycle evidence is invalid"
            ) from error

        previous = "0" * 64

        for sequence, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise WorkerContractStorageError(
                    "worker lifecycle evidence record shape is invalid"
                )

            expected = canonical_digest(
                {
                    key: value
                    for key, value in record.items()
                    if key != "entry_hash"
                }
            )

            if (
                record.get("sequence") != sequence
                or record.get("previous_record_hash") != previous
                or record.get("entry_hash") != expected
            ):
                raise WorkerContractStorageError(
                    "worker lifecycle evidence integrity is invalid"
                )

            previous = expected

        return records
