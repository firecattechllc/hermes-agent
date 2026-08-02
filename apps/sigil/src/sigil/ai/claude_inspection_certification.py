from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from .claude_inspection import ClaudeInspectionReport
from .registry import canonical_digest

CLAUDE_INSPECTION_CERTIFICATION_VERSION = 1


class ClaudeInspectionCertificationState(str, Enum):
    ADVISORY_PASS = "advisory_pass"
    REVIEW_REQUIRED = "review_required"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ClaudeInspectionCertification:
    certification_id: str
    inspection_id: str
    target_revision: str
    target_digest: str
    inspection_report_digest: str
    state: ClaudeInspectionCertificationState
    finding_count: int
    critical_finding_count: int
    high_finding_count: int
    failed_inspection: bool
    certified_at: str
    certification_digest: str
    human_review_required: bool
    promotion_authorized: bool = False
    release_authority: bool = False
    approval_authority: bool = False
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    tool_execution: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.certification_id or not self.inspection_id:
            raise ValueError("certification identities cannot be blank")
        if not self.target_revision:
            raise ValueError("certification target revision cannot be blank")
        for value, label in (
            (self.target_digest, "certification target digest"),
            (self.inspection_report_digest, "inspection report digest"),
            (self.certification_digest, "certification digest"),
        ):
            if not value.startswith("sha256:"):
                raise ValueError(f"{label} must be SHA-256")
        if min(
            self.finding_count,
            self.critical_finding_count,
            self.high_finding_count,
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
                "Claude inspection certification cannot receive authority"
            )


def certify_claude_inspection(
    report: ClaudeInspectionReport,
    *,
    certified_at: str,
) -> ClaudeInspectionCertification:
    critical_count = sum(
        finding.severity == "critical" for finding in report.findings
    )
    high_count = sum(finding.severity == "high" for finding in report.findings)
    failed = not report.succeeded

    if failed:
        state = ClaudeInspectionCertificationState.INVALID
    elif critical_count or high_count:
        state = ClaudeInspectionCertificationState.REVIEW_REQUIRED
    else:
        state = ClaudeInspectionCertificationState.ADVISORY_PASS

    human_review_required = (
        state != ClaudeInspectionCertificationState.ADVISORY_PASS
    )
    certification_id = (
        "claude-inspection-certification-"
        + canonical_digest(
            {
                "inspection_id": report.inspection_id,
                "target_revision": report.target_revision,
                "target_digest": report.target_digest,
                "inspection_report_digest": report.report_digest,
            }
        )
    )
    payload = {
        "version": CLAUDE_INSPECTION_CERTIFICATION_VERSION,
        "certification_id": certification_id,
        "inspection_id": report.inspection_id,
        "target_revision": report.target_revision,
        "target_digest": report.target_digest,
        "inspection_report_digest": report.report_digest,
        "state": state.value,
        "finding_count": len(report.findings),
        "critical_finding_count": critical_count,
        "high_finding_count": high_count,
        "failed_inspection": failed,
        "certified_at": certified_at,
        "human_review_required": human_review_required,
        "promotion_authorized": False,
        "release_authority": False,
        "approval_authority": False,
        "execution_authorized": False,
        "broker_submission": False,
        "portfolio_mutation": False,
        "tool_execution": False,
        "paper_only": True,
    }
    return ClaudeInspectionCertification(
        certification_id=certification_id,
        inspection_id=report.inspection_id,
        target_revision=report.target_revision,
        target_digest=report.target_digest,
        inspection_report_digest=report.report_digest,
        state=state,
        finding_count=len(report.findings),
        critical_finding_count=critical_count,
        high_finding_count=high_count,
        failed_inspection=failed,
        certified_at=certified_at,
        certification_digest=f"sha256:{canonical_digest(payload)}",
        human_review_required=human_review_required,
    )


def certification_manifest(
    certification: ClaudeInspectionCertification,
) -> dict[str, object]:
    payload = asdict(certification)
    payload["state"] = certification.state.value
    return payload
