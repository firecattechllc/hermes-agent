"""Immutable governed runtime-session lifecycle.

A runtime session represents an accepted runtime handoff before any worker
execution begins.

This module does not create directories, start subprocesses, resolve
providers, contact remote services, modify repositories, or launch agents.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from hermes_cli.agent_roles.launch import LaunchContract
from hermes_cli.agent_roles.runtime_handoff import (
    RuntimeHandoffMode,
    RuntimeHandoffReceipt,
    RuntimeHandoffStatus,
)


RUNTIME_SESSION_SCHEMA_VERSION = 1


def _required_text(value: str, field_name: str) -> str:
    normalised = value.strip()

    if not normalised:
        raise ValueError(f"{field_name} must not be blank")

    return normalised


def _optional_text(
    value: Optional[str],
    field_name: str,
) -> Optional[str]:
    if value is None:
        return None

    return _required_text(value, field_name)


class RuntimeSessionState(str, Enum):
    """Pre-execution runtime-session states."""

    CREATED = "created"
    READY = "ready"


class RuntimeSessionTransition(str, Enum):
    """Stable transition identifiers for session evidence."""

    CREATED = "created"
    MARKED_READY = "marked_ready"


class RuntimeSessionEvent(BaseModel):
    """Immutable lifecycle event embedded in a session."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    transition: RuntimeSessionTransition
    state: RuntimeSessionState
    timestamp: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=512)

    @field_validator("reason")
    @classmethod
    def _normalise_reason(cls, value: str) -> str:
        return _required_text(value, "reason")


class RuntimeSession(BaseModel):
    """Immutable governed session created from an accepted handoff."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: int = Field(
        default=RUNTIME_SESSION_SCHEMA_VERSION,
        ge=1,
    )
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    project_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    contract_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    assignment_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    role_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    agent_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
    )
    receipt_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    request_fingerprint: str = Field(
        ...,
        min_length=64,
        max_length=64,
    )
    adapter_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    adapter_version: str = Field(
        ...,
        min_length=1,
        max_length=64,
    )
    runtime: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    workspace_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    state: RuntimeSessionState
    created_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    execution_started: bool = False
    process_id: Optional[int] = Field(default=None, ge=1)
    events: Tuple[RuntimeSessionEvent, ...] = Field(
        min_length=1,
    )

    @field_validator(
        "session_id",
        "project_id",
        "contract_id",
        "assignment_id",
        "role_id",
        "agent_id",
        "receipt_id",
        "request_fingerprint",
        "adapter_name",
        "adapter_version",
        "runtime",
    )
    @classmethod
    def _normalise_required(
        cls,
        value: str,
        info,
    ) -> str:
        return _required_text(value, info.field_name)

    @field_validator("workspace_id")
    @classmethod
    def _normalise_workspace_id(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        return _optional_text(value, "workspace_id")

    @model_validator(mode="after")
    def _validate_session(self) -> "RuntimeSession":
        if (
            self.schema_version
            != RUNTIME_SESSION_SCHEMA_VERSION
        ):
            raise ValueError(
                "unsupported runtime session schema version"
            )

        if self.updated_at < self.created_at:
            raise ValueError(
                "updated_at cannot precede created_at"
            )

        if self.execution_started:
            raise ValueError(
                "pre-execution runtime session cannot report "
                "execution"
            )

        if self.process_id is not None:
            raise ValueError(
                "pre-execution runtime session cannot contain "
                "process_id"
            )

        if self.events[0].transition != (
            RuntimeSessionTransition.CREATED
        ):
            raise ValueError(
                "runtime session must begin with created event"
            )

        if self.events[0].state != RuntimeSessionState.CREATED:
            raise ValueError(
                "created event must record created state"
            )

        previous_timestamp = self.created_at

        for event in self.events:
            if event.timestamp < previous_timestamp:
                raise ValueError(
                    "runtime session events must be "
                    "chronological"
                )

            previous_timestamp = event.timestamp

        if self.events[-1].state != self.state:
            raise ValueError(
                "latest runtime session event must match "
                "session state"
            )

        if self.events[-1].timestamp != self.updated_at:
            raise ValueError(
                "latest runtime session event timestamp must "
                "match updated_at"
            )

        if self.state == RuntimeSessionState.CREATED:
            if len(self.events) != 1:
                raise ValueError(
                    "created runtime session must contain only "
                    "its creation event"
                )

        if self.state == RuntimeSessionState.READY:
            if len(self.events) != 2:
                raise ValueError(
                    "ready runtime session requires exactly "
                    "one ready transition"
                )

            if self.events[-1].transition != (
                RuntimeSessionTransition.MARKED_READY
            ):
                raise ValueError(
                    "ready runtime session requires "
                    "marked_ready transition"
                )

        return self


class RuntimeSessionFactory:
    """Create deterministic pre-execution runtime sessions."""

    def create(
        self,
        contract: LaunchContract,
        receipt: RuntimeHandoffReceipt,
        *,
        created_at: int,
    ) -> RuntimeSession:
        """Create a session from one accepted dry-run receipt."""
        self._validate_inputs(contract, receipt)

        session_seed = "|".join(
            (
                contract.project_id,
                contract.contract_id,
                receipt.receipt_id,
                receipt.request_fingerprint,
                receipt.adapter_name,
                receipt.adapter_version,
            )
        )
        session_digest = hashlib.sha256(
            session_seed.encode("utf-8")
        ).hexdigest()[:24]

        return RuntimeSession(
            session_id=f"session_{session_digest}",
            project_id=contract.project_id,
            contract_id=contract.contract_id,
            assignment_id=contract.assignment_id,
            role_id=contract.role_id,
            agent_id=contract.agent_id,
            receipt_id=receipt.receipt_id,
            request_fingerprint=receipt.request_fingerprint,
            adapter_name=receipt.adapter_name,
            adapter_version=receipt.adapter_version,
            runtime=receipt.runtime,
            workspace_id=contract.workspace.workspace_id,
            state=RuntimeSessionState.CREATED,
            created_at=created_at,
            updated_at=created_at,
            execution_started=False,
            process_id=None,
            events=(
                RuntimeSessionEvent(
                    transition=RuntimeSessionTransition.CREATED,
                    state=RuntimeSessionState.CREATED,
                    timestamp=created_at,
                    reason=(
                        "runtime session created from accepted "
                        "dry-run handoff"
                    ),
                ),
            ),
        )

    @staticmethod
    def _validate_inputs(
        contract: LaunchContract,
        receipt: RuntimeHandoffReceipt,
    ) -> None:
        if receipt.contract_id != contract.contract_id:
            raise ValueError(
                "handoff receipt contract_id does not match "
                "launch contract"
            )

        if receipt.runtime != contract.environment.runtime:
            raise ValueError(
                "handoff receipt runtime does not match "
                "launch contract"
            )

        if receipt.mode != RuntimeHandoffMode.DRY_RUN:
            raise ValueError(
                "runtime session requires dry-run handoff receipt"
            )

        if receipt.status != RuntimeHandoffStatus.ACCEPTED:
            raise ValueError(
                "runtime session requires accepted handoff receipt"
            )

        if not receipt.accepted:
            raise ValueError(
                "runtime session requires accepted=true receipt"
            )

        if receipt.reasons:
            raise ValueError(
                "accepted runtime handoff receipt cannot contain "
                "rejection reasons"
            )

        if receipt.execution_started:
            raise ValueError(
                "runtime session cannot be created after "
                "execution starts"
            )

        if receipt.process_id is not None:
            raise ValueError(
                "runtime session cannot be created from receipt "
                "with process_id"
            )


class RuntimeSessionService:
    """Apply pure, fail-closed runtime-session transitions."""

    def __init__(
        self,
        factory: Optional[RuntimeSessionFactory] = None,
    ) -> None:
        self._factory = factory or RuntimeSessionFactory()

    def create(
        self,
        contract: LaunchContract,
        receipt: RuntimeHandoffReceipt,
        *,
        created_at: int,
    ) -> RuntimeSession:
        """Create an immutable session in CREATED state."""
        return self._factory.create(
            contract,
            receipt,
            created_at=created_at,
        )

    def mark_ready(
        self,
        session: RuntimeSession,
        *,
        ready_at: int,
        reason: str = "runtime session passed readiness checks",
    ) -> RuntimeSession:
        """Return a new immutable session in READY state."""
        if session.state != RuntimeSessionState.CREATED:
            raise ValueError(
                "only created runtime sessions can be "
                "marked ready"
            )

        if ready_at < session.updated_at:
            raise ValueError(
                "ready_at cannot precede current session state"
            )

        reason = _required_text(reason, "reason")

        return session.model_copy(
            update={
                "state": RuntimeSessionState.READY,
                "updated_at": ready_at,
                "events": session.events
                + (
                    RuntimeSessionEvent(
                        transition=(
                            RuntimeSessionTransition.MARKED_READY
                        ),
                        state=RuntimeSessionState.READY,
                        timestamp=ready_at,
                        reason=reason,
                    ),
                ),
            }
        )
