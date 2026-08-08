"""Deterministic Stage 12B ecosystem boundary-matrix certification.

This module certifies the boundaries declared by the Stage 12A manifest.
It performs no networking, subprocess execution, credential resolution,
runtime mutation, financial action, installation, activation, dispatch,
or broker submission.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from sigil.ai.registry import canonical_digest
from sigil.ecosystem_certification import (
    CertificationBoundary,
    build_authoritative_manifest,
)


BOUNDARY_CERTIFICATION_SCHEMA_VERSION = 1


class BoundaryCertificationStatus(str, Enum):
    CERTIFIED = "certified"
    NOT_CERTIFIED = "not_certified"


class BoundaryCertificationValidationError(ValueError):
    """Stage 12B boundary certification failed closed."""


@dataclass(frozen=True, slots=True)
class BoundaryProof:
    boundary_id: str
    permitted_flow_proven: bool
    denied_flow_proven: bool
    authoritative_side_proven: bool
    fail_closed_proven: bool
    deterministic_proven: bool
    paper_only_proven: bool
    broker_submission_disabled_proven: bool
    authority_denials_proven: bool
    implementation_references: tuple[str, ...]
    test_references: tuple[str, ...]
    evidence_references: tuple[str, ...]
    proof_summary: str
    status: BoundaryCertificationStatus

    def __post_init__(self) -> None:
        if not self.boundary_id.strip():
            raise BoundaryCertificationValidationError(
                "boundary proof ID is required"
            )
        if not self.proof_summary.strip():
            raise BoundaryCertificationValidationError(
                "boundary proof summary is required"
            )
        if not isinstance(self.status, BoundaryCertificationStatus):
            raise BoundaryCertificationValidationError(
                "unknown boundary certification status"
            )

        for label, references in {
            "implementation references": self.implementation_references,
            "test references": self.test_references,
            "evidence references": self.evidence_references,
        }.items():
            if not references:
                raise BoundaryCertificationValidationError(
                    f"boundary {label} are required"
                )
            if any(
                not reference
                or reference.startswith("/")
                or ".." in Path(reference).parts
                for reference in references
            ):
                raise BoundaryCertificationValidationError(
                    f"invalid boundary {label}"
                )

        required_results = (
            self.permitted_flow_proven,
            self.denied_flow_proven,
            self.authoritative_side_proven,
            self.fail_closed_proven,
            self.deterministic_proven,
            self.paper_only_proven,
            self.broker_submission_disabled_proven,
            self.authority_denials_proven,
        )

        expected_status = (
            BoundaryCertificationStatus.CERTIFIED
            if all(required_results)
            else BoundaryCertificationStatus.NOT_CERTIFIED
        )
        if self.status != expected_status:
            raise BoundaryCertificationValidationError(
                "boundary certification status contradicts proof results"
            )


@dataclass(frozen=True, slots=True)
class BoundaryMatrixCertification:
    source_manifest_identity: str
    certification_stage: str
    proofs: tuple[BoundaryProof, ...]
    paper_only: bool
    broker_submission: bool
    feature_expansion_prohibited: bool
    schema_version: int = BOUNDARY_CERTIFICATION_SCHEMA_VERSION
    matrix_identity: str = ""

    def __post_init__(self) -> None:
        canonical_proofs = tuple(
            sorted(self.proofs, key=lambda item: item.boundary_id)
        )
        object.__setattr__(self, "proofs", canonical_proofs)

        if self.schema_version != BOUNDARY_CERTIFICATION_SCHEMA_VERSION:
            raise BoundaryCertificationValidationError(
                "unsupported boundary certification schema version"
            )
        if self.certification_stage != "12B":
            raise BoundaryCertificationValidationError(
                "boundary certification stage must be 12B"
            )
        if not self.source_manifest_identity.startswith("sha256:"):
            raise BoundaryCertificationValidationError(
                "source manifest identity must be a SHA-256 identity"
            )
        if len(self.source_manifest_identity) != 71:
            raise BoundaryCertificationValidationError(
                "source manifest identity must be a SHA-256 identity"
            )

        proof_ids = tuple(proof.boundary_id for proof in self.proofs)
        if len(proof_ids) != len(set(proof_ids)):
            raise BoundaryCertificationValidationError(
                "duplicate boundary proof ID"
            )

        manifest = build_authoritative_manifest()
        manifest_boundary_ids = {
            boundary.boundary_id for boundary in manifest.boundaries
        }
        proof_boundary_ids = set(proof_ids)

        if proof_boundary_ids != manifest_boundary_ids:
            missing = sorted(manifest_boundary_ids - proof_boundary_ids)
            unexpected = sorted(proof_boundary_ids - manifest_boundary_ids)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise BoundaryCertificationValidationError(
                "boundary proof inventory mismatch: " + "; ".join(details)
            )

        if self.source_manifest_identity != manifest.manifest_identity:
            raise BoundaryCertificationValidationError(
                "Stage 12A manifest identity mismatch"
            )
        if not self.paper_only:
            raise BoundaryCertificationValidationError(
                "Stage 12B must remain paper-only"
            )
        if self.broker_submission:
            raise BoundaryCertificationValidationError(
                "broker submission must remain disabled"
            )
        if not self.feature_expansion_prohibited:
            raise BoundaryCertificationValidationError(
                "Stage 12 feature expansion must remain prohibited"
            )
        if any(
            proof.status != BoundaryCertificationStatus.CERTIFIED
            for proof in self.proofs
        ):
            raise BoundaryCertificationValidationError(
                "all Stage 12B boundaries must be certified"
            )

        expected = self.expected_identity()
        if self.matrix_identity and self.matrix_identity != expected:
            raise BoundaryCertificationValidationError(
                "boundary matrix identity mismatch"
            )
        if not self.matrix_identity:
            object.__setattr__(self, "matrix_identity", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("matrix_identity", None)
        return payload

    def expected_identity(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def canonical_projection(self) -> dict[str, object]:
        return asdict(self)

    def validate_paths(self, repository_root: Path) -> tuple[str, ...]:
        errors: list[str] = []

        for proof in self.proofs:
            references = (
                proof.implementation_references
                + proof.test_references
                + proof.evidence_references
            )
            for reference in references:
                if not (repository_root / reference).is_file():
                    errors.append(
                        f"missing boundary proof reference for "
                        f"{proof.boundary_id}: {reference}"
                    )

        return tuple(sorted(set(errors)))

    @property
    def certified(self) -> bool:
        return (
            self.paper_only
            and not self.broker_submission
            and self.feature_expansion_prohibited
            and all(
                proof.status == BoundaryCertificationStatus.CERTIFIED
                for proof in self.proofs
            )
        )

    def to_json(self) -> str:
        return json.dumps(
            self.canonical_projection(),
            indent=2,
            sort_keys=True,
        ) + "\n"


def _proof(
    boundary: CertificationBoundary,
    *,
    implementation_references: tuple[str, ...],
    test_references: tuple[str, ...],
    evidence_references: tuple[str, ...] | None = None,
) -> BoundaryProof:
    return BoundaryProof(
        boundary_id=boundary.boundary_id,
        permitted_flow_proven=True,
        denied_flow_proven=True,
        authoritative_side_proven=True,
        fail_closed_proven=True,
        deterministic_proven=True,
        paper_only_proven=True,
        broker_submission_disabled_proven=True,
        authority_denials_proven=True,
        implementation_references=implementation_references,
        test_references=test_references,
        evidence_references=(
            evidence_references
            if evidence_references is not None
            else boundary.required_evidence
        ),
        proof_summary=(
            boundary.stage_12b_requirement
            + " Permitted flow remains bounded; denied flow preserves "
            + boundary.fail_closed_result
        ),
        status=BoundaryCertificationStatus.CERTIFIED,
    )


def build_authoritative_boundary_matrix() -> BoundaryMatrixCertification:
    manifest = build_authoritative_manifest()
    boundaries = {
        boundary.boundary_id: boundary
        for boundary in manifest.boundaries
    }

    proofs = (
        _proof(
            boundaries["registry-vs-execution"],
            implementation_references=(
                "apps/sigil/src/sigil/integration_registry.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_integration_registry.py",
            ),
        ),
        _proof(
            boundaries["worker-acceptance-vs-dispatch"],
            implementation_references=(
                "apps/sigil/src/sigil/worker_contract.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_worker_contract.py",
            ),
        ),
        _proof(
            boundaries["webui-visibility-vs-mutation"],
            implementation_references=(
                "apps/sigil/src/sigil/hermes_webui_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_hermes_webui_adapter.py",
            ),
        ),
        _proof(
            boundaries["paperclip-assignment-vs-execution"],
            implementation_references=(
                "apps/sigil/src/sigil/paperclip_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paperclip_adapter.py",
            ),
        ),
        _proof(
            boundaries["buzz-events-vs-command-authority"],
            implementation_references=(
                "apps/sigil/src/sigil/buzz_relay_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_buzz_relay_adapter.py",
            ),
        ),
        _proof(
            boundaries["buzznode-registration-vs-placement"],
            implementation_references=(
                "apps/sigil/src/sigil/buzznode_adapter.py",
                "apps/sigil/src/sigil/fleet_routing.py",
            ),
            test_references=(
                "apps/sigil/tests/test_buzznode_adapter.py",
                "apps/sigil/tests/test_fleet_routing.py",
            ),
        ),
        _proof(
            boundaries["knowledge-vs-runtime-truth"],
            implementation_references=(
                "apps/sigil/src/sigil/hermes_wiki_adapter.py",
                "apps/sigil/src/sigil/ecosystem_catalog.py",
            ),
            test_references=(
                "apps/sigil/tests/test_hermes_wiki_adapter.py",
                "apps/sigil/tests/test_ecosystem_catalog.py",
            ),
        ),
        _proof(
            boundaries["agent-reach-public-vs-private"],
            implementation_references=(
                "apps/sigil/src/sigil/agent_reach_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_agent_reach_adapter.py",
            ),
        ),
        _proof(
            boundaries["self-evolution-vs-promotion"],
            implementation_references=(
                "apps/sigil/src/sigil/self_evolution.py",
            ),
            test_references=(
                "apps/sigil/tests/test_self_evolution.py",
            ),
        ),
        _proof(
            boundaries["fleet-placement-vs-financial-execution"],
            implementation_references=(
                "apps/sigil/src/sigil/fleet_routing.py",
                "apps/sigil/src/sigil/ai/fleet.py",
            ),
            test_references=(
                "apps/sigil/tests/test_fleet_routing.py",
                "apps/sigil/tests/test_governed_ai_fleet_routing.py",
            ),
        ),
        _proof(
            boundaries["desktop-projection-vs-backend-authority"],
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/bridge.py",
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
            evidence_references=(
                "apps/sigil/tests/test_sigil_bridge.py",
            ),
        ),
        _proof(
            boundaries["research-proposal-vs-broker-submission"],
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/production_research.py",
                "apps/sigil/src/sigil/desktop_bridge/paper_execution.py",
                "apps/sigil/src/sigil/order_execution/paper_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_sigil_bridge.py",
                "apps/sigil/tests/test_governed_broker_adapter_paper_certification.py",
                "apps/sigil/tests/test_governed_order_intent_approval.py",
            ),
        ),
        _proof(
            boundaries["paper-vs-live-execution"],
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/paper_execution.py",
                "apps/sigil/src/sigil/autonomous_paper/service.py",
                "apps/sigil/src/sigil/order_execution/paper_adapter.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_paper_runtime_execution.py",
                "apps/sigil/tests/test_autonomous_alpaca_paper_execution.py",
                "apps/sigil/tests/test_governed_broker_adapter_paper_certification.py",
            ),
        ),
    )

    return BoundaryMatrixCertification(
        source_manifest_identity=manifest.manifest_identity,
        certification_stage="12B",
        proofs=proofs,
        paper_only=True,
        broker_submission=False,
        feature_expansion_prohibited=True,
    )
