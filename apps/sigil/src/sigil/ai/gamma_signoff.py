from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .gamma_claude_production_status import GammaClaudeProductionStatus
from .gamma_release_review import (
    GammaReleaseReview,
    GammaReleaseReviewState,
)
from .registry import canonical_digest

GAMMA_SIGNOFF_VERSION = 1


class GammaSignoffState(str, Enum):
    READY_FOR_PROMOTION_DECISION = "ready_for_promotion_decision"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class GammaSignoffRecord:
    signoff_id: str
    release_review_id: str
    release_review_digest: str
    golden_master_revision: str
    gamma_revision: str
    gamma_tag: str
    claude_subsystem_status: GammaClaudeProductionStatus
    claude_production_integrated: bool
    claude_production_enabled: bool
    state: GammaSignoffState
    blockers: tuple[str, ...]
    human_promotion_decision_required: bool
    signed_at: str
    signoff_digest: str
    release_authorized: bool = False
    promotion_authorized: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.signoff_id or not self.release_review_id:
            raise ValueError("Gamma sign-off identities cannot be blank")
        if not self.release_review_digest.startswith("sha256:"):
            raise ValueError("release review digest must be SHA-256")
        if not self.signoff_digest.startswith("sha256:"):
            raise ValueError("Gamma sign-off digest must be SHA-256")
        if not self.golden_master_revision or not self.gamma_revision:
            raise ValueError("Gamma sign-off revisions cannot be blank")
        if not self.gamma_tag.startswith("sigil-gamma-"):
            raise ValueError("Gamma sign-off tag is invalid")
        if self.claude_subsystem_status not in set(GammaClaudeProductionStatus):
            raise ValueError("Gamma sign-off Claude subsystem status is malformed")
        if self.claude_production_enabled and not self.claude_production_integrated:
            raise ValueError(
                "Gamma sign-off cannot claim Claude is production-enabled "
                "without also being production-integrated"
            )
        if not self.signed_at:
            raise ValueError("Gamma sign-off timestamp cannot be blank")
        if self.human_promotion_decision_required is not True:
            raise ValueError("Gamma sign-off requires a human promotion decision")
        if self.state == GammaSignoffState.READY_FOR_PROMOTION_DECISION:
            if self.blockers:
                raise ValueError("ready Gamma sign-off cannot contain blockers")
        elif not self.blockers:
            raise ValueError("blocked Gamma sign-off requires blockers")
        if (
            self.release_authorized is not False
            or self.promotion_authorized is not False
            or self.approval_authority is not False
            or self.execution_authorized is not False
            or self.broker_submission is not False
            or self.portfolio_mutation is not False
            or self.tool_execution is not False
            or self.paper_only is not True
        ):
            raise ValueError("Gamma sign-off record cannot receive authority")


def build_gamma_signoff(
    review: GammaReleaseReview,
    *,
    gamma_tag: str,
    signed_at: str,
) -> GammaSignoffRecord:
    blockers = tuple(review.blockers)
    if review.state != GammaReleaseReviewState.READY_FOR_HUMAN_DECISION:
        blockers = tuple(sorted(set(blockers + ("release_review_not_ready",))))

    state = (
        GammaSignoffState.READY_FOR_PROMOTION_DECISION
        if not blockers
        else GammaSignoffState.BLOCKED
    )

    signoff_id = (
        "gamma-signoff-"
        + canonical_digest(
            {
                "version": GAMMA_SIGNOFF_VERSION,
                "release_review_id": review.review_id,
                "release_review_digest": review.review_digest,
                "golden_master_revision": review.golden_master_revision,
                "gamma_revision": review.gamma_revision,
                "gamma_tag": gamma_tag,
                "blockers": blockers,
            }
        )
    )

    payload = {
        "version": GAMMA_SIGNOFF_VERSION,
        "signoff_id": signoff_id,
        "release_review_id": review.review_id,
        "release_review_digest": review.review_digest,
        "golden_master_revision": review.golden_master_revision,
        "gamma_revision": review.gamma_revision,
        "gamma_tag": gamma_tag,
        "claude_subsystem_status": review.claude_subsystem_status.value,
        "claude_production_integrated": review.claude_production_integrated,
        "claude_production_enabled": review.claude_production_enabled,
        "state": state.value,
        "blockers": blockers,
        "human_promotion_decision_required": True,
        "signed_at": signed_at,
        "release_authorized": False,
        "promotion_authorized": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }

    return GammaSignoffRecord(
        signoff_id=signoff_id,
        release_review_id=review.review_id,
        release_review_digest=review.review_digest,
        golden_master_revision=review.golden_master_revision,
        gamma_revision=review.gamma_revision,
        gamma_tag=gamma_tag,
        claude_subsystem_status=review.claude_subsystem_status,
        claude_production_integrated=review.claude_production_integrated,
        claude_production_enabled=review.claude_production_enabled,
        state=state,
        blockers=blockers,
        human_promotion_decision_required=True,
        signed_at=signed_at,
        signoff_digest=f"sha256:{canonical_digest(payload)}",
    )


def gamma_signoff_projection(
    signoff: GammaSignoffRecord,
) -> dict[str, object]:
    payload = asdict(signoff)
    payload["state"] = signoff.state.value
    payload["claude_subsystem_status"] = signoff.claude_subsystem_status.value
    return payload
