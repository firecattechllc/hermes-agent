"""Mission Control visibility for governed launch contracts.

This module publishes immutable launch-contract and validation visibility
records into the existing Mission Control append-only telemetry journal.

It does not execute contracts, mutate assignments, select providers, create
workspaces, or alter validation results.
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from hermes_cli.agent_roles.launch import LaunchContract
from hermes_cli.agent_roles.launch_validation import (
    LaunchValidationIssue,
    LaunchValidationResult,
)
from hermes_cli.mission_control import models as mission_models
from hermes_cli.mission_control.service import MissionControlService


CONTRACT_EVENT_TYPE = "launch_contract_published"
VALIDATION_EVENT_TYPE = "launch_validation_recorded"


def _required_text(value: str, field_name: str) -> str:
    normalised = value.strip()

    if not normalised:
        raise ValueError(f"{field_name} must not be blank")

    return normalised


class LaunchVisibilityRecord(BaseModel):
    """Immutable Mission Control projection for one launch contract."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    project_id: str = Field(..., min_length=1, max_length=128)
    contract_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    backlog_item_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    contract_status: str = Field(..., min_length=1, max_length=32)
    validation_valid: bool
    runtime: str = Field(..., min_length=1, max_length=128)
    engine: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    provider: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    model: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    modifies_repository: bool
    workspace_mode: str = Field(..., min_length=1, max_length=64)
    blocked_reasons: Tuple[str, ...] = Field(default_factory=tuple)
    validation_issues: Tuple[LaunchValidationIssue, ...] = Field(
        default_factory=tuple
    )
    published_at: int = Field(..., ge=0)
    contract_event_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    validation_event_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )

    @field_validator(
        "project_id",
        "contract_id",
        "assignment_id",
        "role_id",
        "agent_id",
        "contract_status",
        "runtime",
        "workspace_mode",
        "contract_event_id",
        "validation_event_id",
    )
    @classmethod
    def _normalise_required(
        cls,
        value: str,
        info,
    ) -> str:
        return _required_text(value, info.field_name)


class LaunchVisibilityAdapter:
    """Translate launch artifacts to and from Mission Control telemetry."""

    def to_events(
        self,
        contract: LaunchContract,
        validation: LaunchValidationResult,
        *,
        timestamp: Optional[int] = None,
    ) -> Tuple[
        mission_models.TelemetryEvent,
        mission_models.TelemetryEvent,
    ]:
        """Create deterministic-order telemetry events without storing them."""
        if validation.contract_id != contract.contract_id:
            raise ValueError(
                "validation contract_id does not match launch contract"
            )

        published_at = (
            contract.created_at
            if timestamp is None
            else timestamp
        )

        contract_event = mission_models.TelemetryEvent(
            event_id=mission_models.new_telemetry_event_id(),
            event_type=CONTRACT_EVENT_TYPE,
            project_id=contract.project_id,
            launch_id=contract.contract_id,
            task_id=contract.assignment_id,
            backlog_id=contract.backlog_item_id,
            agent_id=contract.agent_id,
            timestamp=published_at,
            sequence=0,
            severity=(
                "warning"
                if contract.blocked_reasons
                else "info"
            ),
            correlation_id=contract.correlation_id,
            causation_id=contract.causation_id,
            payload={
                "contract": contract.model_dump(mode="json"),
                "source": "agent_roles",
            },
        )

        validation_event = mission_models.TelemetryEvent(
            event_id=mission_models.new_telemetry_event_id(),
            event_type=VALIDATION_EVENT_TYPE,
            project_id=contract.project_id,
            launch_id=contract.contract_id,
            task_id=contract.assignment_id,
            backlog_id=contract.backlog_item_id,
            agent_id=contract.agent_id,
            timestamp=published_at,
            sequence=0,
            severity=(
                "info"
                if validation.valid
                else "warning"
            ),
            correlation_id=contract.correlation_id,
            causation_id=contract_event.event_id,
            payload={
                "validation": validation.model_dump(mode="json"),
                "source": "agent_roles",
            },
        )

        return contract_event, validation_event

    def from_events(
        self,
        events: Iterable[mission_models.TelemetryEvent],
    ) -> Tuple[LaunchVisibilityRecord, ...]:
        """Project visibility records from Mission Control events."""
        contracts = {}
        validations = {}

        for event in events:
            if event.event_type == CONTRACT_EVENT_TYPE:
                raw_contract = event.payload.get("contract")

                if not isinstance(raw_contract, dict):
                    raise ValueError(
                        "launch contract telemetry payload is malformed"
                    )

                contract = LaunchContract.model_validate(raw_contract)
                contracts[contract.contract_id] = (
                    event,
                    contract,
                )

            elif event.event_type == VALIDATION_EVENT_TYPE:
                raw_validation = event.payload.get("validation")

                if not isinstance(raw_validation, dict):
                    raise ValueError(
                        "launch validation telemetry payload is malformed"
                    )

                validation = LaunchValidationResult.model_validate(
                    raw_validation
                )
                validations[validation.contract_id] = (
                    event,
                    validation,
                )

        records = []

        for contract_id, (
            contract_event,
            contract,
        ) in contracts.items():
            validation_entry = validations.get(contract_id)

            if validation_entry is None:
                continue

            validation_event, validation = validation_entry

            if contract.project_id != contract_event.project_id:
                raise ValueError(
                    "launch contract telemetry project mismatch"
                )

            if validation_event.project_id != contract.project_id:
                raise ValueError(
                    "launch validation telemetry project mismatch"
                )

            records.append(
                LaunchVisibilityRecord(
                    project_id=contract.project_id,
                    contract_id=contract.contract_id,
                    assignment_id=contract.assignment_id,
                    role_id=contract.role_id,
                    agent_id=contract.agent_id,
                    backlog_item_id=contract.backlog_item_id,
                    contract_status=contract.status.value,
                    validation_valid=validation.valid,
                    runtime=contract.environment.runtime,
                    engine=contract.environment.engine,
                    provider=contract.environment.provider,
                    model=contract.environment.model,
                    modifies_repository=(
                        contract.policy.modifies_repository
                    ),
                    workspace_mode=contract.workspace.mode.value,
                    blocked_reasons=contract.blocked_reasons,
                    validation_issues=validation.issues,
                    published_at=contract_event.timestamp,
                    contract_event_id=contract_event.event_id,
                    validation_event_id=validation_event.event_id,
                )
            )

        return tuple(
            sorted(
                records,
                key=lambda record: (
                    record.published_at,
                    record.contract_id,
                ),
            )
        )


class LaunchVisibilityService:
    """Publish and query launch visibility through Mission Control."""

    def __init__(
        self,
        mission_control: MissionControlService,
        adapter: Optional[LaunchVisibilityAdapter] = None,
    ) -> None:
        self._mission_control = mission_control
        self._adapter = adapter or LaunchVisibilityAdapter()

    def publish(
        self,
        contract: LaunchContract,
        validation: LaunchValidationResult,
        *,
        timestamp: Optional[int] = None,
    ) -> LaunchVisibilityRecord:
        """Append one contract and validation pair to Mission Control."""
        events = self._adapter.to_events(
            contract,
            validation,
            timestamp=timestamp,
        )
        stored = self._mission_control.append_events(list(events))
        records = self._adapter.from_events(stored)

        if len(records) != 1:
            raise ValueError(
                "published launch visibility pair did not project "
                "to exactly one record"
            )

        return records[0]

    def list_records(
        self,
        project_id: str,
        *,
        contract_id: Optional[str] = None,
    ) -> Tuple[LaunchVisibilityRecord, ...]:
        """Read launch visibility records from project telemetry."""
        project_id = _required_text(project_id, "project_id")
        events = self._mission_control.get_events(project_id)
        records = self._adapter.from_events(events)

        if contract_id is None:
            return records

        contract_id = _required_text(
            contract_id,
            "contract_id",
        )

        return tuple(
            record
            for record in records
            if record.contract_id == contract_id
        )
