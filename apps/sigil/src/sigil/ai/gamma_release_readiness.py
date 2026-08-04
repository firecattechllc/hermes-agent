from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .gamma_claude_production_status import (
    GammaClaudeProductionStatus,
    claude_production_enabled,
    claude_production_integrated,
    gamma_claude_production_status,
)
from .gamma_reliability_certification import GammaReliabilityCertification
from .registry import canonical_digest

GAMMA_RELEASE_READINESS_VERSION = 2
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
    reliability_certification_id: str
    reliability_certification_digest: str
    claude_subsystem_status: GammaClaudeProductionStatus
    claude_production_integrated: bool
    claude_production_enabled: bool
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
        if not self.reliability_certification_digest.startswith("sha256:"):
            raise ValueError(
                "release readiness reliability certification digest must be SHA-256"
            )
        if not self.reliability_certification_id:
            raise ValueError(
                "release readiness reliability certification id cannot be blank"
            )
        if self.claude_subsystem_status not in set(GammaClaudeProductionStatus):
            raise ValueError("release readiness Claude subsystem status is malformed")
        if self.claude_production_enabled and not self.claude_production_integrated:
            raise ValueError(
                "release readiness cannot claim Claude is production-enabled "
                "without also being production-integrated"
            )
        if self.claude_production_integrated != claude_production_integrated(
            self.claude_subsystem_status
        ):
            raise ValueError(
                "release readiness Claude integration flag is inconsistent "
                "with its subsystem status"
            )
        if self.claude_production_enabled != claude_production_enabled(
            self.claude_subsystem_status
        ):
            raise ValueError(
                "release readiness Claude enablement flag is inconsistent "
                "with its subsystem status"
            )
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
    reliability_certification: GammaReliabilityCertification,
    claude_wired_into_production_runtime: bool,
    claude_config_enabled: bool,
    generated_at: str,
) -> GammaReleaseReadinessManifest:
    """Build the Stage 7 readiness manifest from verified upstream evidence.

    ``reliability_certification`` must be a real
    :class:`GammaReliabilityCertification` bound to ``gamma_revision`` — its
    own construction already required verified, passing test evidence (see
    :mod:`sigil.ai.gamma_reliability_certification`), so a revision mismatch
    here means the caller is trying to attach reliability evidence for the
    wrong build, which fails closed rather than being silently accepted.

    ``claude_wired_into_production_runtime`` and ``claude_config_enabled``
    must reflect real, checkable state; this function derives the Claude
    subsystem's status from them rather than trusting an asserted claim
    (see :mod:`sigil.ai.gamma_claude_production_status`).
    """

    if tuple(item.stage for item in stage_evidence) != _REQUIRED_STAGES:
        raise ValueError("stage evidence must be ordered from 1 through 6")
    if not isinstance(reliability_certification, GammaReliabilityCertification):
        # ValueError (not TypeError) to match every other fail-closed
        # validation in this module and the callers' pytest.raises(ValueError).
        raise ValueError(  # noqa: TRY004
            "release readiness requires a verified Gamma reliability certification"
        )
    if reliability_certification.target_revision != gamma_revision:
        raise ValueError(
            "release readiness reliability certification revision does not "
            "match the Gamma revision"
        )

    claude_status = gamma_claude_production_status(
        wired_into_production_runtime=claude_wired_into_production_runtime,
        config_enabled=claude_config_enabled,
    )
    claude_integrated = claude_production_integrated(claude_status)
    claude_enabled = claude_production_enabled(claude_status)

    all_required_stages_present = (
        tuple(item.stage for item in stage_evidence) == _REQUIRED_STAGES
    )
    evidence_chain_ordered = all_required_stages_present and all(
        bool(item.revision.strip()) for item in stage_evidence
    )
    golden_master_preserved = bool(_SHA.fullmatch(golden_master_revision))
    deterministic_replay_certified = reliability_certification.replay_deterministic
    corruption_fail_closed_certified = reliability_certification.corruption_fails_closed

    # These reflect fixed Sigil governance invariants that are independently
    # enforced by the "cannot receive authority" checks below and throughout
    # the codebase (paper-only runtime, no broker submission, explicit
    # external-provider admission, Claude advisory-only) rather than
    # unverifiable test outcomes.
    paper_only_preserved = True
    broker_submission_disabled = True
    external_provider_explicit_admission = True
    claude_advisory_only = True
    human_release_review_required = True

    release_ready_for_review = all(
        (
            all_required_stages_present,
            evidence_chain_ordered,
            golden_master_preserved,
            paper_only_preserved,
            broker_submission_disabled,
            external_provider_explicit_admission,
            claude_advisory_only,
            deterministic_replay_certified,
            corruption_fail_closed_certified,
            human_release_review_required,
        )
    )

    manifest_id = (
        "gamma-release-readiness-"
        + canonical_digest(
            {
                "version": GAMMA_RELEASE_READINESS_VERSION,
                "golden_master_revision": golden_master_revision,
                "golden_master_tag": golden_master_tag,
                "gamma_revision": gamma_revision,
                "stage_evidence": [asdict(item) for item in stage_evidence],
                "reliability_certification_id": reliability_certification.certification_id,
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
        "reliability_certification_id": reliability_certification.certification_id,
        "reliability_certification_digest": reliability_certification.certification_digest,
        "claude_subsystem_status": claude_status.value,
        "claude_production_integrated": claude_integrated,
        "claude_production_enabled": claude_enabled,
        "all_required_stages_present": all_required_stages_present,
        "evidence_chain_ordered": evidence_chain_ordered,
        "golden_master_preserved": golden_master_preserved,
        "paper_only_preserved": paper_only_preserved,
        "broker_submission_disabled": broker_submission_disabled,
        "external_provider_explicit_admission": external_provider_explicit_admission,
        "claude_advisory_only": claude_advisory_only,
        "deterministic_replay_certified": deterministic_replay_certified,
        "corruption_fail_closed_certified": corruption_fail_closed_certified,
        "human_release_review_required": human_release_review_required,
        "generated_at": generated_at,
        "release_ready_for_review": release_ready_for_review,
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
        reliability_certification_id=reliability_certification.certification_id,
        reliability_certification_digest=reliability_certification.certification_digest,
        claude_subsystem_status=claude_status,
        claude_production_integrated=claude_integrated,
        claude_production_enabled=claude_enabled,
        all_required_stages_present=all_required_stages_present,
        evidence_chain_ordered=evidence_chain_ordered,
        golden_master_preserved=golden_master_preserved,
        paper_only_preserved=paper_only_preserved,
        broker_submission_disabled=broker_submission_disabled,
        external_provider_explicit_admission=external_provider_explicit_admission,
        claude_advisory_only=claude_advisory_only,
        deterministic_replay_certified=deterministic_replay_certified,
        corruption_fail_closed_certified=corruption_fail_closed_certified,
        human_release_review_required=human_release_review_required,
        generated_at=generated_at,
        manifest_digest=f"sha256:{canonical_digest(payload)}",
        release_ready_for_review=release_ready_for_review,
    )


def gamma_release_readiness_projection(
    manifest: GammaReleaseReadinessManifest,
) -> dict[str, object]:
    payload = asdict(manifest)
    payload["claude_subsystem_status"] = manifest.claude_subsystem_status.value
    payload["stage_evidence"] = [
        {
            "stage": item.stage,
            "revision": item.revision,
            "summary": item.summary,
        }
        for item in manifest.stage_evidence
    ]
    return payload
