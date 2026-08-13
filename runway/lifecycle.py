"""Provider lifecycle state machine (spec section 4).

``discovered`` is the only state a newly-found candidate may start in, and
``APPROVED`` is the only state that makes a provider routable for anything
above :attr:`~runway.classification.DataClassification.SYNTHETIC`
(:mod:`runway.scoring` enforces this, not this module — this module only
enforces which *transitions* are legal).
"""

from __future__ import annotations

from enum import Enum


class LifecycleState(str, Enum):
    DISCOVERED = "discovered"
    PENDING_QUALIFICATION = "pending_qualification"
    SYNTHETIC_TESTING = "synthetic_testing"
    QUALIFIED = "qualified"
    APPROVED = "approved"
    DEGRADED = "degraded"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class QuarantineReason(str, Enum):
    IDENTITY_MISMATCH = "identity_mismatch"
    CANARY_REGRESSION = "canary_regression"
    EXCESSIVE_FAILURES = "excessive_failures"
    BILLING_ANOMALY = "billing_anomaly"
    MODEL_SUBSTITUTION_SUSPECTED = "model_substitution_suspected"
    HEALTH_DEGRADATION = "health_degradation"
    POLICY_VIOLATION = "policy_violation"


#: Explicit transition graph. Automatic discovery can never jump straight to
#: APPROVED — every edge into APPROVED originates from QUALIFIED, and only a
#: human/policy-driven :meth:`~runway.qualification.QualificationService.authorize`
#: call performs that specific transition (qualification passing alone does not).
ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DISCOVERED: frozenset({LifecycleState.PENDING_QUALIFICATION, LifecycleState.REVOKED}),
    LifecycleState.PENDING_QUALIFICATION: frozenset({
        LifecycleState.SYNTHETIC_TESTING, LifecycleState.REVOKED,
    }),
    LifecycleState.SYNTHETIC_TESTING: frozenset({
        LifecycleState.QUALIFIED, LifecycleState.PENDING_QUALIFICATION, LifecycleState.REVOKED,
    }),
    LifecycleState.QUALIFIED: frozenset({
        LifecycleState.APPROVED, LifecycleState.SYNTHETIC_TESTING, LifecycleState.REVOKED,
    }),
    LifecycleState.APPROVED: frozenset({
        LifecycleState.DEGRADED, LifecycleState.QUARANTINED, LifecycleState.REVOKED,
    }),
    LifecycleState.DEGRADED: frozenset({
        LifecycleState.APPROVED, LifecycleState.QUARANTINED, LifecycleState.REVOKED,
    }),
    LifecycleState.QUARANTINED: frozenset({
        LifecycleState.SYNTHETIC_TESTING, LifecycleState.REVOKED,
    }),
    LifecycleState.REVOKED: frozenset(),
}


class InvalidLifecycleTransition(ValueError):
    def __init__(self, current: LifecycleState, target: LifecycleState) -> None:
        super().__init__(f"illegal provider lifecycle transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def transition(current: LifecycleState, target: LifecycleState) -> LifecycleState:
    """Validate a lifecycle transition. Raises :class:`InvalidLifecycleTransition`
    rather than allowing an implicit/soft transition — config validation must
    reject invalid trust/lifecycle transitions (spec section 20).
    """
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidLifecycleTransition(current, target)
    return target


def is_routable(state: LifecycleState) -> bool:
    """Only APPROVED (and its temporary DEGRADED sibling, gated further by
    health checks elsewhere) may ever be considered for non-synthetic routing.
    """
    return state in (LifecycleState.APPROVED, LifecycleState.DEGRADED)
