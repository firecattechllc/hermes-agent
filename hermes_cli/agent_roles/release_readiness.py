"""Immutable Step 29 release preparation models; never releases or deploys."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .system_integration_certification import CertificationFinding, FindingSeverity, SystemIntegrationCertification


RELEASE_READINESS_SCHEMA_VERSION = 1


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class ReleaseDisposition(str, Enum):
    READY_FOR_OPERATOR_REVIEW = "ready_for_operator_review"
    CONDITIONALLY_READY = "conditionally_ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class RollbackReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    known_good_source_commit: str
    rollback_reference: str
    schema_compatibility_notes: Tuple[str, ...]
    persistence_compatibility_notes: Tuple[str, ...]
    configuration_restoration_requirements: Tuple[str, ...]
    provider_disable_strategy: str
    service_stop_strategy: str
    evidence_preservation_requirements: Tuple[str, ...]
    operator_decision_points: Tuple[str, ...]


class ReleaseManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    release_candidate_id: str
    source_branch: str
    source_commit: str
    included_milestone_range: str = "Steps 1-29"
    required_migrations: Tuple[str, ...]
    public_interfaces: Tuple[str, ...]
    event_types: Tuple[str, ...]
    evidence_schema_versions: Tuple[str, ...]
    rollback_prerequisites: Tuple[str, ...]
    configuration_prerequisites: Tuple[str, ...]
    known_limitations: Tuple[str, ...]
    unresolved_risks: Tuple[str, ...]
    validation_results: Tuple[str, ...]
    artifact_references: Tuple[str, ...]
    operator_approvals_still_required: Tuple[str, ...]
    manifest_id: str

    @model_validator(mode="after")
    def _identity(self) -> "ReleaseManifest":
        expected = f"release_manifest_{_digest(self.model_dump(mode='json', exclude={'manifest_id'}))[:24]}"
        if self.manifest_id != expected:
            raise ValueError("release manifest identity mismatch")
        return self


class ReleaseReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = RELEASE_READINESS_SCHEMA_VERSION
    certification_id: str
    source_commit: str
    branch: str
    repository_clean: bool
    required_suites: Tuple[str, ...]
    passed_suites: Tuple[str, ...]
    failed_suites: Tuple[str, ...]
    skipped_suites: Tuple[str, ...]
    governance_invariants_passed: bool
    evidence_chain_status: str
    persistence_status: str
    mission_control_status: str
    security_posture: str
    unresolved_risks: Tuple[str, ...]
    blocking_findings: Tuple[CertificationFinding, ...]
    advisory_findings: Tuple[CertificationFinding, ...]
    disposition: ReleaseDisposition
    operator_approval_required: bool = True
    generated_at: int = Field(..., ge=0)
    report_id: str
    manifest: ReleaseManifest
    rollback: RollbackReadiness

    @model_validator(mode="after")
    def _valid(self) -> "ReleaseReadiness":
        if not self.operator_approval_required:
            raise ValueError("release readiness can never remove operator approval")
        groups = (self.required_suites, self.passed_suites, self.failed_suites, self.skipped_suites)
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("suite accounting contains duplicates")
        accounted = self.passed_suites + self.failed_suites + self.skipped_suites
        if len(accounted) != len(set(accounted)) or set(accounted) != set(self.required_suites):
            raise ValueError("suite accounting is incomplete")
        certification_blocked = not (
            self.governance_invariants_passed
            and self.evidence_chain_status == "certified"
            and self.persistence_status == "certified"
            and self.mission_control_status == "certified"
        )
        expected = ReleaseDisposition.FAILED if any(item.severity is FindingSeverity.CRITICAL for item in self.blocking_findings) else (ReleaseDisposition.BLOCKED if self.blocking_findings or self.failed_suites or not self.repository_clean or certification_blocked else (ReleaseDisposition.CONDITIONALLY_READY if self.advisory_findings or self.unresolved_risks or self.skipped_suites else ReleaseDisposition.READY_FOR_OPERATOR_REVIEW))
        if self.disposition is not expected:
            raise ValueError("release disposition mismatch")
        digest = _digest(self.model_dump(mode="json", exclude={"report_id"}))
        if self.report_id != f"release_readiness_{digest[:24]}":
            raise ValueError("release readiness report identity mismatch")
        return self


def build_release_readiness(certification: SystemIntegrationCertification, *, repository_clean: bool, required_suites: Iterable[str], passed_suites: Iterable[str], failed_suites: Iterable[str] = (), skipped_suites: Iterable[str] = (), generated_at: int = 0, known_good_source_commit: str = "14272ebb1") -> ReleaseReadiness:
    required = tuple(sorted(required_suites)); passed = tuple(sorted(passed_suites)); failed = tuple(sorted(failed_suites)); skipped = tuple(sorted(skipped_suites))
    groups = (required, passed, failed, skipped)
    if any(len(group) != len(set(group)) for group in groups):
        raise ValueError("suite accounting contains duplicates")
    accounted = passed + failed + skipped
    if len(accounted) != len(set(accounted)) or set(accounted) != set(required):
        raise ValueError("every required suite must be accounted for exactly once")
    findings = certification.findings
    blocking = tuple(item for item in findings if item.severity in {FindingSeverity.BLOCKING, FindingSeverity.CRITICAL})
    advisory = tuple(item for item in findings if item.severity in {FindingSeverity.ADVISORY, FindingSeverity.WARNING})
    risks = tuple(item.summary for item in advisory)
    certification_blocked = certification.status.value != "certified"
    disposition = ReleaseDisposition.FAILED if any(item.severity is FindingSeverity.CRITICAL for item in blocking) else (ReleaseDisposition.BLOCKED if blocking or failed or not repository_clean or certification_blocked else (ReleaseDisposition.CONDITIONALLY_READY if advisory or risks or skipped else ReleaseDisposition.READY_FOR_OPERATOR_REVIEW))
    rollback = RollbackReadiness(known_good_source_commit=known_good_source_commit, rollback_reference=f"git://commit/{known_good_source_commit}", schema_compatibility_notes=("Step 29 adds schema-versioned evidence only",), persistence_compatibility_notes=("Preserve append-only journals before rollback",), configuration_restoration_requirements=("Restore operator-reviewed configuration",), provider_disable_strategy="disable provider adapters before service restart", service_stop_strategy="operator stops affected services before rollback", evidence_preservation_requirements=("retain certification and Mission Control journals",), operator_decision_points=("authorize rollback", "authorize service restoration"))
    manifest_values = dict(release_candidate_id=f"hermes-step29-{certification.source_commit[:12]}", source_branch=certification.branch, source_commit=certification.source_commit, included_milestone_range="Steps 1-29", required_migrations=(), public_interfaces=("hermes_cli.agent_roles",), event_types=("evidence_chain_certified", "release_readiness_blocked", "release_readiness_recorded", "rollback_readiness_recorded", "system_integration_certification_blocked", "system_integration_certification_recorded", "system_integration_certification_started"), evidence_schema_versions=("system_integration_certification:1", "release_readiness:1"), rollback_prerequisites=("operator authorization", "known-good commit available", "evidence preserved"), configuration_prerequisites=("providers remain disabled for deterministic certification",), known_limitations=("provider execution is simulated through deterministic adapters", "release execution is out of scope"), unresolved_risks=risks, validation_results=tuple(f"passed:{name}" for name in passed) + tuple(f"failed:{name}" for name in failed) + tuple(f"skipped:{name}" for name in skipped), artifact_references=(f"certification://system/{certification.certification_id}",), operator_approvals_still_required=("merge", "tag", "release", "deployment", "rollback"))
    manifest = ReleaseManifest(**manifest_values, manifest_id=f"release_manifest_{_digest(manifest_values)[:24]}")
    values = dict(schema_version=RELEASE_READINESS_SCHEMA_VERSION, certification_id=certification.certification_id, source_commit=certification.source_commit, branch=certification.branch, repository_clean=repository_clean, required_suites=required, passed_suites=passed, failed_suites=failed, skipped_suites=skipped, governance_invariants_passed=all(item.passed for item in certification.governance_invariants), evidence_chain_status="certified", persistence_status="certified" if certification.persistence_certified else "blocked", mission_control_status="certified" if certification.mission_control_certified else "blocked", security_posture="sanitized_operator_gated", unresolved_risks=risks, blocking_findings=blocking, advisory_findings=advisory, disposition=disposition, operator_approval_required=True, generated_at=generated_at, manifest=manifest, rollback=rollback)
    provisional = ReleaseReadiness.model_construct(**values, report_id="")
    digest = _digest(provisional.model_dump(mode="json", exclude={"report_id"}))
    return ReleaseReadiness(**values, report_id=f"release_readiness_{digest[:24]}")
