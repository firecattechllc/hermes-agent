"""Immutable governed launch contracts for Agent Roles.

A launch contract describes work that is ready to cross from assignment
governance into a future runtime adapter. It does not execute commands,
create workspaces, start workers, resolve providers, or mutate assignment
state.

All models are immutable and fail closed on unknown fields.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


CURRENT_LAUNCH_SCHEMA_VERSION = 1
SUPPORTED_LAUNCH_SCHEMA_VERSIONS = frozenset({1})


def _strip_required(value: str, field_name: str) -> str:
    normalised = value.strip()

    if not normalised:
        raise ValueError(f"{field_name} must not be blank")

    return normalised


def _strip_optional(
    value: Optional[str],
    field_name: str,
) -> Optional[str]:
    if value is None:
        return None

    return _strip_required(value, field_name)


def _normalise_text_tuple(
    values: Tuple[str, ...],
    field_name: str,
) -> Tuple[str, ...]:
    normalised = []
    seen = set()

    for value in values:
        item = _strip_required(value, field_name)

        if item in seen:
            continue

        seen.add(item)
        normalised.append(item)

    return tuple(normalised)


class LaunchContractStatus(str, Enum):
    """Readiness state of an immutable launch contract."""

    READY = "ready"
    BLOCKED = "blocked"


class LaunchWorkspaceMode(str, Enum):
    """Isolation mode requested from a future runtime adapter."""

    READ_ONLY = "read_only"
    ISOLATED_WRITE = "isolated_write"


class LaunchWorkspace(BaseModel):
    """Requested workspace boundary for one governed launch."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    mode: LaunchWorkspaceMode
    repository_root: str = Field(..., min_length=1, max_length=4096)
    workspace_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    base_ref: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )

    @field_validator(
        "repository_root",
        "workspace_id",
        "base_ref",
    )
    @classmethod
    def _normalise_text(
        cls,
        value: Optional[str],
        info,
    ) -> Optional[str]:
        if info.field_name == "repository_root":
            assert value is not None
            return _strip_required(value, info.field_name)

        return _strip_optional(value, info.field_name)

    @model_validator(mode="after")
    def _validate_write_workspace(self) -> "LaunchWorkspace":
        if (
            self.mode == LaunchWorkspaceMode.ISOLATED_WRITE
            and self.workspace_id is None
        ):
            raise ValueError(
                "isolated_write workspace requires workspace_id"
            )

        return self


class LaunchPolicy(BaseModel):
    """Governance restrictions propagated into the runtime boundary."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    risk_level: str = Field(..., min_length=1, max_length=32)
    modifies_repository: bool = False
    human_approved: bool = False
    allowed_paths: Tuple[str, ...] = Field(default_factory=tuple)
    denied_paths: Tuple[str, ...] = Field(default_factory=tuple)
    required_capabilities: Tuple[str, ...] = Field(
        default_factory=tuple
    )

    @field_validator("risk_level")
    @classmethod
    def _normalise_risk_level(cls, value: str) -> str:
        return _strip_required(value, "risk_level").lower()

    @field_validator(
        "allowed_paths",
        "denied_paths",
        "required_capabilities",
    )
    @classmethod
    def _normalise_sequences(
        cls,
        values: Tuple[str, ...],
        info,
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(values, info.field_name)

    @model_validator(mode="after")
    def _validate_policy(self) -> "LaunchPolicy":
        overlap = set(self.allowed_paths).intersection(
            self.denied_paths
        )

        if overlap:
            paths = ", ".join(sorted(overlap))
            raise ValueError(
                "launch paths may not be both allowed and denied: "
                f"{paths}"
            )

        if self.modifies_repository and not self.allowed_paths:
            raise ValueError(
                "repository modification requires allowed_paths"
            )

        return self


class LaunchEnvironment(BaseModel):
    """Non-secret runtime-selection hints for a future adapter."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

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
    environment: Tuple[Tuple[str, str], ...] = Field(
        default_factory=tuple
    )

    @field_validator(
        "runtime",
        "engine",
        "provider",
        "model",
    )
    @classmethod
    def _normalise_runtime_text(
        cls,
        value: Optional[str],
        info,
    ) -> Optional[str]:
        if info.field_name == "runtime":
            assert value is not None
            return _strip_required(value, info.field_name)

        return _strip_optional(value, info.field_name)

    @field_validator("environment")
    @classmethod
    def _normalise_environment(
        cls,
        values: Tuple[Tuple[str, str], ...],
    ) -> Tuple[Tuple[str, str], ...]:
        normalised = []
        seen = set()

        for raw_key, raw_value in values:
            key = _strip_required(raw_key, "environment key")
            value = raw_value.strip()

            if key in seen:
                raise ValueError(
                    f"duplicate environment key: {key}"
                )

            seen.add(key)
            normalised.append((key, value))

        return tuple(normalised)


class LaunchContract(BaseModel):
    """Immutable handoff from governed assignment to future runtime."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: int = CURRENT_LAUNCH_SCHEMA_VERSION
    contract_id: str = Field(..., min_length=1, max_length=128)
    project_id: str = Field(..., min_length=1, max_length=128)
    assignment_id: str = Field(..., min_length=1, max_length=128)
    role_id: str = Field(..., min_length=1, max_length=128)
    agent_id: str = Field(..., min_length=1, max_length=256)
    backlog_item_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    status: LaunchContractStatus = LaunchContractStatus.READY
    instructions: str = Field(..., min_length=1)
    created_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    causation_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    blocked_reasons: Tuple[str, ...] = Field(default_factory=tuple)
    workspace: LaunchWorkspace
    policy: LaunchPolicy
    environment: LaunchEnvironment

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value not in SUPPORTED_LAUNCH_SCHEMA_VERSIONS:
            raise ValueError(
                f"launch schema version {value} not supported"
            )

        return value

    @field_validator(
        "contract_id",
        "project_id",
        "assignment_id",
        "role_id",
        "agent_id",
        "backlog_item_id",
        "correlation_id",
        "causation_id",
    )
    @classmethod
    def _normalise_identifiers(
        cls,
        value: Optional[str],
        info,
    ) -> Optional[str]:
        optional_fields = {
            "backlog_item_id",
            "correlation_id",
            "causation_id",
        }

        if info.field_name in optional_fields:
            return _strip_optional(value, info.field_name)

        assert value is not None
        return _strip_required(value, info.field_name)

    @field_validator("instructions")
    @classmethod
    def _normalise_instructions(cls, value: str) -> str:
        return _strip_required(value, "instructions")

    @field_validator("blocked_reasons")
    @classmethod
    def _normalise_blocked_reasons(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return _normalise_text_tuple(
            values,
            "blocked_reasons",
        )

    @model_validator(mode="after")
    def _validate_readiness(self) -> "LaunchContract":
        if (
            self.status == LaunchContractStatus.READY
            and self.blocked_reasons
        ):
            raise ValueError(
                "ready launch contract may not have blocked_reasons"
            )

        if (
            self.status == LaunchContractStatus.BLOCKED
            and not self.blocked_reasons
        ):
            raise ValueError(
                "blocked launch contract requires blocked_reasons"
            )

        if (
            self.policy.modifies_repository
            and self.workspace.mode
            != LaunchWorkspaceMode.ISOLATED_WRITE
        ):
            raise ValueError(
                "repository modification requires isolated_write "
                "workspace"
            )

        return self
