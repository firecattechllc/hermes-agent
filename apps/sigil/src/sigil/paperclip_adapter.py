"""Disabled-by-default governed Paperclip organizational adapter.

Stage 4 models Paperclip organization, project, employee, issue, heartbeat,
workspace, budget, evidence, and lifecycle concepts as immutable local
projections over the Stage 1 integration registry and Stage 2 worker contract.

This module performs no network requests, authentication, task mutation,
service activation, job dispatch, credential resolution, shell execution,
filesystem access, worktree creation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)
from sigil.worker_contract import (
    WORKER_CONTRACT_SCHEMA_VERSION,
    GovernedWorkerJob,
    JobState,
)

PAPERCLIP_ADAPTER_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REPOSITORY = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
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


class PaperclipValidationError(ValueError):
    """Paperclip adapter input failed closed."""


class PaperclipHeartbeatState(str, Enum):
    IDLE = "idle"
    READY = "ready"
    WORKING = "working"
    BLOCKED = "blocked"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class PaperclipIssueState(str, Enum):
    BACKLOG = "backlog"
    ASSIGNED = "assigned"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPLETION_UNKNOWN = "completion_unknown"


class PaperclipProjectionHealth(str, Enum):
    DISABLED = "disabled"
    READY = "ready"
    STALE = "stale"
    INCOMPATIBLE = "incompatible"
    INVALID = "invalid"


_JOB_STATE_PROJECTION: dict[JobState, PaperclipIssueState] = {
    JobState.PROPOSED: PaperclipIssueState.BACKLOG,
    JobState.ADMITTED: PaperclipIssueState.ASSIGNED,
    JobState.REJECTED: PaperclipIssueState.FAILED,
    JobState.QUEUED: PaperclipIssueState.QUEUED,
    JobState.RUNNING: PaperclipIssueState.IN_PROGRESS,
    JobState.CANCELLATION_REQUESTED: (
        PaperclipIssueState.CANCELLATION_REQUESTED
    ),
    JobState.CANCELLED: PaperclipIssueState.CANCELLED,
    JobState.SUCCEEDED: PaperclipIssueState.COMPLETED,
    JobState.FAILED: PaperclipIssueState.FAILED,
    JobState.COMPLETION_UNKNOWN: PaperclipIssueState.COMPLETION_UNKNOWN,
}


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise PaperclipValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise PaperclipValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise PaperclipValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise PaperclipValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise PaperclipValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise PaperclipValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise PaperclipValidationError(
            f"{label} must be a repository-relative reference"
        )


def _exact_decimal(value: str, label: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise PaperclipValidationError(
            f"{label} must be an exact decimal"
        ) from error

    if not parsed.is_finite():
        raise PaperclipValidationError(f"{label} must be finite")

    return parsed


@dataclass(frozen=True, slots=True)
class PaperclipAdapterConfig:
    integration_id: str = "paperclip"
    enabled: bool = False
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    schema_version: int = PAPERCLIP_ADAPTER_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != PAPERCLIP_ADAPTER_SCHEMA_VERSION:
            raise PaperclipValidationError(
                "unsupported Paperclip adapter schema version"
            )
        _require_identifier(self.integration_id, "Paperclip integration ID")
        if (
            self.expected_worker_contract_schema
            != WORKER_CONTRACT_SCHEMA_VERSION
        ):
            raise PaperclipValidationError(
                "incompatible worker contract schema"
            )
        self.authority.validate()
        _validate_sanitized(asdict(self), "Paperclip adapter configuration")

    @property
    def can_connect(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_mutate_remote_state(self) -> bool:
        return False

    @property
    def can_create_workspace(self) -> bool:
        return False

    @property
    def can_spend(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PaperclipOrganizationRef:
    organization_id: str
    project_id: str
    organization_name: str
    project_name: str

    def __post_init__(self) -> None:
        _require_identifier(self.organization_id, "organization ID")
        _require_identifier(self.project_id, "project ID")

        if not self.organization_name.strip():
            raise PaperclipValidationError("organization name is required")
        if not self.project_name.strip():
            raise PaperclipValidationError("project name is required")

        _validate_sanitized(asdict(self), "Paperclip organization reference")


@dataclass(frozen=True, slots=True)
class PaperclipAgentRef:
    agent_id: str
    employee_id: str
    display_name: str
    role_id: str
    worker_profile: str
    active: bool

    def __post_init__(self) -> None:
        for value, label in (
            (self.agent_id, "agent ID"),
            (self.employee_id, "employee ID"),
            (self.role_id, "role ID"),
            (self.worker_profile, "worker profile"),
        ):
            _require_identifier(value, label)

        if not self.display_name.strip():
            raise PaperclipValidationError("agent display name is required")

        _validate_sanitized(asdict(self), "Paperclip agent reference")


@dataclass(frozen=True, slots=True)
class PaperclipHeartbeat:
    agent_id: str
    observed_at: str
    sequence: int
    state: PaperclipHeartbeatState
    current_issue_id: str | None
    sanitized_summary: str

    def __post_init__(self) -> None:
        _require_identifier(self.agent_id, "heartbeat agent ID")
        _require_timestamp(self.observed_at, "heartbeat observation time")

        if self.sequence < 0:
            raise PaperclipValidationError(
                "heartbeat sequence cannot be negative"
            )
        if not isinstance(self.state, PaperclipHeartbeatState):
            raise PaperclipValidationError("unknown heartbeat state")
        if self.current_issue_id is not None:
            _require_identifier(self.current_issue_id, "heartbeat issue ID")
        if not self.sanitized_summary.strip():
            raise PaperclipValidationError(
                "heartbeat summary is required"
            )

        _validate_sanitized(asdict(self), "Paperclip heartbeat")


@dataclass(frozen=True, slots=True)
class PaperclipCommentRef:
    comment_id: str
    author_identity: str
    created_at: str
    transcript_reference: str | None
    content_digest: str

    def __post_init__(self) -> None:
        _require_identifier(self.comment_id, "comment ID")
        _require_timestamp(self.created_at, "comment creation time")
        _require_digest(self.content_digest, "comment content digest")

        if not self.author_identity.strip():
            raise PaperclipValidationError(
                "comment author identity is required"
            )

        _validate_sanitized(asdict(self), "Paperclip comment reference")

        if self.transcript_reference is not None:
            _require_relative_reference(
                self.transcript_reference,
                "transcript reference",
            )


@dataclass(frozen=True, slots=True)
class PaperclipWorkspaceRef:
    repository_identity: str
    revision: str
    workspace_reference: str
    worktree_reference: str | None
    read_only: bool = True

    def __post_init__(self) -> None:
        if _REPOSITORY.fullmatch(self.repository_identity) is None:
            raise PaperclipValidationError(
                "malformed workspace repository identity"
            )
        if _REVISION.fullmatch(self.revision) is None:
            raise PaperclipValidationError(
                "workspace revision must be an immutable commit"
            )

        _require_relative_reference(
            self.workspace_reference,
            "workspace reference",
        )

        if self.worktree_reference is not None:
            _require_relative_reference(
                self.worktree_reference,
                "worktree reference",
            )

        if not self.read_only:
            raise PaperclipValidationError(
                "Stage 4 Paperclip workspaces must remain read-only references"
            )

        _validate_sanitized(asdict(self), "Paperclip workspace reference")


@dataclass(frozen=True, slots=True)
class PaperclipCostAccounting:
    budget_limit_usd: str
    recorded_cost_usd: str
    runtime_seconds: int
    attempt_count: int

    def __post_init__(self) -> None:
        budget = _exact_decimal(self.budget_limit_usd, "budget limit")
        recorded = _exact_decimal(self.recorded_cost_usd, "recorded cost")

        if budget < Decimal("0") or budget > Decimal("1000000"):
            raise PaperclipValidationError(
                "Paperclip budget is outside policy bounds"
            )
        if recorded < Decimal("0"):
            raise PaperclipValidationError(
                "recorded cost cannot be negative"
            )
        if recorded > budget:
            raise PaperclipValidationError(
                "recorded cost exceeds governed budget"
            )
        if not 0 <= self.runtime_seconds <= 86400:
            raise PaperclipValidationError(
                "runtime accounting is outside bounds"
            )
        if not 0 <= self.attempt_count <= 10:
            raise PaperclipValidationError(
                "attempt accounting is outside bounds"
            )


@dataclass(frozen=True, slots=True)
class PaperclipEvidenceRef:
    evidence_id: str
    kind: str
    content_digest: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "evidence ID")
        _require_identifier(self.kind, "evidence kind")
        _require_digest(self.content_digest, "evidence content digest")
        _require_relative_reference(self.reference, "evidence reference")

        if not self.provenance.strip():
            raise PaperclipValidationError(
                "evidence provenance is required"
            )

        _validate_sanitized(asdict(self), "Paperclip evidence reference")


@dataclass(frozen=True, slots=True)
class PaperclipIssueProjection:
    organization: PaperclipOrganizationRef
    issue_id: str
    issue_title: str
    assigned_agent: PaperclipAgentRef | None
    state: PaperclipIssueState
    priority: int
    correlation_id: str
    idempotency_key: str
    worker_job_id: str
    worker_contract_digest: str
    worker_contract_schema: int
    created_at: str
    updated_at: str
    comments: tuple[PaperclipCommentRef, ...]
    workspaces: tuple[PaperclipWorkspaceRef, ...]
    accounting: PaperclipCostAccounting
    evidence: tuple[PaperclipEvidenceRef, ...]
    schema_version: int = PAPERCLIP_ADAPTER_SCHEMA_VERSION
    projection_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.projection_digest and self.projection_digest != expected:
            raise PaperclipValidationError(
                "Paperclip issue projection digest mismatch"
            )

        if not self.projection_digest:
            object.__setattr__(self, "projection_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload.pop("projection_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != PAPERCLIP_ADAPTER_SCHEMA_VERSION:
            raise PaperclipValidationError(
                "unsupported Paperclip projection schema"
            )

        for value, label in (
            (self.issue_id, "issue ID"),
            (self.correlation_id, "correlation ID"),
            (self.idempotency_key, "idempotency key"),
            (self.worker_job_id, "worker job ID"),
        ):
            _require_identifier(value, label)

        if not self.issue_title.strip():
            raise PaperclipValidationError("issue title is required")
        if not isinstance(self.state, PaperclipIssueState):
            raise PaperclipValidationError(
                "unknown Paperclip issue state"
            )
        if not 0 <= self.priority <= 100:
            raise PaperclipValidationError(
                "issue priority is outside bounds"
            )

        _require_digest(
            self.worker_contract_digest,
            "worker contract digest",
        )
        _require_timestamp(self.created_at, "issue creation time")
        _require_timestamp(self.updated_at, "issue update time")

        if self.worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise PaperclipValidationError(
                "Paperclip projection has incompatible worker schema"
            )

        if len({item.comment_id for item in self.comments}) != len(
            self.comments
        ):
            raise PaperclipValidationError("duplicate comment identity")
        if len(
            {item.workspace_reference for item in self.workspaces}
        ) != len(self.workspaces):
            raise PaperclipValidationError("duplicate workspace reference")
        if len({item.evidence_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise PaperclipValidationError("duplicate evidence identity")

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "Paperclip issue projection",
        )

    @property
    def can_execute(self) -> bool:
        return False

    @property
    def can_approve(self) -> bool:
        return False

    @property
    def can_mutate_portfolio(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PaperclipProjectionStatus:
    integration_id: str
    issue_id: str
    state: PaperclipProjectionHealth
    enabled: bool
    worker_contract_compatible: bool
    heartbeat_current: bool
    reason: str
    projection_digest: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.integration_id, "status integration ID")
        _require_identifier(self.issue_id, "status issue ID")
        _require_digest(self.projection_digest, "projection digest")

        if not isinstance(self.state, PaperclipProjectionHealth):
            raise PaperclipValidationError(
                "unknown Paperclip projection health"
            )
        if not self.reason.strip():
            raise PaperclipValidationError(
                "projection status reason is required"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "Paperclip projection status")


def validate_paperclip_registry_entry(
    config: PaperclipAdapterConfig,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate that a Stage 1 registry entry describes Paperclip safely."""

    if entry.integration_id != config.integration_id:
        raise PaperclipValidationError(
            "Paperclip registry identity mismatch"
        )
    if entry.category is not IntegrationCategory.ORGANIZATION:
        raise PaperclipValidationError(
            "Paperclip must be registered as an organization integration"
        )
    if entry.lifecycle_state in {
        LifecycleState.REJECTED,
        LifecycleState.DEPRECATED,
        LifecycleState.QUARANTINED,
    }:
        raise PaperclipValidationError(
            "Paperclip registry lifecycle is not eligible for projection"
        )

    entry.authority.validate()

    if entry.can_activate:
        raise PaperclipValidationError(
            "Paperclip registry entry unexpectedly permits activation"
        )


def project_worker_job(
    config: PaperclipAdapterConfig,
    job: GovernedWorkerJob,
    *,
    organization: PaperclipOrganizationRef,
    issue_id: str,
    issue_title: str,
    assigned_agent: PaperclipAgentRef | None,
    priority: int,
    updated_at: str,
    comments: tuple[PaperclipCommentRef, ...] = (),
    workspaces: tuple[PaperclipWorkspaceRef, ...] = (),
    evidence: tuple[PaperclipEvidenceRef, ...] = (),
    recorded_cost_usd: str = "0",
    runtime_seconds: int = 0,
    attempt_count: int = 0,
) -> PaperclipIssueProjection:
    """Create an immutable local Paperclip projection from one worker job."""

    if job.integration_id != config.integration_id:
        raise PaperclipValidationError(
            "worker job integration does not match Paperclip"
        )
    if job.schema_version != config.expected_worker_contract_schema:
        raise PaperclipValidationError(
            "worker job schema is incompatible with Paperclip"
        )

    job.authority.validate()

    return PaperclipIssueProjection(
        organization=organization,
        issue_id=issue_id,
        issue_title=issue_title,
        assigned_agent=assigned_agent,
        state=_JOB_STATE_PROJECTION[job.state],
        priority=priority,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        worker_job_id=job.job_id,
        worker_contract_digest=job.contract_digest,
        worker_contract_schema=job.schema_version,
        created_at=job.created_at,
        updated_at=updated_at,
        comments=comments,
        workspaces=workspaces,
        accounting=PaperclipCostAccounting(
            budget_limit_usd=job.budget.maximum_cost_usd,
            recorded_cost_usd=recorded_cost_usd,
            runtime_seconds=runtime_seconds,
            attempt_count=attempt_count,
        ),
        evidence=evidence,
    )


def evaluate_projection_status(
    config: PaperclipAdapterConfig,
    projection: PaperclipIssueProjection,
    *,
    heartbeat: PaperclipHeartbeat | None,
    heartbeat_age_seconds: int | None,
    stale_after_seconds: int = 120,
) -> PaperclipProjectionStatus:
    """Evaluate injected Paperclip evidence without contacting a service."""

    if not 1 <= stale_after_seconds <= 86400:
        raise PaperclipValidationError(
            "heartbeat staleness threshold is outside bounds"
        )

    if not config.enabled:
        return PaperclipProjectionStatus(
            integration_id=config.integration_id,
            issue_id=projection.issue_id,
            state=PaperclipProjectionHealth.DISABLED,
            enabled=False,
            worker_contract_compatible=True,
            heartbeat_current=False,
            reason="Paperclip adapter is disabled by policy.",
            projection_digest=projection.projection_digest,
        )

    compatible = (
        projection.worker_contract_schema
        == config.expected_worker_contract_schema
    )

    if not compatible:
        return PaperclipProjectionStatus(
            integration_id=config.integration_id,
            issue_id=projection.issue_id,
            state=PaperclipProjectionHealth.INCOMPATIBLE,
            enabled=True,
            worker_contract_compatible=False,
            heartbeat_current=False,
            reason="Paperclip worker contract schema is incompatible.",
            projection_digest=projection.projection_digest,
        )

    if projection.assigned_agent is None:
        return PaperclipProjectionStatus(
            integration_id=config.integration_id,
            issue_id=projection.issue_id,
            state=PaperclipProjectionHealth.READY,
            enabled=True,
            worker_contract_compatible=True,
            heartbeat_current=False,
            reason="Unassigned Paperclip issue projection is valid.",
            projection_digest=projection.projection_digest,
        )

    if heartbeat is None or heartbeat_age_seconds is None:
        return PaperclipProjectionStatus(
            integration_id=config.integration_id,
            issue_id=projection.issue_id,
            state=PaperclipProjectionHealth.STALE,
            enabled=True,
            worker_contract_compatible=True,
            heartbeat_current=False,
            reason="Assigned Paperclip issue has no current heartbeat evidence.",
            projection_digest=projection.projection_digest,
        )

    if heartbeat.agent_id != projection.assigned_agent.agent_id:
        raise PaperclipValidationError(
            "heartbeat agent does not match assigned Paperclip agent"
        )
    if heartbeat_age_seconds < 0:
        raise PaperclipValidationError(
            "heartbeat evidence cannot originate in the future"
        )
    if heartbeat.current_issue_id not in {
        None,
        projection.issue_id,
    }:
        raise PaperclipValidationError(
            "heartbeat issue does not match Paperclip projection"
        )

    if heartbeat_age_seconds > stale_after_seconds:
        return PaperclipProjectionStatus(
            integration_id=config.integration_id,
            issue_id=projection.issue_id,
            state=PaperclipProjectionHealth.STALE,
            enabled=True,
            worker_contract_compatible=True,
            heartbeat_current=False,
            reason=(
                "Paperclip heartbeat evidence exceeded the freshness window."
            ),
            projection_digest=projection.projection_digest,
        )

    return PaperclipProjectionStatus(
        integration_id=config.integration_id,
        issue_id=projection.issue_id,
        state=PaperclipProjectionHealth.READY,
        enabled=True,
        worker_contract_compatible=True,
        heartbeat_current=True,
        reason="Paperclip projection and heartbeat evidence are current.",
        projection_digest=projection.projection_digest,
    )


def lifecycle_projection() -> Mapping[JobState, PaperclipIssueState]:
    """Expose a copy of the deterministic worker-to-Paperclip state map."""

    return dict(_JOB_STATE_PROJECTION)
