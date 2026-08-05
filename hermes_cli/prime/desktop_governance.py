"""Governed desktop-use runtime for the Mac fleet node.

Fleet Unification live-runtime work. ``tools/computer_use`` already
implements the actual desktop-automation mechanics (screenshots, mouse,
keyboard, drag, app focus) via ``cua-driver``, and its own schema already
documents that "`capture` is free (no side effects). All other actions
require approval unless auto-approved." This module is the fleet-governance
layer in front of that tool for the Mac node specifically: every non-capture
action must be an explicitly admitted, currently-healthy fleet node, scoped
to an explicit app allowlist, bounded by an explicit timeout, and — for
anything beyond read-only inspection — backed by a fresh, single-use
:class:`hermes_cli.prime.operator_approval.OperatorApproval` bound to that
exact action.

This module does not call ``tools/computer_use`` itself and does not modify
it. It is a pure precondition gate (mirroring
:mod:`hermes_cli.prime.sigil_contract`'s relationship to Sigil): a caller
must get an ``ADMITTED`` :class:`DesktopUseDecision` here *before* invoking
the actual computer-use tool, and the decision itself never performs, or
grants authority to perform, any desktop action.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.operator_approval import (
    OperatorApproval,
    OperatorApprovalReplayStore,
    OperatorApprovalScope,
    compute_action_id,
    validate_operator_approval,
)

DESKTOP_USE_SCHEMA_VERSION = 1

# Mirrors tools/computer_use/schema.py's COMPUTER_USE_SCHEMA action enum,
# kept as an explicit closed set (not imported) so a new action added there
# does not silently become dispatchable through this gate — extending this
# module to recognize a new action is a deliberate governance decision.
KNOWN_DESKTOP_USE_ACTIONS = frozenset({
    "capture", "click", "double_click", "right_click", "middle_click", "drag",
    "scroll", "type", "key", "set_value", "wait", "list_apps", "list_windows",
    "focus_app",
})

# Read-only, no-side-effect actions that never require operator approval —
# matches the computer_use schema's own documented exemption for `capture`,
# extended to the two other purely enumerative actions.
UNSCOPED_SAFE_ACTIONS = frozenset({"capture", "list_apps", "list_windows"})

DEFAULT_MAX_TIMEOUT_SECONDS = 30


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


class DesktopUsePolicy(BaseModel):
    """The bounded scope a Mac node's desktop-use is governed by.

    ``allowed_apps`` is a closed allowlist, not a denylist — an app absent
    from it can never be targeted, regardless of action. An empty allowlist
    means only whole-desktop-free actions with no ``app`` are reachable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=DESKTOP_USE_SCHEMA_VERSION)
    allowed_apps: Tuple[str, ...] = ()
    max_timeout_seconds: int = Field(default=DEFAULT_MAX_TIMEOUT_SECONDS, ge=1, le=300)

    @model_validator(mode="after")
    def _apps_are_well_formed(self) -> "DesktopUsePolicy":
        if any(not app.strip() for app in self.allowed_apps):
            raise ValueError("allowed_apps entries cannot be blank")
        if len(set(self.allowed_apps)) != len(self.allowed_apps):
            raise ValueError("allowed_apps cannot contain duplicates")
        return self


class DesktopUseRequest(BaseModel):
    """A single request to perform one governed desktop-use action on Mac."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=DESKTOP_USE_SCHEMA_VERSION)
    request_id: str = Field(..., min_length=1, max_length=160)
    natural_key: Literal["mac"] = "mac"
    action: str = Field(..., min_length=1, max_length=64)
    app: Optional[str] = Field(default=None, max_length=256)
    timeout_seconds: int = Field(default=DEFAULT_MAX_TIMEOUT_SECONDS, ge=1, le=300)
    requested_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)
    operator_approval: Optional[OperatorApproval] = None

    @model_validator(mode="after")
    def _known_action(self) -> "DesktopUseRequest":
        if self.action not in KNOWN_DESKTOP_USE_ACTIONS:
            raise ValueError(f"unknown desktop-use action: {self.action!r}")
        return self

    @property
    def action_id(self) -> str:
        """Content-addressed identifier this action must be approved against."""
        return compute_action_id(
            {"natural_key": self.natural_key, "action": self.action, "app": self.app}
        )


class DesktopUseOutcome(str, Enum):
    ADMITTED = "admitted"
    DENIED = "denied"


class DesktopUseRejectionCode(str, Enum):
    NODE_NOT_DISPATCHABLE = "node_not_dispatchable"
    APP_NOT_IN_WORKSPACE_SCOPE = "app_not_in_workspace_scope"
    TIMEOUT_OUT_OF_BOUNDS = "timeout_out_of_bounds"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_INVALID = "approval_invalid"


class DesktopUseDecision(BaseModel):
    """A deterministic, content-addressed desktop-use governance decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(..., min_length=1, max_length=160)
    request_id: str = Field(..., min_length=1, max_length=160)
    action: str = Field(..., min_length=1, max_length=64)
    action_id: str = Field(..., min_length=1, max_length=160)
    outcome: DesktopUseOutcome
    rejection_code: Optional[DesktopUseRejectionCode] = None
    approval_detail: Optional[str] = Field(default=None, max_length=64)
    decided_at: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _rejection_code_required_unless_admitted(self) -> "DesktopUseDecision":
        if self.outcome == DesktopUseOutcome.DENIED and self.rejection_code is None:
            raise ValueError("a denied desktop-use decision requires a rejection_code")
        if self.outcome == DesktopUseOutcome.ADMITTED and self.rejection_code is not None:
            raise ValueError("an admitted desktop-use decision cannot carry a rejection_code")
        return self

    def grants_no_execution_authority(self) -> None:
        """Documentation no-op — an ADMITTED decision only permits the caller
        to invoke the separately-governed ``tools.computer_use`` tool; it
        does not itself perform any desktop action."""
        return None


def evaluate_desktop_use_request(
    request: DesktopUseRequest,
    policy: DesktopUsePolicy,
    *,
    fleet_runtime: FleetRuntime,
    certification_status: CertificationStatus,
    certification_evidence_ref: Optional[str],
    replay_store: OperatorApprovalReplayStore,
    now: int,
) -> DesktopUseDecision:
    """Deterministically evaluate one desktop-use request. Fail closed.

    On an ADMITTED outcome for a non-safe action, the supplied approval has
    already been consumed (single-use) by this call — a second evaluation
    of the same request, even unmodified, is rejected as
    ``approval_invalid`` (replayed).
    """

    def _decision(
        outcome: DesktopUseOutcome,
        *,
        rejection_code: Optional[DesktopUseRejectionCode] = None,
        approval_detail: Optional[str] = None,
    ) -> DesktopUseDecision:
        payload = {
            "request_id": request.request_id,
            "action_id": request.action_id,
            "outcome": outcome.value,
            "rejection_code": rejection_code.value if rejection_code else None,
            "decided_at": now,
        }
        return DesktopUseDecision(
            decision_id=f"pdsk_{_digest(payload)[:24]}",
            request_id=request.request_id,
            action=request.action,
            action_id=request.action_id,
            outcome=outcome,
            rejection_code=rejection_code,
            approval_detail=approval_detail,
            decided_at=now,
            correlation_id=request.correlation_id,
        )

    if not fleet_runtime.is_dispatchable(
        "mac",
        now=now,
        certification_status=certification_status,
        certification_evidence_ref=certification_evidence_ref,
    ):
        return _decision(
            DesktopUseOutcome.DENIED,
            rejection_code=DesktopUseRejectionCode.NODE_NOT_DISPATCHABLE,
        )

    if request.app is not None and request.app not in policy.allowed_apps:
        return _decision(
            DesktopUseOutcome.DENIED,
            rejection_code=DesktopUseRejectionCode.APP_NOT_IN_WORKSPACE_SCOPE,
        )

    if request.timeout_seconds > policy.max_timeout_seconds:
        return _decision(
            DesktopUseOutcome.DENIED,
            rejection_code=DesktopUseRejectionCode.TIMEOUT_OUT_OF_BOUNDS,
        )

    if request.action in UNSCOPED_SAFE_ACTIONS:
        return _decision(DesktopUseOutcome.ADMITTED)

    node = fleet_runtime.get_node("mac")
    subject_identity_id = node.identity_id if node is not None else "unknown:mac"

    if request.operator_approval is None:
        return _decision(
            DesktopUseOutcome.DENIED,
            rejection_code=DesktopUseRejectionCode.APPROVAL_REQUIRED,
        )

    ok, code = validate_operator_approval(
        request.operator_approval,
        expected_scope=OperatorApprovalScope.DESKTOP_USE,
        expected_action_id=request.action_id,
        expected_subject_identity_id=subject_identity_id,
        now=now,
        replay_store=replay_store,
    )
    if not ok:
        return _decision(
            DesktopUseOutcome.DENIED,
            rejection_code=DesktopUseRejectionCode.APPROVAL_INVALID,
            approval_detail=code.value if code else None,
        )

    return _decision(DesktopUseOutcome.ADMITTED)


def evaluate_and_publish_desktop_use(
    request: DesktopUseRequest,
    policy: DesktopUsePolicy,
    *,
    fleet_runtime: FleetRuntime,
    certification_status: CertificationStatus,
    certification_evidence_ref: Optional[str],
    replay_store: OperatorApprovalReplayStore,
    now: int,
) -> DesktopUseDecision:
    """Evaluate a desktop-use request and publish the decision to Mission Control.

    Thin composition wrapper: calls :func:`evaluate_desktop_use_request` (the
    pure decision) and then ``fleet_runtime.visibility.publish_desktop_use_decision``
    (the I/O). Kept here rather than on ``FleetRuntime`` itself so
    ``fleet_runtime.py`` never needs to import this module — ``FleetRuntime``
    stays a dependency of ``desktop_governance``, never the reverse.
    """
    decision = evaluate_desktop_use_request(
        request,
        policy,
        fleet_runtime=fleet_runtime,
        certification_status=certification_status,
        certification_evidence_ref=certification_evidence_ref,
        replay_store=replay_store,
        now=now,
    )
    fleet_runtime.visibility.publish_desktop_use_decision(fleet_runtime.project_id, decision)
    return decision
