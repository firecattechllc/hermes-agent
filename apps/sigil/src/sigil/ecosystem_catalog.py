"""Disabled-by-default governed ecosystem discovery catalog.

Stage 8 models externally supplied discovery evidence and deterministically
evaluates integration metadata, compatibility, suitability, overlap, risk,
recommendation, and admission readiness.

This module performs no web crawling, network requests, repository cloning,
authentication, credential resolution, installation, activation, job dispatch,
filesystem access, shell execution, policy mutation, or financial action.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import (
    INTEGRATION_REGISTRY_SCHEMA_VERSION,
    AuthorityDenials,
    IntegrationCategory,
    IntegrationRegistryEntry,
    LifecycleState,
)

ECOSYSTEM_CATALOG_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REPOSITORY = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_PUBLIC_REPOSITORY_URL = re.compile(
    r"^https://(?:github\.com|gitlab\.com)/"
    r"([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+?)(?:\.git)?$"
)
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|"
    r"[A-Za-z]:\\Users\\)"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)


class EcosystemCatalogValidationError(ValueError):
    """Ecosystem catalog input failed closed."""


class DiscoverySourceKind(str, Enum):
    MANUAL_REVIEW = "manual_review"
    REPOSITORY_SNAPSHOT = "repository_snapshot"
    RELEASE_MANIFEST = "release_manifest"
    DOCUMENTATION_SNAPSHOT = "documentation_snapshot"
    SECURITY_REVIEW = "security_review"
    LICENSE_REVIEW = "license_review"
    ACTIVITY_REVIEW = "activity_review"


class CatalogRecommendation(str, Enum):
    HOLD = "hold"
    REVIEW = "review"
    REJECT = "reject"
    SANDBOX_CANDIDATE = "sandbox_candidate"
    PILOT_CANDIDATE = "pilot_candidate"
    CERTIFICATION_CANDIDATE = "certification_candidate"
    QUARANTINE = "quarantine"


class AdmissionReadiness(str, Enum):
    NOT_READY = "not_ready"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    CONFLICTED = "conflicted"
    RISK_BLOCKED = "risk_blocked"
    READY_FOR_REVIEW = "ready_for_review"
    READY_FOR_SANDBOX = "ready_for_sandbox"


class CompatibilityState(str, Enum):
    COMPATIBLE = "compatible"
    PARTIAL = "partial"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise EcosystemCatalogValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise EcosystemCatalogValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise EcosystemCatalogValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise EcosystemCatalogValidationError(f"malformed {label}")


def _require_timestamp(value: str, label: str) -> None:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise EcosystemCatalogValidationError(
            f"{label} must be a canonical UTC timestamp"
        )


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise EcosystemCatalogValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise EcosystemCatalogValidationError(
            f"{label} must be a repository-relative reference"
        )


@dataclass(frozen=True, slots=True)
class EcosystemCatalogConfig:
    enabled: bool = False
    expected_registry_schema: int = INTEGRATION_REGISTRY_SCHEMA_VERSION
    schema_version: int = ECOSYSTEM_CATALOG_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if self.schema_version != ECOSYSTEM_CATALOG_SCHEMA_VERSION:
            raise EcosystemCatalogValidationError(
                "unsupported ecosystem catalog schema"
            )
        if self.expected_registry_schema != INTEGRATION_REGISTRY_SCHEMA_VERSION:
            raise EcosystemCatalogValidationError(
                "incompatible integration registry schema"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "ecosystem catalog configuration")

    @property
    def can_discover(self) -> bool:
        return False

    @property
    def can_crawl(self) -> bool:
        return False

    @property
    def can_install(self) -> bool:
        return False

    @property
    def can_activate(self) -> bool:
        return False

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_mutate_registry(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    evidence_id: str
    source_kind: DiscoverySourceKind
    observed_at: str
    source_identity: str
    content_digest: str
    provenance: str
    reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.evidence_id, "discovery evidence ID")
        _require_timestamp(self.observed_at, "evidence observation time")
        _require_digest(self.content_digest, "evidence content digest")

        if not isinstance(self.source_kind, DiscoverySourceKind):
            raise EcosystemCatalogValidationError(
                "unknown discovery source kind"
            )
        if not self.source_identity.strip():
            raise EcosystemCatalogValidationError(
                "discovery source identity is required"
            )
        if not self.provenance.strip():
            raise EcosystemCatalogValidationError(
                "discovery provenance is required"
            )

        _validate_sanitized(asdict(self), "discovery evidence")
        _require_relative_reference(
            self.reference,
            "discovery evidence reference",
        )


@dataclass(frozen=True, slots=True)
class DiscoveredIntegration:
    discovery_id: str
    integration_id: str
    canonical_project_name: str
    category: IntegrationCategory
    repository_url: str
    repository_identity: str
    pinned_identity: str
    release_label: str | None
    maintainer_identity: str
    license_classification: str
    maturity: str
    capabilities: tuple[str, ...]
    supported_machines: tuple[str, ...]
    supported_profiles: tuple[str, ...]
    known_risks: tuple[str, ...]
    threat_model_references: tuple[str, ...]
    evidence: tuple[DiscoveryEvidence, ...]
    observed_at: str
    schema_version: int = ECOSYSTEM_CATALOG_SCHEMA_VERSION
    discovery_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.discovery_digest and self.discovery_digest != expected:
            raise EcosystemCatalogValidationError(
                "discovered integration digest mismatch"
            )
        if not self.discovery_digest:
            object.__setattr__(self, "discovery_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["category"] = self.category.value
        payload.pop("discovery_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != ECOSYSTEM_CATALOG_SCHEMA_VERSION:
            raise EcosystemCatalogValidationError(
                "unsupported discovered integration schema"
            )

        _require_identifier(self.discovery_id, "discovery ID")
        _require_identifier(self.integration_id, "integration ID")
        _require_timestamp(self.observed_at, "discovery observation time")

        if not isinstance(self.category, IntegrationCategory):
            raise EcosystemCatalogValidationError(
                "unknown integration category"
            )
        if not self.canonical_project_name.strip():
            raise EcosystemCatalogValidationError(
                "canonical project name is required"
            )

        match = _PUBLIC_REPOSITORY_URL.fullmatch(self.repository_url)

        if match is None or _REPOSITORY.fullmatch(
            self.repository_identity
        ) is None:
            raise EcosystemCatalogValidationError(
                "malformed repository identity"
            )

        if (
            match.group(1).removesuffix(".git").lower()
            != self.repository_identity.lower()
        ):
            raise EcosystemCatalogValidationError(
                "repository URL and identity conflict"
            )

        if not (
            _COMMIT.fullmatch(self.pinned_identity)
            or _DIGEST.fullmatch(self.pinned_identity)
        ):
            raise EcosystemCatalogValidationError(
                "immutable commit or release digest is required"
            )

        for value, label in (
            (self.maintainer_identity, "maintainer identity"),
            (self.license_classification, "license classification"),
            (self.maturity, "maturity"),
        ):
            if not value.strip():
                raise EcosystemCatalogValidationError(
                    f"{label} is required"
                )

        for values, label in (
            (self.capabilities, "capability"),
            (self.supported_machines, "supported machine"),
            (self.supported_profiles, "supported profile"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise EcosystemCatalogValidationError(
                    f"duplicate {label}"
                )

        if len({item.evidence_id for item in self.evidence}) != len(
            self.evidence
        ):
            raise EcosystemCatalogValidationError(
                "duplicate discovery evidence identity"
            )

        for reference in self.threat_model_references:
            _require_relative_reference(
                reference,
                "threat-model reference",
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "discovered integration",
        )

    @property
    def can_install(self) -> bool:
        return False

    @property
    def can_activate(self) -> bool:
        return False

    @property
    def can_escalate_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class CatalogEnvironment:
    machines: tuple[str, ...]
    profiles: tuple[str, ...]
    approved_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        for values, label in (
            (self.machines, "environment machine"),
            (self.profiles, "environment profile"),
            (self.approved_capabilities, "approved capability"),
        ):
            for value in values:
                _require_identifier(value, label)
            if len(set(values)) != len(values):
                raise EcosystemCatalogValidationError(
                    f"duplicate {label}"
                )


@dataclass(frozen=True, slots=True)
class CatalogConflict:
    conflict_id: str
    conflicting_integration_id: str
    overlapping_capabilities: tuple[str, ...]
    severity: int
    reason: str
    evidence_reference: str

    def __post_init__(self) -> None:
        _require_identifier(self.conflict_id, "conflict ID")
        _require_identifier(
            self.conflicting_integration_id,
            "conflicting integration ID",
        )

        if not 1 <= self.severity <= 100:
            raise EcosystemCatalogValidationError(
                "conflict severity is outside bounds"
            )
        if not self.reason.strip():
            raise EcosystemCatalogValidationError(
                "conflict reason is required"
            )

        for capability in self.overlapping_capabilities:
            _require_identifier(capability, "overlapping capability")

        if len(set(self.overlapping_capabilities)) != len(
            self.overlapping_capabilities
        ):
            raise EcosystemCatalogValidationError(
                "duplicate overlapping capability"
            )

        _validate_sanitized(asdict(self), "catalog conflict")
        _require_relative_reference(
            self.evidence_reference,
            "conflict evidence reference",
        )


@dataclass(frozen=True, slots=True)
class CatalogAssessment:
    discovery_id: str
    integration_id: str
    compatibility: CompatibilityState
    machine_matches: tuple[str, ...]
    profile_matches: tuple[str, ...]
    capability_matches: tuple[str, ...]
    conflicts: tuple[CatalogConflict, ...]
    evidence_complete: bool
    evidence_current: bool
    registry_match: bool
    recommendation: CatalogRecommendation
    admission_readiness: AdmissionReadiness
    reason: str
    discovery_digest: str
    assessment_digest: str = ""
    schema_version: int = ECOSYSTEM_CATALOG_SCHEMA_VERSION
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.assessment_digest and self.assessment_digest != expected:
            raise EcosystemCatalogValidationError(
                "catalog assessment digest mismatch"
            )
        if not self.assessment_digest:
            object.__setattr__(self, "assessment_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["compatibility"] = self.compatibility.value
        payload["recommendation"] = self.recommendation.value
        payload["admission_readiness"] = self.admission_readiness.value
        payload.pop("assessment_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != ECOSYSTEM_CATALOG_SCHEMA_VERSION:
            raise EcosystemCatalogValidationError(
                "unsupported catalog assessment schema"
            )

        _require_identifier(self.discovery_id, "assessment discovery ID")
        _require_identifier(self.integration_id, "assessment integration ID")
        _require_digest(self.discovery_digest, "discovery digest")

        if not isinstance(self.compatibility, CompatibilityState):
            raise EcosystemCatalogValidationError(
                "unknown compatibility state"
            )
        if not isinstance(self.recommendation, CatalogRecommendation):
            raise EcosystemCatalogValidationError(
                "unknown catalog recommendation"
            )
        if not isinstance(self.admission_readiness, AdmissionReadiness):
            raise EcosystemCatalogValidationError(
                "unknown admission readiness"
            )
        if not self.reason.strip():
            raise EcosystemCatalogValidationError(
                "catalog assessment reason is required"
            )

        if len({item.conflict_id for item in self.conflicts}) != len(
            self.conflicts
        ):
            raise EcosystemCatalogValidationError(
                "duplicate conflict identity"
            )

        self.authority.validate()
        _validate_sanitized(
            self.digest_payload(),
            "catalog assessment",
        )

    @property
    def can_admit(self) -> bool:
        return False

    @property
    def can_install(self) -> bool:
        return False

    @property
    def can_activate(self) -> bool:
        return False


def validate_against_registry(
    discovery: DiscoveredIntegration,
    entry: IntegrationRegistryEntry,
) -> None:
    """Validate identity compatibility with an existing Stage 1 entry."""

    if discovery.integration_id != entry.integration_id:
        raise EcosystemCatalogValidationError(
            "catalog and registry integration identity mismatch"
        )
    if discovery.category is not entry.category:
        raise EcosystemCatalogValidationError(
            "catalog and registry category mismatch"
        )
    if (
        discovery.repository_identity.lower()
        != entry.upstream_repository_identity.lower()
    ):
        raise EcosystemCatalogValidationError(
            "catalog and registry repository mismatch"
        )
    if discovery.pinned_identity != entry.pinned_identity:
        raise EcosystemCatalogValidationError(
            "catalog and registry pinned identity mismatch"
        )

    entry.authority.validate()


def assess_discovery(
    config: EcosystemCatalogConfig,
    discovery: DiscoveredIntegration,
    environment: CatalogEnvironment,
    *,
    registry_entry: IntegrationRegistryEntry | None,
    conflicts: tuple[CatalogConflict, ...] = (),
    evidence_age_seconds: int,
    stale_after_seconds: int = 604800,
) -> CatalogAssessment:
    """Evaluate supplied discovery evidence without performing discovery."""

    if not 1 <= stale_after_seconds <= 31_536_000:
        raise EcosystemCatalogValidationError(
            "catalog staleness threshold is outside bounds"
        )
    if evidence_age_seconds < 0:
        raise EcosystemCatalogValidationError(
            "discovery evidence cannot originate in the future"
        )

    machine_matches = tuple(
        sorted(
            set(discovery.supported_machines).intersection(
                environment.machines
            )
        )
    )
    profile_matches = tuple(
        sorted(
            set(discovery.supported_profiles).intersection(
                environment.profiles
            )
        )
    )
    capability_matches = tuple(
        sorted(
            set(discovery.capabilities).intersection(
                environment.approved_capabilities
            )
        )
    )

    evidence_kinds = {item.source_kind for item in discovery.evidence}
    required_kinds = {
        DiscoverySourceKind.REPOSITORY_SNAPSHOT,
        DiscoverySourceKind.LICENSE_REVIEW,
        DiscoverySourceKind.ACTIVITY_REVIEW,
        DiscoverySourceKind.SECURITY_REVIEW,
    }
    evidence_complete = required_kinds.issubset(evidence_kinds)
    evidence_current = evidence_age_seconds <= stale_after_seconds

    registry_match = False

    if registry_entry is not None:
        validate_against_registry(discovery, registry_entry)
        registry_match = True

    if not config.enabled:
        compatibility = CompatibilityState.UNKNOWN
        recommendation = CatalogRecommendation.HOLD
        readiness = AdmissionReadiness.NOT_READY
        reason = "Ecosystem catalog is disabled by policy."
    elif any(item.severity >= 80 for item in conflicts):
        compatibility = CompatibilityState.INCOMPATIBLE
        recommendation = CatalogRecommendation.QUARANTINE
        readiness = AdmissionReadiness.RISK_BLOCKED
        reason = "High-severity ecosystem conflict blocks admission."
    elif discovery.known_risks and not discovery.threat_model_references:
        compatibility = CompatibilityState.UNKNOWN
        recommendation = CatalogRecommendation.REJECT
        readiness = AdmissionReadiness.RISK_BLOCKED
        reason = "Known risks lack threat-model evidence."
    elif conflicts:
        compatibility = CompatibilityState.PARTIAL
        recommendation = CatalogRecommendation.REVIEW
        readiness = AdmissionReadiness.CONFLICTED
        reason = "Ecosystem overlap requires governed review."
    elif not evidence_complete or not evidence_current:
        compatibility = CompatibilityState.UNKNOWN
        recommendation = CatalogRecommendation.HOLD
        readiness = AdmissionReadiness.EVIDENCE_INCOMPLETE
        reason = "Discovery evidence is incomplete or stale."
    elif not machine_matches or not profile_matches or not capability_matches:
        compatibility = CompatibilityState.INCOMPATIBLE
        recommendation = CatalogRecommendation.REJECT
        readiness = AdmissionReadiness.NOT_READY
        reason = "Integration is unsuitable for the governed environment."
    elif registry_entry is None:
        compatibility = CompatibilityState.COMPATIBLE
        recommendation = CatalogRecommendation.REVIEW
        readiness = AdmissionReadiness.READY_FOR_REVIEW
        reason = "Discovery is compatible and ready for registry review."
    elif registry_entry.lifecycle_state in {
        LifecycleState.SANDBOX_APPROVED,
        LifecycleState.PILOT,
        LifecycleState.CERTIFIED,
    }:
        compatibility = CompatibilityState.COMPATIBLE
        recommendation = CatalogRecommendation.SANDBOX_CANDIDATE
        readiness = AdmissionReadiness.READY_FOR_SANDBOX
        reason = "Registry evidence supports governed sandbox evaluation."
    else:
        compatibility = CompatibilityState.COMPATIBLE
        recommendation = CatalogRecommendation.REVIEW
        readiness = AdmissionReadiness.READY_FOR_REVIEW
        reason = "Discovery matches the registry and is ready for review."

    return CatalogAssessment(
        discovery_id=discovery.discovery_id,
        integration_id=discovery.integration_id,
        compatibility=compatibility,
        machine_matches=machine_matches,
        profile_matches=profile_matches,
        capability_matches=capability_matches,
        conflicts=conflicts,
        evidence_complete=evidence_complete,
        evidence_current=evidence_current,
        registry_match=registry_match,
        recommendation=recommendation,
        admission_readiness=readiness,
        reason=reason,
        discovery_digest=discovery.discovery_digest,
    )
