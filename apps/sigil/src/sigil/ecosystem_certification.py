"""Authoritative deterministic Stage 12 ecosystem certification manifest.

Stage 12 is certification-only. This module inventories the governed
post-Phase-9 ecosystem delivered through Stages 1–11 and defines the
requirements that Stages 12A–12D must prove.

This module performs no networking, dispatch, installation, activation,
credential resolution, subprocess execution, shell execution, runtime
mutation, policy mutation, portfolio mutation, approval, capital
authorization, or broker submission.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import AuthorityDenials


ECOSYSTEM_CERTIFICATION_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATIVE_REFERENCE = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[a-zA-Z0-9._/-]{1,256}$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?<![A-Za-z0-9])(?:sk|ghp|xox[baprs])[-_][A-Za-z0-9]{8,}"
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


class EcosystemCertificationValidationError(ValueError):
    """Stage 12 certification manifest input failed closed."""


class CertificationComponentType(str, Enum):
    REGISTRY = "registry"
    CONTRACT = "contract"
    ADAPTER = "adapter"
    CATALOG = "catalog"
    POLICY_ENGINE = "policy_engine"
    ROUTER = "router"
    PROJECTION_BRIDGE = "projection_bridge"


class ExecutionAuthority(str, Enum):
    PROHIBITED = "prohibited"
    DESCRIPTIVE_ONLY = "descriptive_only"
    READ_ONLY = "read_only"
    PROPOSAL_ONLY = "proposal_only"
    PAPER_ONLY = "paper_only"


class NetworkClassification(str, Enum):
    NONE = "none"
    INJECTED_EVIDENCE_ONLY = "injected_evidence_only"
    PUBLIC_READ_MODELED = "public_read_modeled"
    CONNECTION_MODELED_DISABLED = "connection_modeled_disabled"


class CredentialClassification(str, Enum):
    NONE = "none"
    REFERENCES_ONLY = "references_only"
    RESOLUTION_PROHIBITED = "resolution_prohibited"


class FinancialBoundary(str, Enum):
    NO_FINANCIAL_AUTHORITY = "no_financial_authority"
    RESEARCH_ONLY = "research_only"
    PROPOSAL_ONLY = "proposal_only"
    PAPER_ONLY = "paper_only"


class CertificationBlockId(str, Enum):
    STAGE_12A = "12A"
    STAGE_12B = "12B"
    STAGE_12C = "12C"
    STAGE_12D = "12D"


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)

    if _SECRET.search(serialized):
        raise EcosystemCertificationValidationError(
            f"credential material is prohibited in {context}"
        )
    if _PRIVATE_PATH.search(serialized):
        raise EcosystemCertificationValidationError(
            f"private host paths are prohibited in {context}"
        )
    if _PRIVATE_ENDPOINT.search(serialized):
        raise EcosystemCertificationValidationError(
            f"private endpoints are prohibited in {context}"
        )


def _require_identifier(value: str, label: str) -> None:
    if _IDENTIFIER.fullmatch(value) is None:
        raise EcosystemCertificationValidationError(f"malformed {label}")


def _require_digest(value: str, label: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise EcosystemCertificationValidationError(
            f"{label} must be a SHA-256 identity"
        )


def _require_relative_reference(value: str, label: str) -> None:
    if (
        _RELATIVE_REFERENCE.fullmatch(value) is None
        or "//" in value
        or value.startswith(".")
    ):
        raise EcosystemCertificationValidationError(
            f"{label} must be a repository-relative reference"
        )


def _require_nonempty(value: str, label: str) -> None:
    if not value.strip():
        raise EcosystemCertificationValidationError(f"{label} is required")


@dataclass(frozen=True, slots=True)
class CertificationComponent:
    component_id: str
    stage: str
    component_type: CertificationComponentType
    implementation_references: tuple[str, ...]
    test_references: tuple[str, ...]
    configuration_references: tuple[str, ...] = ()
    evidence_references: tuple[str, ...] = ()
    default_lifecycle_state: str = "disabled"
    execution_authority: ExecutionAuthority = ExecutionAuthority.PROHIBITED
    network_classification: NetworkClassification = NetworkClassification.NONE
    credential_classification: CredentialClassification = (
        CredentialClassification.NONE
    )
    financial_boundary: FinancialBoundary = (
        FinancialBoundary.NO_FINANCIAL_AUTHORITY
    )
    required_certification_blocks: tuple[CertificationBlockId, ...] = (
        CertificationBlockId.STAGE_12B,
        CertificationBlockId.STAGE_12C,
        CertificationBlockId.STAGE_12D,
    )
    fail_closed_behavior: str = ""
    rollback_or_quarantine_behavior: str = ""
    dependencies: tuple[str, ...] = ()
    activation_state: str = "prohibited"
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        _require_identifier(self.component_id, "certification component ID")
        _require_nonempty(self.stage, "component stage")
        _require_nonempty(
            self.default_lifecycle_state,
            "component default lifecycle state",
        )
        _require_nonempty(
            self.fail_closed_behavior,
            "component fail-closed behavior",
        )
        _require_nonempty(
            self.rollback_or_quarantine_behavior,
            "component rollback or quarantine behavior",
        )
        _require_nonempty(self.activation_state, "component activation state")

        if not isinstance(self.component_type, CertificationComponentType):
            raise EcosystemCertificationValidationError(
                "unknown certification component type"
            )
        if not isinstance(self.execution_authority, ExecutionAuthority):
            raise EcosystemCertificationValidationError(
                "unknown execution authority classification"
            )
        if not isinstance(self.network_classification, NetworkClassification):
            raise EcosystemCertificationValidationError(
                "unknown network classification"
            )
        if not isinstance(
            self.credential_classification,
            CredentialClassification,
        ):
            raise EcosystemCertificationValidationError(
                "unknown credential classification"
            )
        if not isinstance(self.financial_boundary, FinancialBoundary):
            raise EcosystemCertificationValidationError(
                "unknown financial boundary"
            )

        if not self.implementation_references:
            raise EcosystemCertificationValidationError(
                "component implementation reference is required"
            )
        if not self.test_references:
            raise EcosystemCertificationValidationError(
                "component test reference is required"
            )
        if not self.required_certification_blocks:
            raise EcosystemCertificationValidationError(
                "component certification blocks are required"
            )

        references = (
            self.implementation_references
            + self.test_references
            + self.configuration_references
            + self.evidence_references
        )
        for reference in references:
            _require_relative_reference(
                reference,
                "component repository reference",
            )

        if len(self.required_certification_blocks) != len(
            set(self.required_certification_blocks)
        ):
            raise EcosystemCertificationValidationError(
                "duplicate component certification block"
            )

        for block in self.required_certification_blocks:
            if not isinstance(block, CertificationBlockId):
                raise EcosystemCertificationValidationError(
                    "unknown component certification block"
                )

        if self.execution_authority not in {
            ExecutionAuthority.PROHIBITED,
            ExecutionAuthority.DESCRIPTIVE_ONLY,
            ExecutionAuthority.READ_ONLY,
            ExecutionAuthority.PROPOSAL_ONLY,
            ExecutionAuthority.PAPER_ONLY,
        }:
            raise EcosystemCertificationValidationError(
                "unauthorized component execution authority"
            )

        if self.activation_state not in {
            "prohibited",
            "disabled",
            "read_only",
            "proposal_only",
            "paper_only",
        }:
            raise EcosystemCertificationValidationError(
                "unsupported component activation state"
            )

        self.authority.validate()
        _validate_sanitized(asdict(self), "certification component")

    def validate_paths(self, repository_root: Path) -> tuple[str, ...]:
        missing: list[str] = []

        references = (
            self.implementation_references
            + self.test_references
            + self.configuration_references
            + self.evidence_references
        )

        for reference in references:
            if not (repository_root / reference).is_file():
                missing.append(reference)

        return tuple(sorted(missing))


@dataclass(frozen=True, slots=True)
class CertificationBoundary:
    boundary_id: str
    producer: str
    consumer: str
    permitted_class: str
    denied_class: str
    authoritative_side: str
    fail_closed_result: str
    required_evidence: tuple[str, ...]
    stage_12b_requirement: str

    def __post_init__(self) -> None:
        _require_identifier(self.boundary_id, "certification boundary ID")

        required = {
            "boundary producer": self.producer,
            "boundary consumer": self.consumer,
            "permitted class": self.permitted_class,
            "denied class": self.denied_class,
            "authoritative side": self.authoritative_side,
            "fail-closed result": self.fail_closed_result,
            "Stage 12B requirement": self.stage_12b_requirement,
        }
        for label, value in required.items():
            _require_nonempty(value, label)

        if not self.required_evidence:
            raise EcosystemCertificationValidationError(
                "boundary evidence requirement is required"
            )

        for reference in self.required_evidence:
            _require_relative_reference(
                reference,
                "boundary evidence reference",
            )

        _validate_sanitized(asdict(self), "certification boundary")


@dataclass(frozen=True, slots=True)
class CertificationBlock:
    block_id: CertificationBlockId
    objective: str
    required_inputs: tuple[str, ...]
    required_outputs: tuple[str, ...]
    pass_conditions: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    defects_may_be_fixed: bool = True
    feature_expansion_prohibited: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.block_id, CertificationBlockId):
            raise EcosystemCertificationValidationError(
                "unknown Stage 12 certification block"
            )

        _require_nonempty(self.objective, "certification block objective")

        required_collections = {
            "required inputs": self.required_inputs,
            "required outputs": self.required_outputs,
            "pass conditions": self.pass_conditions,
            "failure conditions": self.failure_conditions,
            "evidence requirements": self.evidence_requirements,
        }
        for label, values in required_collections.items():
            if not values:
                raise EcosystemCertificationValidationError(
                    f"certification block {label} are required"
                )
            if any(not value.strip() for value in values):
                raise EcosystemCertificationValidationError(
                    f"certification block {label} contain an empty value"
                )

        if not self.feature_expansion_prohibited:
            raise EcosystemCertificationValidationError(
                "Stage 12 feature expansion must remain prohibited"
            )

        _validate_sanitized(asdict(self), "certification block")


@dataclass(frozen=True, slots=True)
class CertificationInvariant:
    invariant_id: str
    statement: str
    failure_result: str
    required_blocks: tuple[CertificationBlockId, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.invariant_id, "certification invariant ID")
        _require_nonempty(self.statement, "certification invariant statement")
        _require_nonempty(
            self.failure_result,
            "certification invariant failure result",
        )

        if not self.required_blocks:
            raise EcosystemCertificationValidationError(
                "certification invariant blocks are required"
            )
        if len(self.required_blocks) != len(set(self.required_blocks)):
            raise EcosystemCertificationValidationError(
                "duplicate certification invariant block"
            )

        for block in self.required_blocks:
            if not isinstance(block, CertificationBlockId):
                raise EcosystemCertificationValidationError(
                    "unknown certification invariant block"
                )

        _validate_sanitized(asdict(self), "certification invariant")


@dataclass(frozen=True, slots=True)
class CertificationValidationResult:
    valid: bool
    manifest_identity: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    component_count: int
    boundary_count: int
    invariant_count: int
    certification_block_count: int
    paper_only_confirmed: bool
    broker_submission_disabled_confirmed: bool
    feature_freeze_confirmed: bool

    def __post_init__(self) -> None:
        _require_digest(
            self.manifest_identity,
            "certification manifest identity",
        )

        counts = (
            self.component_count,
            self.boundary_count,
            self.invariant_count,
            self.certification_block_count,
        )
        if any(count < 0 for count in counts):
            raise EcosystemCertificationValidationError(
                "certification validation counts cannot be negative"
            )

        if self.valid and self.errors:
            raise EcosystemCertificationValidationError(
                "valid certification result cannot contain errors"
            )
        if not self.valid and not self.errors:
            raise EcosystemCertificationValidationError(
                "invalid certification result must contain errors"
            )

        if tuple(sorted(self.errors)) != self.errors:
            raise EcosystemCertificationValidationError(
                "certification validation errors must be ordered"
            )
        if tuple(sorted(self.warnings)) != self.warnings:
            raise EcosystemCertificationValidationError(
                "certification validation warnings must be ordered"
            )

        _validate_sanitized(asdict(self), "certification validation result")


_REQUIRED_COMPONENT_IDS = frozenset(
    {
        "integration-registry",
        "worker-contract",
        "hermes-webui",
        "paperclip",
        "buzz-relay",
        "buzznode",
        "hermes-wiki",
        "ecosystem-catalog",
        "agent-reach",
        "self-evolution",
        "fleet-routing",
        "sigil-desktop-bridge",
    }
)

_REQUIRED_INVARIANT_IDS = frozenset(
    {
        "paper-only",
        "broker-submission-disabled",
        "no-independent-execution-authority",
        "disabled-integrations-inactive",
        "untrusted-input-non-authoritative",
        "invalid-evidence-fails-closed",
        "secrets-prohibited",
        "deterministic-identities",
        "duplicate-requests-idempotent",
        "recovery-cannot-escalate",
        "ui-projections-non-authoritative",
        "self-evolution-boundary",
        "knowledge-non-authoritative",
        "golden-master-gated",
    }
)


@dataclass(frozen=True, slots=True)
class EcosystemCertificationManifest:
    program_name: str
    certification_stage: str
    certification_scope: str
    source_baseline: str
    creation_policy: str
    components: tuple[CertificationComponent, ...]
    boundaries: tuple[CertificationBoundary, ...]
    invariants: tuple[CertificationInvariant, ...]
    certification_blocks: tuple[CertificationBlock, ...]
    paper_only: bool = True
    broker_submission: bool = False
    feature_expansion_prohibited: bool = True
    schema_version: int = ECOSYSTEM_CERTIFICATION_SCHEMA_VERSION
    manifest_identity: str = ""

    def __post_init__(self) -> None:
        canonical_components = tuple(
            sorted(self.components, key=lambda item: item.component_id)
        )
        canonical_boundaries = tuple(
            sorted(self.boundaries, key=lambda item: item.boundary_id)
        )
        canonical_invariants = tuple(
            sorted(self.invariants, key=lambda item: item.invariant_id)
        )
        canonical_blocks = tuple(
            sorted(
                self.certification_blocks,
                key=lambda item: item.block_id.value,
            )
        )

        object.__setattr__(self, "components", canonical_components)
        object.__setattr__(self, "boundaries", canonical_boundaries)
        object.__setattr__(self, "invariants", canonical_invariants)
        object.__setattr__(self, "certification_blocks", canonical_blocks)

        self._validate_structure()

        expected = self.expected_identity()
        if self.manifest_identity and self.manifest_identity != expected:
            raise EcosystemCertificationValidationError(
                "certification manifest identity mismatch"
            )
        if not self.manifest_identity:
            object.__setattr__(self, "manifest_identity", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("manifest_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def canonical_projection(self) -> dict[str, object]:
        return asdict(self)

    def _validate_structure(self) -> None:
        if self.schema_version != ECOSYSTEM_CERTIFICATION_SCHEMA_VERSION:
            raise EcosystemCertificationValidationError(
                "unsupported ecosystem certification schema version"
            )

        required_text = {
            "program name": self.program_name,
            "certification stage": self.certification_stage,
            "certification scope": self.certification_scope,
            "source baseline": self.source_baseline,
            "creation policy": self.creation_policy,
        }
        for label, value in required_text.items():
            _require_nonempty(value, label)

        component_ids = tuple(item.component_id for item in self.components)
        boundary_ids = tuple(item.boundary_id for item in self.boundaries)
        invariant_ids = tuple(item.invariant_id for item in self.invariants)
        block_ids = tuple(item.block_id for item in self.certification_blocks)

        if len(component_ids) != len(set(component_ids)):
            raise EcosystemCertificationValidationError(
                "duplicate certification component ID"
            )
        if len(boundary_ids) != len(set(boundary_ids)):
            raise EcosystemCertificationValidationError(
                "duplicate certification boundary ID"
            )
        if len(invariant_ids) != len(set(invariant_ids)):
            raise EcosystemCertificationValidationError(
                "duplicate certification invariant ID"
            )
        if len(block_ids) != len(set(block_ids)):
            raise EcosystemCertificationValidationError(
                "duplicate Stage 12 certification block"
            )

        missing_components = sorted(
            _REQUIRED_COMPONENT_IDS - set(component_ids)
        )
        if missing_components:
            raise EcosystemCertificationValidationError(
                "missing required certification components: "
                + ", ".join(missing_components)
            )

        required_blocks = set(CertificationBlockId)
        if set(block_ids) != required_blocks:
            missing = sorted(
                block.value for block in required_blocks - set(block_ids)
            )
            unexpected = sorted(
                block.value for block in set(block_ids) - required_blocks
            )
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise EcosystemCertificationValidationError(
                "Stage 12 block inventory is incomplete: "
                + "; ".join(details)
            )

        missing_invariants = sorted(
            _REQUIRED_INVARIANT_IDS - set(invariant_ids)
        )
        if missing_invariants:
            raise EcosystemCertificationValidationError(
                "missing required certification invariants: "
                + ", ".join(missing_invariants)
            )

        if not self.paper_only:
            raise EcosystemCertificationValidationError(
                "certification manifest must remain paper-only"
            )
        if self.broker_submission:
            raise EcosystemCertificationValidationError(
                "broker submission must remain disabled"
            )
        if not self.feature_expansion_prohibited:
            raise EcosystemCertificationValidationError(
                "Stage 12 feature expansion must remain prohibited"
            )

        for component in self.components:
            if component.component_id == "sigil-desktop-bridge":
                if component.financial_boundary != FinancialBoundary.PAPER_ONLY:
                    raise EcosystemCertificationValidationError(
                        "Sigil desktop bridge must remain paper-only"
                    )
                if component.execution_authority not in {
                    ExecutionAuthority.DESCRIPTIVE_ONLY,
                    ExecutionAuthority.READ_ONLY,
                    ExecutionAuthority.PAPER_ONLY,
                }:
                    raise EcosystemCertificationValidationError(
                        "Sigil desktop bridge has unauthorized execution authority"
                    )

            allowed_activation_states = {
                "disabled": {
                    "prohibited",
                    "disabled",
                    "read_only",
                    "proposal_only",
                    "paper_only",
                },
                "prohibited": {"prohibited"},
                "read_only": {"read_only"},
                "proposal_only": {"proposal_only"},
                "paper_only": {"paper_only"},
            }
            allowed = allowed_activation_states.get(
                component.default_lifecycle_state
            )
            if (
                allowed is None
                or component.activation_state not in allowed
            ):
                raise EcosystemCertificationValidationError(
                    f"contradictory lifecycle and activation state for "
                    f"{component.component_id}"
                )

        _validate_sanitized(
            self.digest_payload(),
            "ecosystem certification manifest",
        )

    def validate(
        self,
        repository_root: Path | None = None,
    ) -> CertificationValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            self._validate_structure()
        except EcosystemCertificationValidationError as error:
            errors.append(str(error))

        if self.manifest_identity != self.expected_identity():
            errors.append("certification manifest identity mismatch")

        if repository_root is not None:
            for component in self.components:
                for missing in component.validate_paths(repository_root):
                    errors.append(
                        f"missing repository reference for "
                        f"{component.component_id}: {missing}"
                    )

            for boundary in self.boundaries:
                for reference in boundary.required_evidence:
                    if not (repository_root / reference).is_file():
                        errors.append(
                            f"missing boundary evidence for "
                            f"{boundary.boundary_id}: {reference}"
                        )

        return CertificationValidationResult(
            valid=not errors,
            manifest_identity=self.manifest_identity,
            errors=tuple(sorted(set(errors))),
            warnings=tuple(sorted(set(warnings))),
            component_count=len(self.components),
            boundary_count=len(self.boundaries),
            invariant_count=len(self.invariants),
            certification_block_count=len(self.certification_blocks),
            paper_only_confirmed=self.paper_only,
            broker_submission_disabled_confirmed=not self.broker_submission,
            feature_freeze_confirmed=self.feature_expansion_prohibited,
        )


def build_authoritative_components() -> tuple[CertificationComponent, ...]:
    common_blocks = (
        CertificationBlockId.STAGE_12B,
        CertificationBlockId.STAGE_12C,
        CertificationBlockId.STAGE_12D,
    )

    return (
        CertificationComponent(
            component_id="integration-registry",
            stage="1",
            component_type=CertificationComponentType.REGISTRY,
            implementation_references=(
                "apps/sigil/src/sigil/integration_registry.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_integration_registry.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/INTEGRATION_REGISTRY_STAGE1_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.DESCRIPTIVE_ONLY,
            fail_closed_behavior=(
                "Reject malformed, conflicting, corrupt, or unauthorized "
                "registry state and preserve disabled integration status."
            ),
            rollback_or_quarantine_behavior=(
                "Disable or quarantine the affected registry entry."
            ),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="worker-contract",
            stage="2",
            component_type=CertificationComponentType.CONTRACT,
            implementation_references=(
                "apps/sigil/src/sigil/worker_contract.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_worker_contract.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/WORKER_JOB_CONTRACT_STAGE2_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.PROPOSAL_ONLY,
            fail_closed_behavior=(
                "Reject invalid contracts, evidence, admission decisions, "
                "state transitions, duplicate authority, or corrupt storage."
            ),
            rollback_or_quarantine_behavior=(
                "Cancel or quarantine the affected job without granting dispatch."
            ),
            dependencies=("integration-registry",),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="hermes-webui",
            stage="3",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/hermes_webui_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_hermes_webui_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/HERMES_WEBUI_STAGE3_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.READ_ONLY,
            network_classification=(
                NetworkClassification.CONNECTION_MODELED_DISABLED
            ),
            credential_classification=(
                CredentialClassification.RESOLUTION_PROHIBITED
            ),
            fail_closed_behavior=(
                "Reject malformed or unauthorized operator projections and "
                "preserve non-mutating disconnected state."
            ),
            rollback_or_quarantine_behavior=(
                "Disable the adapter projection and preserve backend authority."
            ),
            dependencies=("integration-registry", "worker-contract"),
            activation_state="read_only",
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="paperclip",
            stage="4",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/paperclip_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paperclip_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/PAPERCLIP_STAGE4_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.PROPOSAL_ONLY,
            fail_closed_behavior=(
                "Reject invalid organizational assignments, approvals, or "
                "evidence and deny execution authority."
            ),
            rollback_or_quarantine_behavior=(
                "Cancel or quarantine the assignment while preserving Hermes authority."
            ),
            dependencies=("integration-registry", "worker-contract"),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="buzz-relay",
            stage="5",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/buzz_relay_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_buzz_relay_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/BUZZ_RELAY_STAGE5_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.DESCRIPTIVE_ONLY,
            network_classification=(
                NetworkClassification.CONNECTION_MODELED_DISABLED
            ),
            credential_classification=(
                CredentialClassification.RESOLUTION_PROHIBITED
            ),
            fail_closed_behavior=(
                "Reject malformed, unsigned, duplicate, or unauthorized events "
                "and deny command authority."
            ),
            rollback_or_quarantine_behavior=(
                "Disconnect or quarantine the relay without mutating runtime state."
            ),
            dependencies=("integration-registry", "worker-contract"),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="buzznode",
            stage="6",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/buzznode_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_buzznode_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/BUZZNODE_STAGE6_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.PROPOSAL_ONLY,
            network_classification=(
                NetworkClassification.CONNECTION_MODELED_DISABLED
            ),
            credential_classification=(
                CredentialClassification.RESOLUTION_PROHIBITED
            ),
            fail_closed_behavior=(
                "Reject stale, incompatible, uncertified, or unauthorized node "
                "evidence and deny placement or execution."
            ),
            rollback_or_quarantine_behavior=(
                "Quarantine the node and remove it from eligible placement."
            ),
            dependencies=("integration-registry", "worker-contract"),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="hermes-wiki",
            stage="7",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/hermes_wiki_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_hermes_wiki_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/HERMES_WIKI_STAGE7_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.READ_ONLY,
            network_classification=(
                NetworkClassification.INJECTED_EVIDENCE_ONLY
            ),
            fail_closed_behavior=(
                "Reject malformed, stale, conflicting, or unverifiable knowledge "
                "and prevent it from becoming authoritative runtime truth."
            ),
            rollback_or_quarantine_behavior=(
                "Quarantine the affected knowledge record or projection."
            ),
            dependencies=("integration-registry",),
            activation_state="read_only",
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="ecosystem-catalog",
            stage="8",
            component_type=CertificationComponentType.CATALOG,
            implementation_references=(
                "apps/sigil/src/sigil/ecosystem_catalog.py",
            ),
            test_references=(
                "apps/sigil/tests/test_ecosystem_catalog.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/ECOSYSTEM_CATALOG_STAGE8_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.DESCRIPTIVE_ONLY,
            network_classification=(
                NetworkClassification.INJECTED_EVIDENCE_ONLY
            ),
            fail_closed_behavior=(
                "Reject incomplete, conflicting, risky, or unverifiable catalog "
                "evidence and deny registry admission or activation."
            ),
            rollback_or_quarantine_behavior=(
                "Hold, reject, or quarantine the candidate without installation."
            ),
            dependencies=("integration-registry",),
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="agent-reach",
            stage="8A",
            component_type=CertificationComponentType.ADAPTER,
            implementation_references=(
                "apps/sigil/src/sigil/agent_reach_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_agent_reach_adapter.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/AGENT_REACH_STAGE8A_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.READ_ONLY,
            network_classification=(
                NetworkClassification.PUBLIC_READ_MODELED
            ),
            credential_classification=(
                CredentialClassification.RESOLUTION_PROHIBITED
            ),
            financial_boundary=FinancialBoundary.RESEARCH_ONLY,
            fail_closed_behavior=(
                "Reject private, authenticated, mutating, malformed, or "
                "unverifiable internet access requests."
            ),
            rollback_or_quarantine_behavior=(
                "Disable the capability and discard untrusted research evidence."
            ),
            dependencies=("integration-registry", "ecosystem-catalog"),
            activation_state="read_only",
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="self-evolution",
            stage="9",
            component_type=CertificationComponentType.POLICY_ENGINE,
            implementation_references=(
                "apps/sigil/src/sigil/self_evolution.py",
            ),
            test_references=(
                "apps/sigil/tests/test_self_evolution.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/SELF_EVOLUTION_STAGE9_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.PROPOSAL_ONLY,
            fail_closed_behavior=(
                "Reject incomplete evidence, missing independent review, failed "
                "regression proof, or unauthorized promotion."
            ),
            rollback_or_quarantine_behavior=(
                "Reject or quarantine the proposal and preserve current policy."
            ),
            dependencies=(
                "integration-registry",
                "worker-contract",
                "ecosystem-catalog",
            ),
            activation_state="proposal_only",
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="fleet-routing",
            stage="10",
            component_type=CertificationComponentType.ROUTER,
            implementation_references=(
                "apps/sigil/src/sigil/fleet_routing.py",
                "apps/sigil/src/sigil/ai/fleet.py",
            ),
            test_references=(
                "apps/sigil/tests/test_fleet_routing.py",
                "apps/sigil/tests/test_governed_ai_fleet_routing.py",
            ),
            evidence_references=(
                "docs/sigil/evidence/ROUTING_FLEET_STAGE10_CERTIFICATION.json",
            ),
            execution_authority=ExecutionAuthority.PROPOSAL_ONLY,
            network_classification=(
                NetworkClassification.INJECTED_EVIDENCE_ONLY
            ),
            credential_classification=(
                CredentialClassification.RESOLUTION_PROHIBITED
            ),
            fail_closed_behavior=(
                "Exclude disabled, stale, unhealthy, incompatible, uncertified, "
                "over-budget, or capacity-blocked nodes and deny dispatch."
            ),
            rollback_or_quarantine_behavior=(
                "Exclude or quarantine the node and preserve non-dispatching state."
            ),
            dependencies=("worker-contract", "buzznode"),
            activation_state="proposal_only",
            required_certification_blocks=common_blocks,
        ),
        CertificationComponent(
            component_id="sigil-desktop-bridge",
            stage="11",
            component_type=CertificationComponentType.PROJECTION_BRIDGE,
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/bridge.py",
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
            execution_authority=ExecutionAuthority.PAPER_ONLY,
            financial_boundary=FinancialBoundary.PAPER_ONLY,
            fail_closed_behavior=(
                "Reject malformed, contradictory, unauthorized, or corrupt bridge "
                "state and restore the disconnected paper-only projection."
            ),
            rollback_or_quarantine_behavior=(
                "Discard invalid projection state and rebuild the safe default snapshot."
            ),
            dependencies=tuple(sorted(_REQUIRED_COMPONENT_IDS - {"sigil-desktop-bridge"})),
            activation_state="paper_only",
            required_certification_blocks=common_blocks,
        ),
    )


def build_authoritative_boundaries() -> tuple[CertificationBoundary, ...]:
    return (
        CertificationBoundary(
            boundary_id="registry-vs-execution",
            producer="governed integration registry",
            consumer="worker admission and runtime layers",
            permitted_class=(
                "Descriptive metadata, lifecycle state, capabilities, and "
                "immutable identity references."
            ),
            denied_class=(
                "Installation, activation, dispatch, execution, approval, "
                "capital, or policy authority."
            ),
            authoritative_side="Existing governed admission and runtime policy.",
            fail_closed_result=(
                "Reject the requested operation and preserve disabled state."
            ),
            required_evidence=(
                "docs/sigil/evidence/INTEGRATION_REGISTRY_STAGE1_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove registry membership cannot independently grant execution authority."
            ),
        ),
        CertificationBoundary(
            boundary_id="worker-acceptance-vs-dispatch",
            producer="common worker job contract",
            consumer="fleet routing and worker runtime",
            permitted_class=(
                "Validated job intent, limits, evidence requirements, and "
                "independent admission decisions."
            ),
            denied_class=(
                "Automatic dispatch, direct provider invocation, duplicate "
                "execution, or authority escalation."
            ),
            authoritative_side="Governed dispatcher and runtime admission policy.",
            fail_closed_result=(
                "Reject or cancel the job without dispatching it."
            ),
            required_evidence=(
                "docs/sigil/evidence/WORKER_JOB_CONTRACT_STAGE2_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove accepted contracts remain non-dispatching until separately authorized."
            ),
        ),
        CertificationBoundary(
            boundary_id="webui-visibility-vs-mutation",
            producer="Hermes WebUI adapter",
            consumer="operator interface",
            permitted_class=(
                "Sanitized read-only status, sessions, tasks, approvals, and evidence projections."
            ),
            denied_class=(
                "Direct backend mutation, credential access, execution, installation, "
                "activation, or financial authority."
            ),
            authoritative_side="Hermes backend and governed runtime.",
            fail_closed_result=(
                "Suppress the projection and preserve disconnected read-only state."
            ),
            required_evidence=(
                "docs/sigil/evidence/HERMES_WEBUI_STAGE3_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove UI visibility cannot mutate authoritative backend state."
            ),
        ),
        CertificationBoundary(
            boundary_id="paperclip-assignment-vs-execution",
            producer="Paperclip adapter",
            consumer="Hermes worker and project orchestration",
            permitted_class=(
                "Assignments, issues, comments, organizational metadata, and approval references."
            ),
            denied_class=(
                "Independent task execution, policy mutation, capital authority, or promotion."
            ),
            authoritative_side="Hermes governance and worker admission.",
            fail_closed_result=(
                "Reject or quarantine the assignment without executing it."
            ),
            required_evidence=(
                "docs/sigil/evidence/PAPERCLIP_STAGE4_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove organizational assignment never becomes execution authority."
            ),
        ),
        CertificationBoundary(
            boundary_id="buzz-events-vs-command-authority",
            producer="Buzz Relay adapter",
            consumer="collaboration workspace and Hermes event consumers",
            permitted_class=(
                "Sanitized signed events, messages, approvals, and workflow references."
            ),
            denied_class=(
                "Unsigned commands, direct execution, policy mutation, credentials, "
                "or financial instructions."
            ),
            authoritative_side="Hermes governed command and approval pipeline.",
            fail_closed_result=(
                "Reject the event and preserve the current authoritative state."
            ),
            required_evidence=(
                "docs/sigil/evidence/BUZZ_RELAY_STAGE5_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove event transport cannot grant command or execution authority."
            ),
        ),
        CertificationBoundary(
            boundary_id="buzznode-registration-vs-placement",
            producer="Buzznode adapter",
            consumer="fleet routing",
            permitted_class=(
                "Node identity, capabilities, trust, certification, health, and capacity evidence."
            ),
            denied_class=(
                "Self-placement, self-dispatch, shell execution, credential access, "
                "or automatic failover authority."
            ),
            authoritative_side="Governed fleet routing and worker admission.",
            fail_closed_result=(
                "Exclude or quarantine the node from placement."
            ),
            required_evidence=(
                "docs/sigil/evidence/BUZZNODE_STAGE6_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove registration alone cannot make a node eligible for placement."
            ),
        ),
        CertificationBoundary(
            boundary_id="knowledge-vs-runtime-truth",
            producer="Hermes Wiki and ecosystem catalog",
            consumer="research, discovery, and operator projections",
            permitted_class=(
                "Sanitized knowledge, discovery evidence, compatibility assessments, "
                "and recommendations."
            ),
            denied_class=(
                "Override of installed source, observed runtime state, policy, "
                "registry admission, or execution authority."
            ),
            authoritative_side="Installed source, governed registry, and observed runtime evidence.",
            fail_closed_result=(
                "Reject or quarantine conflicting or unverifiable knowledge."
            ),
            required_evidence=(
                "docs/sigil/evidence/HERMES_WIKI_STAGE7_CERTIFICATION.json",
                "docs/sigil/evidence/ECOSYSTEM_CATALOG_STAGE8_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove knowledge and catalog content remain non-authoritative."
            ),
        ),
        CertificationBoundary(
            boundary_id="agent-reach-public-vs-private",
            producer="Agent Reach adapter",
            consumer="research and evidence pipelines",
            permitted_class=(
                "Modeled public, unauthenticated, read-only research evidence."
            ),
            denied_class=(
                "Private access, authenticated mutation, credential use, command execution, "
                "or financial action."
            ),
            authoritative_side="Governed research policy and evidence validation.",
            fail_closed_result=(
                "Reject the request and discard untrusted evidence."
            ),
            required_evidence=(
                "docs/sigil/evidence/AGENT_REACH_STAGE8A_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove public-read capability cannot cross into private or mutating access."
            ),
        ),
        CertificationBoundary(
            boundary_id="self-evolution-vs-promotion",
            producer="governed self-evolution framework",
            consumer="governance and release promotion",
            permitted_class=(
                "Improvement opportunities, proposals, experiment plans, results, "
                "reviews, and promotion assessments."
            ),
            denied_class=(
                "Self-promotion, governance mutation, financial-boundary mutation, "
                "or bypass of independent review."
            ),
            authoritative_side="Independent human-governed promotion policy.",
            fail_closed_result=(
                "Reject or quarantine the proposal and preserve current policy."
            ),
            required_evidence=(
                "docs/sigil/evidence/SELF_EVOLUTION_STAGE9_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove candidate generation cannot promote or alter governance."
            ),
        ),
        CertificationBoundary(
            boundary_id="fleet-placement-vs-financial-execution",
            producer="governed fleet routing",
            consumer="worker runtime and Sigil",
            permitted_class=(
                "Deterministic primary, fallback, exclusion, and placement recommendations."
            ),
            denied_class=(
                "Worker dispatch, broker submission, portfolio mutation, capital authority, "
                "or live trading authorization."
            ),
            authoritative_side="Governed dispatcher and Sigil financial controls.",
            fail_closed_result=(
                "Return no eligible route and deny dispatch or financial execution."
            ),
            required_evidence=(
                "docs/sigil/evidence/ROUTING_FLEET_STAGE10_CERTIFICATION.json",
            ),
            stage_12b_requirement=(
                "Prove placement decisions cannot grant financial execution authority."
            ),
        ),
        CertificationBoundary(
            boundary_id="desktop-projection-vs-backend-authority",
            producer="Sigil desktop ecosystem bridge",
            consumer="Sigil Mission Control desktop interface",
            permitted_class=(
                "Read-only and paper-only lifecycle, status, gate, evidence, and "
                "integration projections."
            ),
            denied_class=(
                "Backend mutation, connection, dispatch, installation, activation, "
                "credential resolution, or broker submission."
            ),
            authoritative_side="Sigil backend and Hermes governed runtime.",
            fail_closed_result=(
                "Restore the disconnected paper-only default projection."
            ),
            required_evidence=(
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
            stage_12b_requirement=(
                "Prove desktop projections cannot become authoritative runtime commands."
            ),
        ),
        CertificationBoundary(
            boundary_id="research-proposal-vs-broker-submission",
            producer="Sigil research and proposal layers",
            consumer="order execution boundary",
            permitted_class=(
                "Research evidence, recommendations, proposals, approvals, and paper intent."
            ),
            denied_class=(
                "Live broker submission, capital movement, or portfolio mutation."
            ),
            authoritative_side="Sigil governed execution and promotion controls.",
            fail_closed_result=(
                "Preserve proposal or paper state and reject live submission."
            ),
            required_evidence=(
                "docs/beta/post-phase9/SIGIL_BOUNDARY.md",
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
            stage_12b_requirement=(
                "Prove research and proposal state cannot reach broker submission."
            ),
        ),
        CertificationBoundary(
            boundary_id="paper-vs-live-execution",
            producer="Sigil paper execution runtime",
            consumer="live execution boundary",
            permitted_class=(
                "Deterministic paper orders, simulated fills, positions, cash, and evidence."
            ),
            denied_class=(
                "Live order placement, real capital movement, or implicit promotion."
            ),
            authoritative_side="Explicit live certification and human promotion policy.",
            fail_closed_result=(
                "Remain paper-only and reject any live execution request."
            ),
            required_evidence=(
                "docs/beta/post-phase9/SIGIL_BOUNDARY.md",
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
            stage_12b_requirement=(
                "Prove paper execution cannot cross into live execution without separate certification."
            ),
        ),
    )


def build_authoritative_invariants() -> tuple[CertificationInvariant, ...]:
    all_blocks = tuple(CertificationBlockId)

    return (
        CertificationInvariant(
            invariant_id="paper-only",
            statement="Sigil remains paper-only throughout Stage 12.",
            failure_result="Fail certification and prohibit promotion.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="broker-submission-disabled",
            statement="Broker submission remains disabled throughout Stage 12.",
            failure_result="Fail certification and prohibit promotion.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="no-independent-execution-authority",
            statement=(
                "No external integration receives independent execution, approval, "
                "capital, portfolio, or policy authority."
            ),
            failure_result="Reject the integration state and fail certification.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="disabled-integrations-inactive",
            statement=(
                "Disabled integrations remain inactive unless separately governed "
                "and certified."
            ),
            failure_result="Restore disabled state and fail certification.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="untrusted-input-non-authoritative",
            statement=(
                "Untrusted input cannot mutate authoritative runtime, governance, "
                "financial, or policy state."
            ),
            failure_result="Reject the input and preserve authoritative state.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="invalid-evidence-fails-closed",
            statement=(
                "Malformed, stale, missing, contradictory, corrupt, or unverifiable "
                "evidence fails closed."
            ),
            failure_result="Reject the operation and fail the affected gate.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="secrets-prohibited",
            statement=(
                "Secrets, credential material, private endpoints, and private host "
                "paths never appear in certification evidence or projections."
            ),
            failure_result="Reject and sanitize the artifact; fail certification.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="deterministic-identities",
            statement=(
                "Canonical identities remain deterministic across ordering, hosts, "
                "processes, locales, and timezones."
            ),
            failure_result="Reject the artifact and fail deterministic certification.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="duplicate-requests-idempotent",
            statement=(
                "Duplicate requests cannot create duplicate authority, dispatch, "
                "execution, evidence, or financial effects."
            ),
            failure_result="Reject or reconcile the duplicate without escalation.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="recovery-cannot-escalate",
            statement=(
                "Cancellation, interruption, replay, and recovery cannot escalate "
                "authority or create duplicate execution."
            ),
            failure_result="Restore the last valid state and fail recovery certification.",
            required_blocks=(
                CertificationBlockId.STAGE_12C,
                CertificationBlockId.STAGE_12D,
            ),
        ),
        CertificationInvariant(
            invariant_id="ui-projections-non-authoritative",
            statement=(
                "Desktop and operator-interface projections remain non-authoritative."
            ),
            failure_result="Discard the projection and preserve backend authority.",
            required_blocks=(
                CertificationBlockId.STAGE_12B,
                CertificationBlockId.STAGE_12C,
                CertificationBlockId.STAGE_12D,
            ),
        ),
        CertificationInvariant(
            invariant_id="self-evolution-boundary",
            statement=(
                "Self-evolution cannot modify governance, financial boundaries, "
                "promotion policy, or execution authority."
            ),
            failure_result="Reject or quarantine the proposal.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="knowledge-non-authoritative",
            statement=(
                "Wiki and catalog content cannot override installed source, governed "
                "registry state, or observed runtime evidence."
            ),
            failure_result="Reject or quarantine the conflicting knowledge.",
            required_blocks=all_blocks,
        ),
        CertificationInvariant(
            invariant_id="golden-master-gated",
            statement=(
                "Golden Master readiness cannot be claimed before Stages 12B, 12C, "
                "and 12D all pass."
            ),
            failure_result="Prohibit Golden Master promotion.",
            required_blocks=all_blocks,
        ),
    )


def build_stage12_blocks() -> tuple[CertificationBlock, ...]:
    return (
        CertificationBlock(
            block_id=CertificationBlockId.STAGE_12A,
            objective=(
                "Define and validate the authoritative deterministic certification manifest."
            ),
            required_inputs=(
                "Stage 1 through Stage 11 implementation files.",
                "Existing Stage 1 through Stage 10 certification evidence.",
                "Stage 11 implementation and tests.",
            ),
            required_outputs=(
                "Canonical machine-readable certification manifest.",
                "Stage 12A validation evidence.",
                "Stage 12 certification documentation.",
            ),
            pass_conditions=(
                "The manifest validates deterministically.",
                "Every required component, boundary, invariant, and block is present.",
                "All declared repository references exist.",
                "Paper-only, broker-disabled, and feature-freeze invariants hold.",
            ),
            failure_conditions=(
                "The manifest is incomplete, contradictory, nondeterministic, or tampered.",
                "A required repository reference is missing.",
                "Any authority or financial boundary is expanded.",
            ),
            evidence_requirements=(
                "Checked-in Stage 12A JSON evidence artifact.",
                "Focused deterministic test results.",
                "Cumulative Stage 1 through Stage 11 validation results.",
            ),
        ),
        CertificationBlock(
            block_id=CertificationBlockId.STAGE_12B,
            objective=(
                "Certify every governed producer-consumer boundary with an explicit matrix."
            ),
            required_inputs=(
                "The certified Stage 12A manifest.",
                "Stage 1 through Stage 11 source, tests, and evidence.",
            ),
            required_outputs=(
                "Boundary matrix.",
                "Positive and negative boundary test evidence.",
                "Fail-closed authority proof.",
            ),
            pass_conditions=(
                "Every permitted flow is bounded and deterministic.",
                "Every denied flow fails closed.",
                "No boundary grants independent execution or financial authority.",
            ),
            failure_conditions=(
                "A denied operation succeeds.",
                "Authority ownership is ambiguous.",
                "Evidence is missing, contradictory, or unverifiable.",
            ),
            evidence_requirements=(
                "Machine-readable boundary matrix evidence.",
                "Focused boundary certification tests.",
                "Cumulative regression results.",
            ),
        ),
        CertificationBlock(
            block_id=CertificationBlockId.STAGE_12C,
            objective=(
                "Certify deterministic replay, interruption recovery, idempotency, "
                "and duplicate prevention."
            ),
            required_inputs=(
                "The certified Stage 12A manifest.",
                "The certified Stage 12B boundary matrix.",
                "Persisted deterministic fixtures and recovery scenarios.",
            ),
            required_outputs=(
                "Replay evidence.",
                "Recovery evidence.",
                "Idempotency and duplicate-prevention evidence.",
            ),
            pass_conditions=(
                "Equivalent inputs reproduce equivalent canonical outcomes.",
                "Interrupted state recovers to the last valid authority state.",
                "Replay and recovery create no duplicate execution or escalation.",
            ),
            failure_conditions=(
                "Replay produces divergent authoritative state.",
                "Recovery escalates authority.",
                "Duplicate execution or evidence is created.",
            ),
            evidence_requirements=(
                "Machine-readable replay and recovery evidence.",
                "Crash, interruption, corruption, and duplicate-request tests.",
                "Cumulative regression results.",
            ),
        ),
        CertificationBlock(
            block_id=CertificationBlockId.STAGE_12D,
            objective=(
                "Close the governed ecosystem and determine Golden Master readiness."
            ),
            required_inputs=(
                "Certified Stage 12A manifest.",
                "Certified Stage 12B boundary matrix.",
                "Certified Stage 12C replay and recovery evidence.",
                "Complete build, package, audit, and repository evidence.",
            ),
            required_outputs=(
                "Final ecosystem closure report.",
                "Golden Master readiness decision.",
                "Complete immutable evidence index.",
            ),
            pass_conditions=(
                "Stages 12A, 12B, and 12C are certified.",
                "All supported tests, lint, builds, packaging, and audits pass.",
                "The repository and generated artifacts are reproducible and clean.",
                "No unresolved release-blocking defect remains.",
            ),
            failure_conditions=(
                "Any prior Stage 12 block is incomplete.",
                "Any required validation fails.",
                "Evidence is incomplete or unreproducible.",
                "A release-blocking defect remains.",
            ),
            evidence_requirements=(
                "Final machine-readable certification report.",
                "Complete validation-command and result inventory.",
                "Golden Master readiness recommendation.",
            ),
        ),
    )


def build_authoritative_manifest() -> EcosystemCertificationManifest:
    return EcosystemCertificationManifest(
        program_name="Sigil Beta governed ecosystem certification",
        certification_stage="12A",
        certification_scope="post_phase9_stage12_ecosystem_certification",
        source_baseline=(
            "Stage 1 through Stage 11 implementation ending at commit "
            "8e3694b87; source identity must be independently verified by the "
            "operator and is not derived at import time."
        ),
        creation_policy=(
            "The manifest contains no runtime-generated timestamp; canonical "
            "identity excludes host, process, locale, timezone, and insertion order."
        ),
        components=build_authoritative_components(),
        boundaries=build_authoritative_boundaries(),
        invariants=build_authoritative_invariants(),
        certification_blocks=build_stage12_blocks(),
    )
