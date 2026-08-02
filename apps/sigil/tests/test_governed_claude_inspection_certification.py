from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.ai import (
    CLAUDE_PROVIDER_ID,
    ClaudeInspectionCertificationState,
    ClaudeInspectionFailure,
    ClaudeInspectionFinding,
    ClaudeInspectionReport,
    certification_manifest,
    certify_claude_inspection,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
REPORT_DIGEST = "sha256:" + "c" * 64
NOW = "2026-08-02T21:15:00Z"


def report(
    *,
    severities: tuple[str, ...] = (),
    failure: ClaudeInspectionFailure | None = None,
) -> ClaudeInspectionReport:
    findings = tuple(
        ClaudeInspectionFinding(
            finding_id=f"finding-{index:03d}",
            severity=severity,
            category="routing",
            summary=f"{severity} advisory finding",
            evidence_references=(DIGEST_B,),
            recommendation="Human review should consider this finding.",
        )
        for index, severity in enumerate(severities, 1)
    )
    return ClaudeInspectionReport(
        inspection_id="inspection-001",
        target_revision="62d070d35",
        target_digest=DIGEST_A,
        provider_id=CLAUDE_PROVIDER_ID,
        model_id="claude-sonnet-governed",
        findings=findings,
        limitations=("Bounded sanitized evidence only.",),
        report_digest=REPORT_DIGEST,
        completed_at=NOW,
        failure=failure,
    )


def test_clean_inspection_produces_advisory_pass_without_authority() -> None:
    certification = certify_claude_inspection(
        report(severities=("info", "medium")),
        certified_at=NOW,
    )
    assert certification.state == ClaudeInspectionCertificationState.ADVISORY_PASS
    assert certification.human_review_required is False
    assert certification.promotion_authorized is False
    assert certification.release_authority is False
    assert certification.approval_authority is False
    assert certification.execution_authorized is False
    assert certification.broker_submission is False
    assert certification.portfolio_mutation is False
    assert certification.tool_execution is False
    assert certification.paper_only is True


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_high_severity_findings_require_human_review(severity: str) -> None:
    certification = certify_claude_inspection(
        report(severities=(severity,)),
        certified_at=NOW,
    )
    assert certification.state == ClaudeInspectionCertificationState.REVIEW_REQUIRED
    assert certification.human_review_required is True
    assert certification.promotion_authorized is False
    assert certification.high_finding_count == int(severity == "high")
    assert certification.critical_finding_count == int(severity == "critical")


def test_failed_inspection_is_invalid_and_cannot_promote() -> None:
    certification = certify_claude_inspection(
        report(failure=ClaudeInspectionFailure.PROVIDER_UNAVAILABLE),
        certified_at=NOW,
    )
    assert certification.state == ClaudeInspectionCertificationState.INVALID
    assert certification.failed_inspection is True
    assert certification.human_review_required is True
    assert certification.promotion_authorized is False
    assert certification.release_authority is False


def test_certification_is_deterministic_and_report_linked() -> None:
    inspection = report(severities=("low",))
    first = certify_claude_inspection(inspection, certified_at=NOW)
    second = certify_claude_inspection(inspection, certified_at=NOW)
    assert first == second
    assert first.inspection_report_digest == REPORT_DIGEST
    assert first.target_revision == "62d070d35"
    assert first.target_digest == DIGEST_A
    assert first.certification_id.startswith("claude-inspection-certification-")
    assert first.certification_digest.startswith("sha256:")


def test_manifest_is_sanitized_and_replayable() -> None:
    certification = certify_claude_inspection(
        report(severities=("medium",)),
        certified_at=NOW,
    )
    manifest = certification_manifest(certification)
    assert manifest["state"] == "advisory_pass"
    assert manifest["inspection_report_digest"] == REPORT_DIGEST
    assert manifest["promotion_authorized"] is False
    assert manifest["release_authority"] is False
    assert "summary" not in manifest
    assert "recommendation" not in manifest
    assert "limitations" not in manifest


@pytest.mark.parametrize(
    "field,value",
    [
        ("promotion_authorized", True),
        ("release_authority", True),
        ("approval_authority", True),
        ("execution_authorized", True),
        ("broker_submission", True),
        ("portfolio_mutation", True),
        ("tool_execution", True),
        ("paper_only", False),
    ],
)
def test_certification_authority_fields_fail_closed(field: str, value: bool) -> None:
    certification = certify_claude_inspection(report(), certified_at=NOW)
    with pytest.raises(ValueError, match="cannot receive authority"):
        replace(certification, **{field: value})
