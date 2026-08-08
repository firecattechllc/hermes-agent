from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .cross_provider_validation import (
    CrossProviderValidationReport,
    CrossProviderValidationState,
)
from .registry import canonical_digest

CROSS_PROVIDER_VALIDATION_CERTIFICATION_VERSION = 1


class CrossProviderValidationCertificationState(str, Enum):
    ADVISORY_PASS = "advisory_pass"
    REVIEW_REQUIRED = "review_required"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class CrossProviderValidationCertification:
    certification_id: str
    validation_id: str
    target_revision: str
    target_digest: str
    validation_digest: str
    state: CrossProviderValidationCertificationState
    agreement_count: int
    disagreement_count: int
    missing_coverage_count: int
    human_review_required: bool
    certified_at: str
    certification_digest: str
    promotion_authorized: bool = False
    release_authority: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.certification_id or not self.validation_id:
            raise ValueError("certification identities cannot be blank")
        if not self.target_revision:
            raise ValueError("certification target revision cannot be blank")
        for value, label in (
            (self.target_digest, "target digest"),
            (self.validation_digest, "validation digest"),
            (self.certification_digest, "certification digest"),
        ):
            if not value.startswith("sha256:"):
                raise ValueError(f"{label} must be SHA-256")
        if min(
            self.agreement_count,
            self.disagreement_count,
            self.missing_coverage_count,
        ) < 0:
            raise ValueError("certification counts cannot be negative")
        if not self.certified_at:
            raise ValueError("certification timestamp cannot be blank")
        if (
            self.promotion_authorized is not False
            or self.release_authority is not False
            or self.approval_authority is not False
            or self.execution_authorized is not False
            or self.broker_submission is not False
            or self.portfolio_mutation is not False
            or self.tool_execution is not False
            or self.paper_only is not True
        ):
            raise ValueError(
                "cross-provider validation certification cannot receive authority"
            )


def certify_cross_provider_validation(
    report: CrossProviderValidationReport,
    *,
    certified_at: str,
) -> CrossProviderValidationCertification:
    if report.state == CrossProviderValidationState.CONSISTENT:
        state = CrossProviderValidationCertificationState.ADVISORY_PASS
        human_review_required = False
    elif report.state in {
        CrossProviderValidationState.REVIEW_REQUIRED,
        CrossProviderValidationState.INSUFFICIENT_EVIDENCE,
    }:
        state = CrossProviderValidationCertificationState.REVIEW_REQUIRED
        human_review_required = True
    else:
        state = CrossProviderValidationCertificationState.INVALID
        human_review_required = True

    certification_id = (
        "cross-provider-validation-certification-"
        + canonical_digest(
            {
                "version": CROSS_PROVIDER_VALIDATION_CERTIFICATION_VERSION,
                "validation_id": report.validation_id,
                "target_revision": report.target_revision,
                "target_digest": report.target_digest,
                "validation_digest": report.validation_digest,
            }
        )
    )

    payload = {
        "version": CROSS_PROVIDER_VALIDATION_CERTIFICATION_VERSION,
        "certification_id": certification_id,
        "validation_id": report.validation_id,
        "target_revision": report.target_revision,
        "target_digest": report.target_digest,
        "validation_digest": report.validation_digest,
        "state": state.value,
        "agreement_count": report.agreement_count,
        "disagreement_count": report.disagreement_count,
        "missing_coverage_count": report.missing_coverage_count,
        "human_review_required": human_review_required,
        "certified_at": certified_at,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }

    return CrossProviderValidationCertification(
        certification_id=certification_id,
        validation_id=report.validation_id,
        target_revision=report.target_revision,
        target_digest=report.target_digest,
        validation_digest=report.validation_digest,
        state=state,
        agreement_count=report.agreement_count,
        disagreement_count=report.disagreement_count,
        missing_coverage_count=report.missing_coverage_count,
        human_review_required=human_review_required,
        certified_at=certified_at,
        certification_digest=f"sha256:{canonical_digest(payload)}",
    )


def cross_provider_certification_manifest(
    certification: CrossProviderValidationCertification,
) -> dict[str, object]:
    payload = asdict(certification)
    payload["state"] = certification.state.value
    return payload
