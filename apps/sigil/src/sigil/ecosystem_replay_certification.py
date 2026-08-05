"""Deterministic Stage 12C replay and recovery certification.

This module certifies existing replay, persistence, recovery, quarantine,
idempotency, and duplicate-prevention behavior.

It performs no networking, subprocess execution, broker submission,
credential resolution, runtime mutation, installation, activation, dispatch,
or financial execution.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from sigil.ai.registry import canonical_digest
from sigil.ecosystem_boundary_certification import (
    build_authoritative_boundary_matrix,
)


REPLAY_CERTIFICATION_SCHEMA_VERSION = 1


class ReplayCertificationStatus(str, Enum):
    CERTIFIED = "certified"
    NOT_CERTIFIED = "not_certified"


class ReplayCertificationValidationError(ValueError):
    """Stage 12C replay and recovery certification failed closed."""


@dataclass(frozen=True, slots=True)
class ReplayRecoveryProof:
    proof_id: str
    category: str
    deterministic_replay_proven: bool
    recovery_proven: bool
    corruption_fails_closed_proven: bool
    duplicate_prevention_proven: bool
    authority_non_escalation_proven: bool
    side_effect_free_replay_proven: bool
    paper_only_proven: bool
    broker_submission_disabled_proven: bool
    implementation_references: tuple[str, ...]
    test_references: tuple[str, ...]
    proof_summary: str
    status: ReplayCertificationStatus

    def __post_init__(self) -> None:
        if not self.proof_id.strip():
            raise ReplayCertificationValidationError(
                "replay proof ID is required"
            )
        if not self.category.strip():
            raise ReplayCertificationValidationError(
                "replay proof category is required"
            )
        if not self.proof_summary.strip():
            raise ReplayCertificationValidationError(
                "replay proof summary is required"
            )
        if not isinstance(self.status, ReplayCertificationStatus):
            raise ReplayCertificationValidationError(
                "unknown replay certification status"
            )

        for label, references in {
            "implementation references": self.implementation_references,
            "test references": self.test_references,
        }.items():
            if not references:
                raise ReplayCertificationValidationError(
                    f"replay {label} are required"
                )
            if any(
                not reference
                or reference.startswith("/")
                or ".." in Path(reference).parts
                for reference in references
            ):
                raise ReplayCertificationValidationError(
                    f"invalid replay {label}"
                )

        required_results = (
            self.deterministic_replay_proven,
            self.recovery_proven,
            self.corruption_fails_closed_proven,
            self.duplicate_prevention_proven,
            self.authority_non_escalation_proven,
            self.side_effect_free_replay_proven,
            self.paper_only_proven,
            self.broker_submission_disabled_proven,
        )

        expected = (
            ReplayCertificationStatus.CERTIFIED
            if all(required_results)
            else ReplayCertificationStatus.NOT_CERTIFIED
        )
        if self.status != expected:
            raise ReplayCertificationValidationError(
                "replay certification status contradicts proof results"
            )


_REQUIRED_PROOF_IDS = frozenset(
    {
        "worker-evidence-chain",
        "worker-corruption-detection",
        "paper-runtime-state-recovery",
        "paper-runtime-corruption-quarantine",
        "single-flight-duplicate-prevention",
        "interrupted-cycle-recovery",
        "atomic-paper-store-persistence",
        "paper-order-restart-recovery",
    }
)


@dataclass(frozen=True, slots=True)
class ReplayRecoveryCertification:
    source_boundary_matrix_identity: str
    certification_stage: str
    proofs: tuple[ReplayRecoveryProof, ...]
    paper_only: bool
    broker_submission: bool
    replay_external_side_effects: bool
    authority_escalation: bool
    feature_expansion_prohibited: bool
    schema_version: int = REPLAY_CERTIFICATION_SCHEMA_VERSION
    certification_identity: str = ""

    def __post_init__(self) -> None:
        canonical_proofs = tuple(
            sorted(self.proofs, key=lambda item: item.proof_id)
        )
        object.__setattr__(self, "proofs", canonical_proofs)

        if self.schema_version != REPLAY_CERTIFICATION_SCHEMA_VERSION:
            raise ReplayCertificationValidationError(
                "unsupported replay certification schema version"
            )
        if self.certification_stage != "12C":
            raise ReplayCertificationValidationError(
                "replay certification stage must be 12C"
            )
        if (
            not self.source_boundary_matrix_identity.startswith("sha256:")
            or len(self.source_boundary_matrix_identity) != 71
        ):
            raise ReplayCertificationValidationError(
                "source boundary matrix identity must be a SHA-256 identity"
            )

        proof_ids = tuple(proof.proof_id for proof in self.proofs)
        if len(proof_ids) != len(set(proof_ids)):
            raise ReplayCertificationValidationError(
                "duplicate replay proof ID"
            )

        proof_id_set = set(proof_ids)
        if proof_id_set != _REQUIRED_PROOF_IDS:
            missing = sorted(_REQUIRED_PROOF_IDS - proof_id_set)
            unexpected = sorted(proof_id_set - _REQUIRED_PROOF_IDS)
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unexpected:
                details.append("unexpected " + ", ".join(unexpected))
            raise ReplayCertificationValidationError(
                "replay proof inventory mismatch: " + "; ".join(details)
            )

        boundary_matrix = build_authoritative_boundary_matrix()
        if (
            self.source_boundary_matrix_identity
            != boundary_matrix.matrix_identity
        ):
            raise ReplayCertificationValidationError(
                "Stage 12B boundary matrix identity mismatch"
            )

        if not self.paper_only:
            raise ReplayCertificationValidationError(
                "Stage 12C must remain paper-only"
            )
        if self.broker_submission:
            raise ReplayCertificationValidationError(
                "broker submission must remain disabled"
            )
        if self.replay_external_side_effects:
            raise ReplayCertificationValidationError(
                "replay must not invoke external side effects"
            )
        if self.authority_escalation:
            raise ReplayCertificationValidationError(
                "replay and recovery must not escalate authority"
            )
        if not self.feature_expansion_prohibited:
            raise ReplayCertificationValidationError(
                "Stage 12 feature expansion must remain prohibited"
            )
        if any(
            proof.status != ReplayCertificationStatus.CERTIFIED
            for proof in self.proofs
        ):
            raise ReplayCertificationValidationError(
                "all Stage 12C replay proofs must be certified"
            )

        expected = self.expected_identity()
        if (
            self.certification_identity
            and self.certification_identity != expected
        ):
            raise ReplayCertificationValidationError(
                "replay certification identity mismatch"
            )
        if not self.certification_identity:
            object.__setattr__(
                self,
                "certification_identity",
                expected,
            )

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("certification_identity", None)
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
            )
            for reference in references:
                if not (repository_root / reference).is_file():
                    errors.append(
                        f"missing replay proof reference for "
                        f"{proof.proof_id}: {reference}"
                    )

        return tuple(sorted(set(errors)))

    @property
    def certified(self) -> bool:
        return (
            self.paper_only
            and not self.broker_submission
            and not self.replay_external_side_effects
            and not self.authority_escalation
            and self.feature_expansion_prohibited
            and all(
                proof.status == ReplayCertificationStatus.CERTIFIED
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
    *,
    proof_id: str,
    category: str,
    implementation_references: tuple[str, ...],
    test_references: tuple[str, ...],
    summary: str,
) -> ReplayRecoveryProof:
    return ReplayRecoveryProof(
        proof_id=proof_id,
        category=category,
        deterministic_replay_proven=True,
        recovery_proven=True,
        corruption_fails_closed_proven=True,
        duplicate_prevention_proven=True,
        authority_non_escalation_proven=True,
        side_effect_free_replay_proven=True,
        paper_only_proven=True,
        broker_submission_disabled_proven=True,
        implementation_references=implementation_references,
        test_references=test_references,
        proof_summary=summary,
        status=ReplayCertificationStatus.CERTIFIED,
    )


def build_authoritative_replay_certification() -> ReplayRecoveryCertification:
    boundary_matrix = build_authoritative_boundary_matrix()

    proofs = (
        _proof(
            proof_id="worker-evidence-chain",
            category="evidence_chain",
            implementation_references=(
                "apps/sigil/src/sigil/worker_contract.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_worker_contract.py",
            ),
            summary=(
                "Worker transition evidence is chained through canonical "
                "previous-record and entry hashes."
            ),
        ),
        _proof(
            proof_id="worker-corruption-detection",
            category="corruption_detection",
            implementation_references=(
                "apps/sigil/src/sigil/worker_contract.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_worker_contract.py",
            ),
            summary=(
                "Malformed, reordered, truncated, or hash-mismatched worker "
                "evidence fails closed."
            ),
        ),
        _proof(
            proof_id="paper-runtime-state-recovery",
            category="restart_recovery",
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paper_runtime_state_recovery.py",
                "apps/sigil/tests/test_governed_paper_runtime_execution.py",
            ),
            summary=(
                "Persisted paper runtime state restores deterministically "
                "without enabling broker submission."
            ),
        ),
        _proof(
            proof_id="paper-runtime-corruption-quarantine",
            category="quarantine",
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paper_runtime_state_recovery.py",
            ),
            summary=(
                "Malformed or checksum-invalid runtime state is quarantined "
                "and replaced by a safe paper-only state."
            ),
        ),
        _proof(
            proof_id="single-flight-duplicate-prevention",
            category="idempotency",
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paper_single_flight_execution.py",
            ),
            summary=(
                "Repeated starts, active persisted claims, and duplicate "
                "proposals cannot create duplicate execution."
            ),
        ),
        _proof(
            proof_id="interrupted-cycle-recovery",
            category="interruption_recovery",
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
            ),
            test_references=(
                "apps/sigil/tests/test_paper_single_flight_execution.py",
            ),
            summary=(
                "Stale cycle claims recover to paused safety state without "
                "broker submission, automatic resume, or authority escalation."
            ),
        ),
        _proof(
            proof_id="atomic-paper-store-persistence",
            category="atomic_persistence",
            implementation_references=(
                "apps/sigil/src/sigil/autonomous_paper/store.py",
            ),
            test_references=(
                "apps/sigil/tests/test_autonomous_alpaca_paper_execution.py",
            ),
            summary=(
                "Paper execution state uses canonical checksums, atomic "
                "replacement, file locking, and fail-closed integrity checks."
            ),
        ),
        _proof(
            proof_id="paper-order-restart-recovery",
            category="financial_state_recovery",
            implementation_references=(
                "apps/sigil/src/sigil/desktop_bridge/runtime.py",
                "apps/sigil/src/sigil/desktop_bridge/paper_execution.py",
            ),
            test_references=(
                "apps/sigil/tests/test_governed_paper_runtime_execution.py",
            ),
            summary=(
                "Orders, fills, balances, reserved cash, positions, and audit "
                "state survive restart deterministically in the local simulator."
            ),
        ),
    )

    return ReplayRecoveryCertification(
        source_boundary_matrix_identity=boundary_matrix.matrix_identity,
        certification_stage="12C",
        proofs=proofs,
        paper_only=True,
        broker_submission=False,
        replay_external_side_effects=False,
        authority_escalation=False,
        feature_expansion_prohibited=True,
    )
