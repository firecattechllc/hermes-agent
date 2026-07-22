"""Governed runtime handoff contracts and dry-run receipts.

This module defines the boundary between validated launch contracts and
future runtime adapters.

The implementation in this slice is intentionally non-executing. It does
not start subprocesses, resolve providers, create workspaces, modify files,
contact remote services, or launch workers.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Protocol, Tuple, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from hermes_cli.agent_roles.launch import (
    LaunchContract,
    LaunchContractStatus,
)
from hermes_cli.agent_roles.launch_validation import (
    LaunchValidationResult,
)


RUNTIME_HANDOFF_SCHEMA_VERSION = 1


class RuntimeHandoffMode(str, Enum):
    """Supported handoff modes."""

    DRY_RUN = "dry_run"


class RuntimeHandoffStatus(str, Enum):
    """Outcome of a governed runtime handoff attempt."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"


class RuntimeHandoffRequest(BaseModel):
    """Immutable request presented to a runtime adapter."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: int = Field(
        default=RUNTIME_HANDOFF_SCHEMA_VERSION,
        ge=1,
    )
    mode: RuntimeHandoffMode = RuntimeHandoffMode.DRY_RUN
    contract: LaunchContract
    validation: LaunchValidationResult
    requested_at: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _validate_request(self) -> "RuntimeHandoffRequest":
        if (
            self.schema_version
            != RUNTIME_HANDOFF_SCHEMA_VERSION
        ):
            raise ValueError(
                "unsupported runtime handoff schema version"
            )

        if (
            self.validation.contract_id
            != self.contract.contract_id
        ):
            raise ValueError(
                "validation contract_id does not match "
                "launch contract"
            )

        return self

    @property
    def request_fingerprint(self) -> str:
        """Return a deterministic fingerprint of the handoff input."""
        payload = self.model_dump(mode="json")
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(canonical).hexdigest()


class RuntimeHandoffReceipt(BaseModel):
    """Immutable evidence returned by a runtime adapter."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    schema_version: int = Field(
        default=RUNTIME_HANDOFF_SCHEMA_VERSION,
        ge=1,
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
    contract_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    runtime: str = Field(
        ...,
        min_length=1,
        max_length=128,
    )
    mode: RuntimeHandoffMode
    status: RuntimeHandoffStatus
    accepted: bool
    reasons: Tuple[str, ...] = Field(default_factory=tuple)
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
    created_at: int = Field(..., ge=0)
    execution_started: bool = False
    process_id: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _validate_receipt(self) -> "RuntimeHandoffReceipt":
        if (
            self.schema_version
            != RUNTIME_HANDOFF_SCHEMA_VERSION
        ):
            raise ValueError(
                "unsupported runtime handoff receipt "
                "schema version"
            )

        if self.status == RuntimeHandoffStatus.ACCEPTED:
            if not self.accepted:
                raise ValueError(
                    "accepted receipt must set accepted=true"
                )

            if self.reasons:
                raise ValueError(
                    "accepted receipt must not contain "
                    "rejection reasons"
                )

        if self.status == RuntimeHandoffStatus.REJECTED:
            if self.accepted:
                raise ValueError(
                    "rejected receipt must set accepted=false"
                )

            if not self.reasons:
                raise ValueError(
                    "rejected receipt requires at least "
                    "one reason"
                )

        if self.mode == RuntimeHandoffMode.DRY_RUN:
            if self.execution_started:
                raise ValueError(
                    "dry-run receipt cannot report execution"
                )

            if self.process_id is not None:
                raise ValueError(
                    "dry-run receipt cannot contain process_id"
                )

        return self


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Interface implemented by governed runtime adapters."""

    @property
    def adapter_name(self) -> str:
        """Stable adapter identifier."""
        ...

    @property
    def adapter_version(self) -> str:
        """Stable adapter implementation version."""
        ...

    def handoff(
        self,
        request: RuntimeHandoffRequest,
    ) -> RuntimeHandoffReceipt:
        """Evaluate or execute a governed runtime handoff."""
        ...


class DeterministicDryRunAdapter:
    """Non-executing adapter for validating the handoff boundary."""

    adapter_name = "deterministic-dry-run"
    adapter_version = "1"

    def handoff(
        self,
        request: RuntimeHandoffRequest,
    ) -> RuntimeHandoffReceipt:
        """Return deterministic acceptance or rejection evidence."""
        reasons = self._rejection_reasons(request)
        accepted = not reasons
        fingerprint = request.request_fingerprint

        receipt_seed = "|".join(
            (
                self.adapter_name,
                self.adapter_version,
                request.contract.contract_id,
                fingerprint,
                (
                    RuntimeHandoffStatus.ACCEPTED.value
                    if accepted
                    else RuntimeHandoffStatus.REJECTED.value
                ),
            )
        )
        receipt_digest = hashlib.sha256(
            receipt_seed.encode("utf-8")
        ).hexdigest()[:24]

        return RuntimeHandoffReceipt(
            receipt_id=f"handoff_{receipt_digest}",
            request_fingerprint=fingerprint,
            contract_id=request.contract.contract_id,
            runtime=request.contract.environment.runtime,
            mode=request.mode,
            status=(
                RuntimeHandoffStatus.ACCEPTED
                if accepted
                else RuntimeHandoffStatus.REJECTED
            ),
            accepted=accepted,
            reasons=reasons,
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
            created_at=request.requested_at,
            execution_started=False,
            process_id=None,
        )

    @staticmethod
    def _rejection_reasons(
        request: RuntimeHandoffRequest,
    ) -> Tuple[str, ...]:
        reasons = []

        if request.mode != RuntimeHandoffMode.DRY_RUN:
            reasons.append(
                "adapter supports dry-run handoffs only"
            )

        if (
            request.contract.status
            != LaunchContractStatus.READY
        ):
            reasons.append(
                "launch contract is not ready"
            )

        if request.contract.blocked_reasons:
            reasons.append(
                "launch contract contains blocked reasons"
            )

        if not request.validation.valid:
            reasons.append(
                "launch contract validation failed"
            )

        if request.validation.errors:
            reasons.append(
                "launch validation contains error diagnostics"
            )

        return tuple(reasons)


class RuntimeHandoffService:
    """Submit governed requests to an explicit runtime adapter."""

    def __init__(self, adapter: RuntimeAdapter) -> None:
        if not isinstance(adapter, RuntimeAdapter):
            raise TypeError(
                "adapter does not implement RuntimeAdapter"
            )

        self._adapter = adapter

    @property
    def adapter(self) -> RuntimeAdapter:
        return self._adapter

    def dry_run(
        self,
        contract: LaunchContract,
        validation: LaunchValidationResult,
        *,
        requested_at: int,
    ) -> RuntimeHandoffReceipt:
        """Perform a non-executing runtime handoff."""
        request = RuntimeHandoffRequest(
            mode=RuntimeHandoffMode.DRY_RUN,
            contract=contract,
            validation=validation,
            requested_at=requested_at,
        )

        receipt = self._adapter.handoff(request)

        if receipt.contract_id != contract.contract_id:
            raise ValueError(
                "runtime adapter returned receipt for "
                "a different contract"
            )

        if (
            receipt.request_fingerprint
            != request.request_fingerprint
        ):
            raise ValueError(
                "runtime adapter returned receipt for "
                "a different request"
            )

        if receipt.mode != RuntimeHandoffMode.DRY_RUN:
            raise ValueError(
                "runtime adapter returned a non-dry-run receipt"
            )

        if receipt.execution_started:
            raise ValueError(
                "runtime adapter started execution during dry-run"
            )

        return receipt
