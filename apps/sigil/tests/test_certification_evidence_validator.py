"""Tests for the deterministic, fail-closed certification evidence validator.

See sigil.certification.evidence and
scripts/verify_certification_evidence.py. These tests cover the required
classification/validation matrix (approved / approved-with-findings /
not-approved / execution-error / missing / malformed / revision-mismatch /
digest-mismatch / Home-Assistant-cannot-satisfy-fleet-failover), plus
regression coverage against the real committed evidence artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sigil.certification.evidence import (
    CERTIFYING_STATUSES,
    CertificationEvidenceError,
    CertificationEvidenceStatus,
    parse_evidence_text,
    parse_metadata,
    validate_certification_evidence,
    validate_fleet_failover_evidence_source,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_REVIEW_ARTIFACT = (
    REPOSITORY_ROOT
    / "certification"
    / "claude-review"
    / "sigil-v3.6-independent-review-20260803T042800Z.md"
)
GOLDEN_MASTER_DOC = (
    REPOSITORY_ROOT / "docs" / "certification" / "sigil-golden-master-v3.5.0-post-gamma.md"
)
FLEET_FAILOVER_DOC = (
    REPOSITORY_ROOT / "docs" / "certification" / "sigil-fleet-failover-certification.md"
)


def _artifact(text: str, path: Path = Path("synthetic.md")):
    return parse_evidence_text(path, text)


APPROVED_TEXT = """
## Review status

- Status: `review_approved`
- Certifying: `true`
- Reviewed commit: `abc1234`
"""

APPROVED_WITH_FINDINGS_TEXT = """
## Review status

- Status: `review_approved_with_non_blocking_findings`
- Certifying: `true`
- Reviewed commit: `abc1234`
"""

NOT_APPROVED_TEXT = """
## Review status

- Status: `review_not_approved`
- Certifying: `false`
- Reviewed commit: `abc1234`
"""

EXECUTION_ERROR_TEXT = """
## Review status

- Status: `execution_error`
- Certifying: `false`
- Reviewed commit: `abc1234`
"""


class TestClassification:
    @pytest.mark.parametrize(
        "text,expected",
        [
            (APPROVED_TEXT, CertificationEvidenceStatus.REVIEW_APPROVED),
            (
                APPROVED_WITH_FINDINGS_TEXT,
                CertificationEvidenceStatus.REVIEW_APPROVED_WITH_FINDINGS,
            ),
            (NOT_APPROVED_TEXT, CertificationEvidenceStatus.REVIEW_NOT_APPROVED),
            (EXECUTION_ERROR_TEXT, CertificationEvidenceStatus.EXECUTION_ERROR),
        ],
    )
    def test_status_classification(self, text: str, expected: CertificationEvidenceStatus) -> None:
        assert _artifact(text).status == expected

    def test_review_requested_and_executed_are_not_certifying(self) -> None:
        for token in ("review_requested", "review_executed", "review_succeeded"):
            text = f"- Status: `{token}`\n- Certifying: `false`\n"
            artifact = _artifact(text)
            assert artifact.status.value == token
            assert not artifact.certifying

    def test_blocked_not_tested_unknown_are_not_certifying(self) -> None:
        for token in ("blocked", "not_tested", "unknown", "failed"):
            text = f"- Status: `{token}`\n- Certifying: `false`\n"
            assert not _artifact(text).certifying

    def test_missing_file_classified_missing(self, tmp_path: Path) -> None:
        from sigil.certification.evidence import load_evidence_artifact

        artifact = load_evidence_artifact(tmp_path / "does-not-exist.md")
        assert artifact.status == CertificationEvidenceStatus.MISSING
        assert not artifact.certifying

    def test_empty_content_classified_missing(self) -> None:
        assert _artifact("   \n\n  ").status == CertificationEvidenceStatus.MISSING

    def test_no_status_field_classified_malformed(self) -> None:
        text = "# Some review\n\nNo status field here at all.\n"
        assert _artifact(text).status == CertificationEvidenceStatus.MALFORMED

    def test_unrecognized_status_token_classified_malformed(self) -> None:
        text = "- Status: `totally_made_up_status`\n"
        assert _artifact(text).status == CertificationEvidenceStatus.MALFORMED

    def test_self_contradictory_certifying_claim_classified_malformed(self) -> None:
        # Status says failure, but the artifact *claims* to be certifying --
        # must not be trusted either way.
        text = "- Status: `execution_error`\n- Certifying: `true`\n"
        assert _artifact(text).status == CertificationEvidenceStatus.MALFORMED

    def test_certifying_statuses_are_exactly_the_two_approved_variants(self) -> None:
        assert CERTIFYING_STATUSES == {
            CertificationEvidenceStatus.REVIEW_APPROVED,
            CertificationEvidenceStatus.REVIEW_APPROVED_WITH_FINDINGS,
        }


class TestValidateCertificationEvidence:
    def test_approved_evidence_passes(self) -> None:
        validate_certification_evidence(_artifact(APPROVED_TEXT))

    def test_approved_with_non_blocking_findings_passes(self) -> None:
        validate_certification_evidence(_artifact(APPROVED_WITH_FINDINGS_TEXT))

    def test_not_approved_evidence_fails(self) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(_artifact(NOT_APPROVED_TEXT))

    def test_execution_error_evidence_fails(self) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(_artifact(EXECUTION_ERROR_TEXT))

    def test_missing_evidence_fails(self, tmp_path: Path) -> None:
        from sigil.certification.evidence import load_evidence_artifact

        artifact = load_evidence_artifact(tmp_path / "missing.md")
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(artifact)

    def test_empty_evidence_fails(self) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(_artifact(""))

    def test_malformed_evidence_fails(self) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(_artifact("no status field"))

    def test_wrong_revision_fails(self) -> None:
        artifact = _artifact(APPROVED_TEXT)  # reviewed_commit = abc1234
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(artifact, expected_revision="deadbee")

    def test_matching_revision_passes(self) -> None:
        artifact = _artifact(APPROVED_TEXT)
        validate_certification_evidence(artifact, expected_revision="abc1234")

    def test_tampered_digest_fails(self) -> None:
        text = APPROVED_TEXT + "- Content digest: `sha256:" + "a" * 64 + "`\n"
        artifact = _artifact(text)
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(
                artifact, expected_digest="sha256:" + "b" * 64
            )

    def test_matching_digest_passes(self) -> None:
        digest = "sha256:" + "a" * 64
        text = APPROVED_TEXT + f"- Content digest: `{digest}`\n"
        artifact = _artifact(text)
        validate_certification_evidence(artifact, expected_digest=digest)

    def test_required_digest_missing_from_artifact_fails(self) -> None:
        artifact = _artifact(APPROVED_TEXT)  # no digest line at all
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(
                artifact, expected_digest="sha256:" + "a" * 64
            )


class TestFleetFailoverEvidenceSource:
    def test_home_assistant_suite_rejected(self) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_fleet_failover_evidence_source("tests/integration/test_ha_integration.py")

    @pytest.mark.parametrize(
        "identity",
        [
            "HomeAssistant adapter tests",
            "home_assistant integration",
            "Home Assistant smoke test",
        ],
    )
    def test_home_assistant_markers_rejected_case_insensitively(self, identity: str) -> None:
        with pytest.raises(CertificationEvidenceError):
            validate_fleet_failover_evidence_source(identity)

    def test_genuine_fleet_failover_suite_identity_is_accepted(self) -> None:
        # Does not raise -- a real fleet failover suite name has nothing to
        # do with Home Assistant.
        validate_fleet_failover_evidence_source("apps/sigil/tests/test_fleet_failover.py")


class TestMetadataParsing:
    def test_parses_backtick_and_plain_values(self) -> None:
        text = "- Branch: `main`\n- Claude exit code: 0\n- Reviewed commit: `abc123`\n"
        metadata = parse_metadata(text)
        assert metadata["branch"] == "main"
        assert metadata["claude_exit_code"] == "0"
        assert metadata["reviewed_commit"] == "abc123"

    def test_ignores_non_metadata_lines(self) -> None:
        text = "# Heading\n\nSome prose that is not a metadata line.\n- Status: `blocked`\n"
        metadata = parse_metadata(text)
        assert metadata == {"status": "blocked"}


class TestRealCommittedArtifacts:
    """Regression coverage against the actual repository-committed evidence."""

    def test_claude_review_artifact_exists(self) -> None:
        assert CLAUDE_REVIEW_ARTIFACT.is_file()

    def test_claude_review_artifact_is_classified_execution_error(self) -> None:
        from sigil.certification.evidence import load_evidence_artifact

        artifact = load_evidence_artifact(CLAUDE_REVIEW_ARTIFACT)
        assert artifact.status == CertificationEvidenceStatus.EXECUTION_ERROR
        assert not artifact.certifying

    def test_claude_review_artifact_is_not_well_formed_enough_to_pass_validation(self) -> None:
        from sigil.certification.evidence import load_evidence_artifact

        artifact = load_evidence_artifact(CLAUDE_REVIEW_ARTIFACT)
        with pytest.raises(CertificationEvidenceError):
            validate_certification_evidence(artifact)

    def test_golden_master_doc_declares_fleet_failover_status(self) -> None:
        metadata = parse_metadata(GOLDEN_MASTER_DOC.read_text(encoding="utf-8"))
        assert metadata.get("fleet_failover_status") == "missing_evidence"

    def test_golden_master_doc_no_longer_conflates_ha_with_fleet_failover(self) -> None:
        text = GOLDEN_MASTER_DOC.read_text(encoding="utf-8").lower()
        assert "home assistant" in text
        assert "not, and was never, evidence of sigil fleet high-availability" in text

    def test_fleet_failover_placeholder_is_explicit_missing_evidence(self) -> None:
        from sigil.certification.evidence import load_evidence_artifact

        artifact = load_evidence_artifact(FLEET_FAILOVER_DOC)
        assert artifact.status == CertificationEvidenceStatus.NOT_TESTED
        assert not artifact.certifying

    def test_ha_integration_test_file_is_actually_home_assistant(self) -> None:
        ha_test_path = REPOSITORY_ROOT / "tests" / "integration" / "test_ha_integration.py"
        text = ha_test_path.read_text(encoding="utf-8")
        assert "Home Assistant" in text.splitlines()[0]
