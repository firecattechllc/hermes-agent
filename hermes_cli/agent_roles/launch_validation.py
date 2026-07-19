"""Independent launch-contract compatibility validation.

This module validates an already-built :class:`LaunchContract` against an
explicit runtime capability description. It does not rebuild contracts,
perform persistence, mutate assignments, create workspaces, select providers,
or execute processes.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
    LaunchWorkspaceMode,
)


class LaunchValidationSeverity(str, Enum):
    """Severity of one launch validation diagnostic."""

    ERROR = "error"
    WARNING = "warning"


class LaunchValidationCode(str, Enum):
    """Stable machine-readable validation diagnostic codes."""

    CONTRACT_BLOCKED = "contract_blocked"
    RUNTIME_MISMATCH = "runtime_mismatch"
    ENGINE_UNSUPPORTED = "engine_unsupported"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    MODEL_UNSUPPORTED = "model_unsupported"
    CAPABILITY_MISSING = "capability_missing"
    REPOSITORY_WRITE_UNSUPPORTED = (
        "repository_write_unsupported"
    )
    ISOLATED_WORKSPACE_UNSUPPORTED = (
        "isolated_workspace_unsupported"
    )
    HUMAN_APPROVAL_MISSING = "human_approval_missing"
    ENVIRONMENT_KEY_UNSUPPORTED = (
        "environment_key_unsupported"
    )
    ENVIRONMENT_KEY_REQUIRED = "environment_key_required"
    BASE_REF_REQUIRED = "base_ref_required"
    WORKSPACE_ID_REQUIRED = "workspace_id_required"


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


def _normalise_tuple(
    values: Tuple[str, ...],
    field_name: str,
) -> Tuple[str, ...]:
    normalised = []
    seen = set()

    for raw_value in values:
        value = _required_text(raw_value, field_name)

        if value in seen:
            continue

        seen.add(value)
        normalised.append(value)

    return tuple(normalised)


class RuntimeCompatibility(BaseModel):
    """Explicit capabilities exposed by one candidate runtime."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    runtime: str = Field(..., min_length=1, max_length=128)
    supported_engines: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    supported_providers: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    supported_models: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    capabilities: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    allowed_environment_keys: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    required_environment_keys: Tuple[str, ...] = Field(
        default_factory=tuple
    )
    supports_repository_write: bool = False
    supports_isolated_workspace: bool = False
    requires_base_ref_for_write: bool = True

    @field_validator("runtime")
    @classmethod
    def _normalise_runtime(cls, value: str) -> str:
        return _required_text(value, "runtime")

    @field_validator(
        "supported_engines",
        "supported_providers",
        "supported_models",
        "capabilities",
        "allowed_environment_keys",
        "required_environment_keys",
    )
    @classmethod
    def _normalise_sequences(
        cls,
        values: Tuple[str, ...],
        info,
    ) -> Tuple[str, ...]:
        return _normalise_tuple(values, info.field_name)


class LaunchValidationIssue(BaseModel):
    """One immutable compatibility diagnostic."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    code: LaunchValidationCode
    severity: LaunchValidationSeverity
    message: str = Field(..., min_length=1)
    field: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @field_validator("message")
    @classmethod
    def _normalise_message(cls, value: str) -> str:
        return _required_text(value, "message")

    @field_validator("field")
    @classmethod
    def _normalise_field(
        cls,
        value: Optional[str],
    ) -> Optional[str]:
        return _optional_text(value, "field")


class LaunchValidationResult(BaseModel):
    """Immutable result of validating one launch contract."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    contract_id: str = Field(..., min_length=1, max_length=128)
    valid: bool
    issues: Tuple[LaunchValidationIssue, ...] = Field(
        default_factory=tuple
    )

    @field_validator("contract_id")
    @classmethod
    def _normalise_contract_id(cls, value: str) -> str:
        return _required_text(value, "contract_id")

    @property
    def errors(self) -> Tuple[LaunchValidationIssue, ...]:
        """Return error diagnostics in deterministic order."""
        return tuple(
            issue
            for issue in self.issues
            if issue.severity == LaunchValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> Tuple[LaunchValidationIssue, ...]:
        """Return warning diagnostics in deterministic order."""
        return tuple(
            issue
            for issue in self.issues
            if issue.severity
            == LaunchValidationSeverity.WARNING
        )


class LaunchContractValidator:
    """Validate immutable launch contracts without side effects."""

    def validate(
        self,
        contract: LaunchContract,
        compatibility: RuntimeCompatibility,
    ) -> LaunchValidationResult:
        """Validate one contract against one candidate runtime."""
        issues = []

        if contract.status == LaunchContractStatus.BLOCKED:
            issues.append(
                self._error(
                    LaunchValidationCode.CONTRACT_BLOCKED,
                    (
                        "launch contract is blocked: "
                        + "; ".join(contract.blocked_reasons)
                    ),
                    "status",
                )
            )

        if contract.environment.runtime != compatibility.runtime:
            issues.append(
                self._error(
                    LaunchValidationCode.RUNTIME_MISMATCH,
                    (
                        "contract runtime "
                        f"{contract.environment.runtime!r} does not "
                        "match compatibility runtime "
                        f"{compatibility.runtime!r}"
                    ),
                    "environment.runtime",
                )
            )

        self._validate_optional_selection(
            issues,
            value=contract.environment.engine,
            supported=compatibility.supported_engines,
            code=LaunchValidationCode.ENGINE_UNSUPPORTED,
            field="environment.engine",
            label="engine",
        )
        self._validate_optional_selection(
            issues,
            value=contract.environment.provider,
            supported=compatibility.supported_providers,
            code=LaunchValidationCode.PROVIDER_UNSUPPORTED,
            field="environment.provider",
            label="provider",
        )
        self._validate_optional_selection(
            issues,
            value=contract.environment.model,
            supported=compatibility.supported_models,
            code=LaunchValidationCode.MODEL_UNSUPPORTED,
            field="environment.model",
            label="model",
        )

        available_capabilities = set(
            compatibility.capabilities
        )

        for capability in contract.policy.required_capabilities:
            if capability not in available_capabilities:
                issues.append(
                    self._error(
                        LaunchValidationCode.CAPABILITY_MISSING,
                        (
                            "runtime lacks required capability: "
                            f"{capability}"
                        ),
                        "policy.required_capabilities",
                    )
                )

        if contract.policy.modifies_repository:
            if not compatibility.supports_repository_write:
                issues.append(
                    self._error(
                        LaunchValidationCode
                        .REPOSITORY_WRITE_UNSUPPORTED,
                        (
                            "runtime does not support repository "
                            "modification"
                        ),
                        "policy.modifies_repository",
                    )
                )

            if not compatibility.supports_isolated_workspace:
                issues.append(
                    self._error(
                        LaunchValidationCode
                        .ISOLATED_WORKSPACE_UNSUPPORTED,
                        (
                            "runtime does not support isolated "
                            "write workspaces"
                        ),
                        "workspace.mode",
                    )
                )

            if (
                contract.workspace.mode
                == LaunchWorkspaceMode.ISOLATED_WRITE
                and contract.workspace.workspace_id is None
            ):
                issues.append(
                    self._error(
                        LaunchValidationCode
                        .WORKSPACE_ID_REQUIRED,
                        (
                            "isolated write launch requires a "
                            "workspace_id"
                        ),
                        "workspace.workspace_id",
                    )
                )

            if (
                compatibility.requires_base_ref_for_write
                and contract.workspace.base_ref is None
            ):
                issues.append(
                    self._error(
                        LaunchValidationCode.BASE_REF_REQUIRED,
                        (
                            "repository-modifying launch requires "
                            "a base_ref for this runtime"
                        ),
                        "workspace.base_ref",
                    )
                )

        if (
            contract.policy.risk_level in {"high", "critical"}
            and not contract.policy.human_approved
        ):
            issues.append(
                self._error(
                    LaunchValidationCode.HUMAN_APPROVAL_MISSING,
                    (
                        "high-risk launch requires recorded "
                        "human approval"
                    ),
                    "policy.human_approved",
                )
            )

        environment = dict(contract.environment.environment)
        allowed_keys = set(
            compatibility.allowed_environment_keys
        )

        if allowed_keys:
            for key in environment:
                if key not in allowed_keys:
                    issues.append(
                        self._error(
                            LaunchValidationCode
                            .ENVIRONMENT_KEY_UNSUPPORTED,
                            (
                                "runtime does not allow environment "
                                f"key: {key}"
                            ),
                            "environment.environment",
                        )
                    )

        for key in compatibility.required_environment_keys:
            if key not in environment:
                issues.append(
                    self._error(
                        LaunchValidationCode
                        .ENVIRONMENT_KEY_REQUIRED,
                        (
                            "runtime requires environment key: "
                            f"{key}"
                        ),
                        "environment.environment",
                    )
                )

        immutable_issues = tuple(issues)

        return LaunchValidationResult(
            contract_id=contract.contract_id,
            valid=not any(
                issue.severity
                == LaunchValidationSeverity.ERROR
                for issue in immutable_issues
            ),
            issues=immutable_issues,
        )

    @classmethod
    def validate_many(
        cls,
        contracts: Iterable[LaunchContract],
        compatibility: RuntimeCompatibility,
    ) -> Tuple[LaunchValidationResult, ...]:
        """Validate contracts in input order without mutation."""
        validator = cls()

        return tuple(
            validator.validate(contract, compatibility)
            for contract in contracts
        )

    @staticmethod
    def _validate_optional_selection(
        issues: list[LaunchValidationIssue],
        *,
        value: Optional[str],
        supported: Tuple[str, ...],
        code: LaunchValidationCode,
        field: str,
        label: str,
    ) -> None:
        if value is None or not supported:
            return

        if value not in supported:
            issues.append(
                LaunchContractValidator._error(
                    code,
                    (
                        f"runtime does not support {label}: "
                        f"{value}"
                    ),
                    field,
                )
            )

    @staticmethod
    def _error(
        code: LaunchValidationCode,
        message: str,
        field: Optional[str] = None,
    ) -> LaunchValidationIssue:
        return LaunchValidationIssue(
            code=code,
            severity=LaunchValidationSeverity.ERROR,
            message=message,
            field=field,
        )
