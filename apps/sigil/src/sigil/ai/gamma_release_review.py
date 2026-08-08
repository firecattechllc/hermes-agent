from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .gamma_release_readiness import GammaReleaseReadinessManifest
from .registry import canonical_digest

GAMMA_RELEASE_REVIEW_VERSION = 1


class GammaReleaseReviewState(str, Enum):
    READY_FOR_HUMAN_DECISION = "ready_for_human_decision"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class GammaReleaseReview:
    review_id: str
    manifest_id: str
    manifest_digest: str
    golden_master_revision: str
    gamma_revision: str
    state: GammaReleaseReviewState
    blockers: tuple[str, ...]
    reviewed_guarantees: tuple[str, ...]
    human_decision_required: bool
    reviewed_at: str
    review_digest: str
    release_authorized: bool = False
    promotion_authorized: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.review_id or not self.manifest_id:
            raise ValueError("release review identities cannot be blank")
        if not self.manifest_digest.startswith("sha256:"):
            raise ValueError("release review manifest digest must be SHA-256")
        if not self.review_digest.startswith("sha256:"):
            raise ValueError("release review digest must be SHA-256")
        if not self.reviewed_guarantees:
            raise ValueError("release review requires evaluated guarantees")
        if tuple(sorted(self.reviewed_guarantees)) != self.reviewed_guarantees:
            raise ValueError("reviewed guarantees must be sorted")
        if not self.reviewed_at:
            raise ValueError("release review timestamp cannot be blank")
        if self.human_decision_required is not True:
            raise ValueError("release review always requires a human decision")
        if self.state == GammaReleaseReviewState.READY_FOR_HUMAN_DECISION:
            if self.blockers:
                raise ValueError("ready release review cannot contain blockers")
        elif not self.blockers:
            raise ValueError("blocked release review requires blockers")
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
            raise ValueError("Gamma release review cannot receive authority")


def review_gamma_release_readiness(
    manifest: GammaReleaseReadinessManifest,
    *,
    reviewed_at: str,
) -> GammaReleaseReview:
    guarantees = tuple(
        sorted(
            {
                "all_required_stages_present",
                "broker_submission_disabled",
                "claude_advisory_only",
                "corruption_fail_closed_certified",
                "deterministic_replay_certified",
                "evidence_chain_ordered",
                "external_provider_explicit_admission",
                "golden_master_preserved",
                "human_release_review_required",
                "paper_only_preserved",
                "release_ready_for_review",
            }
        )
    )

    blockers = tuple(
        sorted(
            name
            for name in guarantees
            if getattr(manifest, name) is not True
        )
    )

    if manifest.release_authorized:
        blockers += ("manifest_attempted_release_authority",)
    if manifest.promotion_authorized:
        blockers += ("manifest_attempted_promotion_authority",)
    if manifest.execution_authorized:
        blockers += ("manifest_attempted_execution_authority",)
    if manifest.broker_submission:
        blockers += ("manifest_attempted_broker_submission",)

    blockers = tuple(sorted(set(blockers)))
    state = (
        GammaReleaseReviewState.READY_FOR_HUMAN_DECISION
        if not blockers
        else GammaReleaseReviewState.BLOCKED
    )

    review_id = (
        "gamma-release-review-"
        + canonical_digest(
            {
                "version": GAMMA_RELEASE_REVIEW_VERSION,
                "manifest_id": manifest.manifest_id,
                "manifest_digest": manifest.manifest_digest,
                "golden_master_revision": manifest.golden_master_revision,
                "gamma_revision": manifest.gamma_revision,
                "reviewed_guarantees": guarantees,
                "blockers": blockers,
            }
        )
    )

    payload = {
        "version": GAMMA_RELEASE_REVIEW_VERSION,
        "review_id": review_id,
        "manifest_id": manifest.manifest_id,
        "manifest_digest": manifest.manifest_digest,
        "golden_master_revision": manifest.golden_master_revision,
        "gamma_revision": manifest.gamma_revision,
        "state": state.value,
        "blockers": blockers,
        "reviewed_guarantees": guarantees,
        "human_decision_required": True,
        "reviewed_at": reviewed_at,
        "release_authorized": False,
        "promotion_authorized": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }

    return GammaReleaseReview(
        review_id=review_id,
        manifest_id=manifest.manifest_id,
        manifest_digest=manifest.manifest_digest,
        golden_master_revision=manifest.golden_master_revision,
        gamma_revision=manifest.gamma_revision,
        state=state,
        blockers=blockers,
        reviewed_guarantees=guarantees,
        human_decision_required=True,
        reviewed_at=reviewed_at,
        review_digest=f"sha256:{canonical_digest(payload)}",
    )


def gamma_release_review_projection(
    review: GammaReleaseReview,
) -> dict[str, object]:
    payload = asdict(review)
    payload["state"] = review.state.value
    return payload
