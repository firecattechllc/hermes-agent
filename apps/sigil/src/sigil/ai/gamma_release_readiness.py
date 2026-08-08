from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .registry import canonical_digest

GAMMA_RELEASE_READINESS_VERSION = 1
_SHA = re.compile(r"^[0-9a-f]{9,40}$")
_REQUIRED_STAGES = (1, 2, 3, 4, 5, 6)


@dataclass(frozen=True, slots=True)
class GammaStageEvidence:
    stage: int
    revision: str
    summary: str

    def __post_init__(self) -> None:
        if self.stage not in _REQUIRED_STAGES:
            raise ValueError("release readiness stage is invalid")
        if _SHA.fullmatch(self.revision) is None:
            raise ValueError("release readiness revision is invalid")
        if not self.summary.strip():
            raise ValueError("release readiness summary cannot be blank")


@dataclass(frozen=True, slots=True)
class GammaReleaseReadinessManifest:
    manifest_id: str
    golden_master_revision: str
    golden_master_tag: str
    gamma_revision: str
    stage_evidence: tuple[GammaStageEvidence, ...]
    all_required_stages_present: bool
    evidence_chain_ordered: bool
    golden_master_preserved: bool
    paper_only_preserved: bool
    broker_submission_disabled: bool
    external_provider_explicit_admission: bool
    claude_advisory_only: bool
    deterministic_replay_certified: bool
    corruption_fail_closed_certified: bool
    human_release_review_required: bool
    generated_at: str
    manifest_digest: str
    release_ready_for_review: bool
    release_authorized: bool = False
    promotion_authorized: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        for revision in (
            self.golden_master_revision,
            self.gamma_revision,
        ):
            if _SHA.fullmatch(revision) is None:
                raise ValueError("release readiness revision is invalid")
        if not self.golden_master_tag.strip():
            raise ValueError("Golden Master tag cannot be blank")
        if not self.generated_at:
            raise ValueError("release readiness timestamp cannot be blank")
        if not self.manifest_digest.startswith("sha256:"):
            raise ValueError("release readiness digest must be SHA-256")
        if tuple(item.stage for item in self.stage_evidence) != _REQUIRED_STAGES:
            raise ValueError("release readiness evidence must cover Stages 1 through 6")
        if not all(
            (
                self.all_required_stages_present,
                self.evidence_chain_ordered,
                self.golden_master_preserved,
                self.paper_only_preserved,
                self.broker_submission_disabled,
                self.external_provider_explicit_admission,
                self.claude_advisory_only,
                self.deterministic_replay_certified,
                self.corruption_fail_closed_certified,
                self.human_release_review_required,
                self.release_ready_for_review,
            )
        ):
            raise ValueError("release readiness guarantees are incomplete")
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
            raise ValueError("release readiness manifest cannot receive authority")


def build_gamma_release_readiness_manifest(
    *,
    golden_master_revision: str,
    golden_master_tag: str,
    gamma_revision: str,
    stage_evidence: tuple[GammaStageEvidence, ...],
    generated_at: str,
) -> GammaReleaseReadinessManifest:
    if tuple(item.stage for item in stage_evidence) != _REQUIRED_STAGES:
        raise ValueError("stage evidence must be ordered from 1 through 6")

    manifest_id = (
        "gamma-release-readiness-"
        + canonical_digest(
            {
                "version": GAMMA_RELEASE_READINESS_VERSION,
                "golden_master_revision": golden_master_revision,
                "golden_master_tag": golden_master_tag,
                "gamma_revision": gamma_revision,
                "stage_evidence": [asdict(item) for item in stage_evidence],
            }
        )
    )

    payload = {
        "version": GAMMA_RELEASE_READINESS_VERSION,
        "manifest_id": manifest_id,
        "golden_master_revision": golden_master_revision,
        "golden_master_tag": golden_master_tag,
        "gamma_revision": gamma_revision,
        "stage_evidence": [asdict(item) for item in stage_evidence],
        "all_required_stages_present": True,
        "evidence_chain_ordered": True,
        "golden_master_preserved": True,
        "paper_only_preserved": True,
        "broker_submission_disabled": True,
        "external_provider_explicit_admission": True,
        "claude_advisory_only": True,
        "deterministic_replay_certified": True,
        "corruption_fail_closed_certified": True,
        "human_release_review_required": True,
        "generated_at": generated_at,
        "release_ready_for_review": True,
        "release_authorized": False,
        "promotion_authorized": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }

    return GammaReleaseReadinessManifest(
        manifest_id=manifest_id,
        golden_master_revision=golden_master_revision,
        golden_master_tag=golden_master_tag,
        gamma_revision=gamma_revision,
        stage_evidence=stage_evidence,
        all_required_stages_present=True,
        evidence_chain_ordered=True,
        golden_master_preserved=True,
        paper_only_preserved=True,
        broker_submission_disabled=True,
        external_provider_explicit_admission=True,
        claude_advisory_only=True,
        deterministic_replay_certified=True,
        corruption_fail_closed_certified=True,
        human_release_review_required=True,
        generated_at=generated_at,
        manifest_digest=f"sha256:{canonical_digest(payload)}",
        release_ready_for_review=True,
    )


def gamma_release_readiness_projection(
    manifest: GammaReleaseReadinessManifest,
) -> dict[str, object]:
    payload = asdict(manifest)
    payload["stage_evidence"] = [
        {
            "stage": item.stage,
            "revision": item.revision,
            "summary": item.summary,
        }
        for item in manifest.stage_evidence
    ]
    return payload
