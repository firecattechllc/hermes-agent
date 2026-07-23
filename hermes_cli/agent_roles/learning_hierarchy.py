"""Governed Step 31 Big Sister / Little Sister learning hierarchy.

This subsystem makes deterministic routing and lesson-planning decisions only.
It does not execute models, contact remote nodes, modify policy, spend money,
or grant execution authority.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


LEARNING_HIERARCHY_SCHEMA_VERSION = 1


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _clean_identifier(value: str) -> str:
    clean = value.strip().lower().replace("_", "-")
    if not clean or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-.:/"
        for character in clean
    ):
        raise ValueError("identifier must be lowercase and stable")
    return clean


def _clean_reference(value: str) -> str:
    clean = value.strip()
    if not clean or "://" not in clean:
        raise ValueError("evidence values must be sanitized references")
    lowered = clean.lower()
    forbidden = (
        "prompt=",
        "raw_prompt",
        "api_key",
        "api-key",
        "authorization",
        "bearer ",
        "password",
        "private_key",
        "secret=",
        "token=",
    )
    if any(marker in lowered for marker in forbidden):
        raise ValueError("learning evidence contains sensitive content")
    return clean


class LearningNodeRole(str, Enum):
    BIG_SISTER = "big_sister"
    LITTLE_SISTER = "little_sister"


class LearningCapability(str, Enum):
    LOCAL_MEMORY = "local_memory"
    LOCAL_REASONING = "local_reasoning"
    FINANCIAL_SENTIMENT = "financial_sentiment"
    REMOTE_GATEWAY = "remote_gateway"
    BIG_SISTER_TEACHING = "big_sister_teaching"
    CLOUD_SPECIALIST = "cloud_specialist"


class LearningRoute(str, Enum):
    LOCAL_MEMORY = "local_memory"
    LOCAL_OLLAMA = "local_ollama"
    FINBERT = "finbert"
    FREELLMAPI = "freellmapi"
    BIG_SISTER = "big_sister"
    CLOUD_SPECIALIST_APPROVAL_REQUIRED = (
        "cloud_specialist_approval_required"
    )
    DEFERRED_LESSON = "deferred_lesson"
    BLOCKED = "blocked"


class LearningDecisionState(str, Enum):
    ROUTED = "routed"
    APPROVAL_REQUIRED = "approval_required"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


class LearningNodeState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    role: LearningNodeRole
    available: bool = True
    capabilities: Tuple[LearningCapability, ...] = ()
    evidence_references: Tuple[str, ...] = ()

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator("capabilities")
    @classmethod
    def _capabilities(
        cls,
        values: Tuple[LearningCapability, ...],
    ) -> Tuple[LearningCapability, ...]:
        return tuple(sorted(set(values), key=lambda item: item.value))

    @field_validator("evidence_references")
    @classmethod
    def _evidence(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return tuple(sorted({_clean_reference(value) for value in values}))


class LearningRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    project_id: str
    task_id: str
    task_type: str
    objective_reference: str
    required_capabilities: Tuple[LearningCapability, ...] = ()
    attempted_routes: Tuple[LearningRoute, ...] = ()
    attempt_evidence_references: Tuple[str, ...] = ()
    budget_limit_micros: int = Field(default=0, ge=0)
    remote_gateway_permitted: bool = True
    big_sister_escalation_permitted: bool = True
    cloud_specialist_permitted: bool = False
    cloud_specialist_requires_approval: bool = True
    maximum_learning_depth: int = Field(default=6, ge=1, le=16)
    created_at: int = Field(..., ge=0)
    idempotency_key: str

    @field_validator(
        "request_id",
        "project_id",
        "task_id",
        "task_type",
        "idempotency_key",
    )
    @classmethod
    def _identifiers(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator("objective_reference")
    @classmethod
    def _objective_reference(cls, value: str) -> str:
        return _clean_reference(value)

    @field_validator("required_capabilities")
    @classmethod
    def _required_capabilities(
        cls,
        values: Tuple[LearningCapability, ...],
    ) -> Tuple[LearningCapability, ...]:
        return tuple(sorted(set(values), key=lambda item: item.value))

    @field_validator("attempted_routes")
    @classmethod
    def _attempted_routes(
        cls,
        values: Tuple[LearningRoute, ...],
    ) -> Tuple[LearningRoute, ...]:
        output = []
        seen = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                output.append(value)
        return tuple(output)

    @field_validator("attempt_evidence_references")
    @classmethod
    def _attempt_evidence(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return tuple(
            sorted({_clean_reference(value) for value in values})
        )

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class LessonRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lesson_request_id: str
    learning_request_id: str
    project_id: str
    task_id: str
    requested_from: LearningNodeRole
    requested_by: LearningNodeRole
    objective_reference: str
    attempted_routes: Tuple[LearningRoute, ...]
    evidence_references: Tuple[str, ...]
    required_capabilities: Tuple[LearningCapability, ...]
    requested_outcome: str = Field(..., min_length=1, max_length=512)
    created_at: int = Field(..., ge=0)

    @field_validator(
        "lesson_request_id",
        "learning_request_id",
        "project_id",
        "task_id",
    )
    @classmethod
    def _ids(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator("objective_reference")
    @classmethod
    def _objective(cls, value: str) -> str:
        return _clean_reference(value)

    @field_validator("evidence_references")
    @classmethod
    def _refs(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(sorted({_clean_reference(value) for value in values}))


class LessonPackage(BaseModel):
    """A governed teaching artifact produced outside this decision engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lesson_id: str
    lesson_request_id: str
    version: int = Field(default=1, ge=1)
    instruction_references: Tuple[str, ...]
    example_references: Tuple[str, ...] = ()
    verification_references: Tuple[str, ...]
    safety_policy_references: Tuple[str, ...]
    created_at: int = Field(..., ge=0)
    expires_at: Optional[int] = Field(default=None, ge=0)

    @field_validator("lesson_id", "lesson_request_id")
    @classmethod
    def _lesson_ids(cls, value: str) -> str:
        return _clean_identifier(value)

    @field_validator(
        "instruction_references",
        "example_references",
        "verification_references",
        "safety_policy_references",
    )
    @classmethod
    def _lesson_refs(
        cls,
        values: Tuple[str, ...],
    ) -> Tuple[str, ...]:
        return tuple(sorted({_clean_reference(value) for value in values}))

    @model_validator(mode="after")
    def _valid_lesson(self) -> "LessonPackage":
        if not self.instruction_references:
            raise ValueError("lesson requires instruction references")
        if not self.verification_references:
            raise ValueError("lesson requires verification references")
        if not self.safety_policy_references:
            raise ValueError("lesson requires safety policy references")
        if (
            self.expires_at is not None
            and self.expires_at < self.created_at
        ):
            raise ValueError("lesson expiry cannot precede creation")
        return self


class LearningDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = LEARNING_HIERARCHY_SCHEMA_VERSION
    decision_id: str
    request_id: str
    request_fingerprint: str = Field(..., min_length=64, max_length=64)
    project_id: str
    task_id: str
    selected_route: LearningRoute
    state: LearningDecisionState
    reason_codes: Tuple[str, ...]
    fallback_chain: Tuple[LearningRoute, ...]
    requires_approval: bool = False
    execution_permitted: bool = False
    lesson_request: Optional[LessonRequest] = None
    created_at: int = Field(..., ge=0)
    idempotency_key: str

    @model_validator(mode="after")
    def _consistent(self) -> "LearningDecision":
        if self.schema_version != LEARNING_HIERARCHY_SCHEMA_VERSION:
            raise ValueError("unsupported learning hierarchy schema version")

        if self.execution_permitted:
            raise ValueError(
                "Step 31 learning decisions cannot grant execution authority"
            )

        approval_route = (
            self.selected_route
            == LearningRoute.CLOUD_SPECIALIST_APPROVAL_REQUIRED
        )
        if approval_route != self.requires_approval:
            raise ValueError("approval state and learning route disagree")

        deferred = self.selected_route == LearningRoute.DEFERRED_LESSON
        if deferred != (self.lesson_request is not None):
            raise ValueError(
                "deferred lesson decisions require one lesson request"
            )

        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
        ).lower()
        forbidden = (
            "raw_prompt",
            "api_key",
            "api-key",
            "authorization",
            "bearer ",
            "password",
            "private_key",
            "secret=",
            "token=",
        )
        if any(marker in encoded for marker in forbidden):
            raise ValueError("learning decision contains sensitive content")

        return self


class GovernedLearningHierarchy:
    """Select the next governed learning route without executing it."""

    def decide(
        self,
        request: LearningRequest,
        *,
        little_sister: LearningNodeState,
        big_sister: LearningNodeState,
        timestamp: int,
    ) -> LearningDecision:
        if timestamp < 0:
            raise ValueError(
                "learning hierarchy timestamp must be non-negative"
            )
        if little_sister.role is not LearningNodeRole.LITTLE_SISTER:
            raise ValueError("little_sister must use the Little Sister role")
        if big_sister.role is not LearningNodeRole.BIG_SISTER:
            raise ValueError("big_sister must use the Big Sister role")

        attempted = set(request.attempted_routes)
        candidates = self._candidate_routes(
            request=request,
            little_sister=little_sister,
            big_sister=big_sister,
        )
        unattempted = tuple(
            route for route in candidates if route not in attempted
        )

        if len(request.attempted_routes) >= request.maximum_learning_depth:
            return self._decision(
                request=request,
                route=LearningRoute.BLOCKED,
                state=LearningDecisionState.BLOCKED,
                reasons=("learning_depth_limit",),
                fallback=(),
                timestamp=timestamp,
            )

        if unattempted:
            selected = unattempted[0]
            fallback = unattempted[1:]

            if selected == LearningRoute.CLOUD_SPECIALIST_APPROVAL_REQUIRED:
                return self._decision(
                    request=request,
                    route=selected,
                    state=LearningDecisionState.APPROVAL_REQUIRED,
                    reasons=(
                        "local_and_sister_routes_exhausted",
                        "cloud_specialist_requires_existing_approval",
                    ),
                    fallback=fallback,
                    timestamp=timestamp,
                    requires_approval=True,
                )

            return self._decision(
                request=request,
                route=selected,
                state=LearningDecisionState.ROUTED,
                reasons=self._route_reasons(selected),
                fallback=fallback,
                timestamp=timestamp,
            )

        lesson_request = self._lesson_request(
            request=request,
            timestamp=timestamp,
        )
        return self._decision(
            request=request,
            route=LearningRoute.DEFERRED_LESSON,
            state=LearningDecisionState.DEFERRED,
            reasons=(
                "eligible_routes_exhausted_or_unavailable",
                "little_sister_remains_operational",
                "lesson_queued_for_big_sister",
            ),
            fallback=(),
            timestamp=timestamp,
            lesson_request=lesson_request,
        )

    @staticmethod
    def _candidate_routes(
        *,
        request: LearningRequest,
        little_sister: LearningNodeState,
        big_sister: LearningNodeState,
    ) -> Tuple[LearningRoute, ...]:
        routes = []
        local = set(little_sister.capabilities)
        required = set(request.required_capabilities)

        if (
            little_sister.available
            and LearningCapability.LOCAL_MEMORY in local
        ):
            routes.append(LearningRoute.LOCAL_MEMORY)

        if (
            LearningCapability.FINANCIAL_SENTIMENT in required
            and little_sister.available
            and LearningCapability.FINANCIAL_SENTIMENT in local
        ):
            routes.append(LearningRoute.FINBERT)

        if (
            little_sister.available
            and LearningCapability.LOCAL_REASONING in local
        ):
            routes.append(LearningRoute.LOCAL_OLLAMA)

        if (
            request.remote_gateway_permitted
            and little_sister.available
            and LearningCapability.REMOTE_GATEWAY in local
        ):
            routes.append(LearningRoute.FREELLMAPI)

        if (
            request.big_sister_escalation_permitted
            and big_sister.available
            and LearningCapability.BIG_SISTER_TEACHING
            in set(big_sister.capabilities)
        ):
            routes.append(LearningRoute.BIG_SISTER)

        if (
            request.cloud_specialist_permitted
            and request.cloud_specialist_requires_approval
            and LearningCapability.CLOUD_SPECIALIST
            in set(big_sister.capabilities)
        ):
            routes.append(
                LearningRoute.CLOUD_SPECIALIST_APPROVAL_REQUIRED
            )

        return tuple(routes)

    @staticmethod
    def _route_reasons(route: LearningRoute) -> Tuple[str, ...]:
        reasons = {
            LearningRoute.LOCAL_MEMORY: (
                "local_first_policy",
                "local_memory_available",
            ),
            LearningRoute.FINBERT: (
                "financial_sentiment_specialist_required",
                "local_specialist_available",
            ),
            LearningRoute.LOCAL_OLLAMA: (
                "local_first_policy",
                "local_reasoning_available",
            ),
            LearningRoute.FREELLMAPI: (
                "governed_remote_gateway_permitted",
                "local_routes_precede_remote_gateway",
            ),
            LearningRoute.BIG_SISTER: (
                "big_sister_available",
                "teaching_escalation_permitted",
            ),
        }
        return reasons.get(route, ("governed_route_selected",))

    @staticmethod
    def _lesson_request(
        *,
        request: LearningRequest,
        timestamp: int,
    ) -> LessonRequest:
        identity = {
            "request_id": request.request_id,
            "fingerprint": request.fingerprint,
            "attempted_routes": [
                route.value for route in request.attempted_routes
            ],
            "created_at": timestamp,
        }
        lesson_id = f"lesson-request-{_digest(identity)[:24]}"
        return LessonRequest(
            lesson_request_id=lesson_id,
            learning_request_id=request.request_id,
            project_id=request.project_id,
            task_id=request.task_id,
            requested_from=LearningNodeRole.BIG_SISTER,
            requested_by=LearningNodeRole.LITTLE_SISTER,
            objective_reference=request.objective_reference,
            attempted_routes=request.attempted_routes,
            evidence_references=request.attempt_evidence_references,
            required_capabilities=request.required_capabilities,
            requested_outcome=(
                "Create a governed lesson package that enables Little Sister "
                "to retry this task locally with verification evidence."
            ),
            created_at=timestamp,
        )

    @staticmethod
    def _decision(
        *,
        request: LearningRequest,
        route: LearningRoute,
        state: LearningDecisionState,
        reasons: Tuple[str, ...],
        fallback: Tuple[LearningRoute, ...],
        timestamp: int,
        requires_approval: bool = False,
        lesson_request: Optional[LessonRequest] = None,
    ) -> LearningDecision:
        identity = {
            "request_fingerprint": request.fingerprint,
            "route": route.value,
            "state": state.value,
            "reasons": reasons,
            "fallback": [item.value for item in fallback],
            "created_at": timestamp,
        }
        return LearningDecision(
            decision_id=f"learning-decision-{_digest(identity)[:24]}",
            request_id=request.request_id,
            request_fingerprint=request.fingerprint,
            project_id=request.project_id,
            task_id=request.task_id,
            selected_route=route,
            state=state,
            reason_codes=reasons,
            fallback_chain=fallback,
            requires_approval=requires_approval,
            execution_permitted=False,
            lesson_request=lesson_request,
            created_at=timestamp,
            idempotency_key=request.idempotency_key,
        )
