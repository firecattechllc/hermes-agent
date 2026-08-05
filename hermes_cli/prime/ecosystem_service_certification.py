"""Ecosystem-service self-tests for fleet certification.

Fleet Unification ecosystem-services work. Mirrors the convention already
established for ``hermes_cli.prime.certification.certify_fleet`` (a pure
function of caller-supplied booleans): this module is where those booleans
come from for the ecosystem service registry. Every function here drives
:mod:`hermes_cli.prime.service_registry` and, where relevant, the real
``sigil.self_evolution`` module against ephemeral state, and asserts the
specific fail-closed outcome each is supposed to produce — never returning
a fixed ``True``.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from hermes_cli.prime.service_registry import (
    KNOWN_ECOSYSTEM_SERVICES,
    EcosystemServiceRegistry,
    EcosystemServiceRegistryStore,
    ServiceInstallationStatus,
    ServiceRegistrationOutcome,
    ServiceRegistrationRejectionCode,
    VerifiedExternalSource,
    validate_external_source,
)


def _now() -> int:
    return int(time.time())


def ecosystem_services_no_unsafe_drift_selftest() -> bool:
    """CRITICAL / core-safety selftest: no *registered* ecosystem service has
    drifted to an unsafe (accidentally-enabled) state, and none is
    dispatchable or certification-gate-met.

    Deliberately does not require every known service to still be
    *present* — a missing/renamed optional adapter module is an
    availability concern (see :func:`ecosystem_services_availability_selftest`),
    not a core-safety one. This check only ever fails on the safety-critical
    condition: something that exists reporting itself as unsafe, dispatchable,
    or gate-met when it should never be able to.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="ecosystem-certify-") as tmp:
            registry = EcosystemServiceRegistry(
                store=EcosystemServiceRegistryStore(state_root=Path(tmp) / "prime")
            )
            now = _now()
            records = registry.register_all_known_services(now=now)
            for record in records:
                if record.installation_status == ServiceInstallationStatus.UNSAFE:
                    return False
                if record.is_dispatchable():
                    return False  # must never be dispatchable while ungated
                if record.certification_gate_met:
                    return False
            return True
    except Exception:  # noqa: BLE001
        return False


def ecosystem_services_availability_selftest() -> bool:
    """Optional-service availability selftest: every service in the known
    catalog is currently real, importable, and confirmed disabled.

    This is intentionally kept separate from — and, in
    :func:`hermes_cli.prime.certification.certify_fleet`, weighted less
    severely than — :func:`ecosystem_services_no_unsafe_drift_selftest`.
    These 8 services are optional components; one of them going missing
    (e.g. a future refactor renames or removes an adapter module) is a
    real, worth-surfacing regression, but must never be allowed to block
    certification with the same severity as an actual core-safety failure
    — see the task's own requirement that "one failed optional service"
    must never "falsely mark the entire fleet healthy" *or* be conflated
    with "failed required core service" failures.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="ecosystem-certify-") as tmp:
            registry = EcosystemServiceRegistry(
                store=EcosystemServiceRegistryStore(state_root=Path(tmp) / "prime")
            )
            now = _now()
            records = registry.register_all_known_services(now=now)
            if len(records) != len(KNOWN_ECOSYSTEM_SERVICES):
                return False
            return all(
                record.installation_status == ServiceInstallationStatus.PRESENT_DISABLED
                for record in records
            )
    except Exception:  # noqa: BLE001
        return False


def unverified_service_rejection_selftest() -> bool:
    """An unknown service key and an unverified external source are both
    rejected — no arbitrary identity can enter the registry."""
    try:
        with tempfile.TemporaryDirectory(prefix="ecosystem-certify-") as tmp:
            registry = EcosystemServiceRegistry(
                store=EcosystemServiceRegistryStore(state_root=Path(tmp) / "prime")
            )
            now = _now()

            outcome, record, rejection = registry.register_known_service(
                "totally-unverified-service", now=now
            )
            if (
                outcome != ServiceRegistrationOutcome.REJECTED
                or rejection != ServiceRegistrationRejectionCode.UNKNOWN_SERVICE_KEY
            ):
                return False

            outcome, record, rejection = registry.register_external_service(
                "paperclip", external_source=None, now=now
            )
            if (
                outcome != ServiceRegistrationOutcome.REJECTED
                or rejection != ServiceRegistrationRejectionCode.UNVERIFIED_EXTERNAL_SOURCE
            ):
                return False

            unclear_license = VerifiedExternalSource(
                repository_url="https://github.com/example/paperclip",
                revision="a" * 40,
                license_spdx_id="unknown",
                integrity_sha256="a" * 64,
                verified_by="selftest",
                verified_at=now,
            )
            ok, _reason = validate_external_source(unclear_license)
            if ok:
                return False
            return True
    except Exception:  # noqa: BLE001
        return False


def duplicate_and_revoked_service_rejection_selftest() -> bool:
    """Duplicate registration and revoked-service re-registration are both
    rejected."""
    try:
        with tempfile.TemporaryDirectory(prefix="ecosystem-certify-") as tmp:
            registry = EcosystemServiceRegistry(
                store=EcosystemServiceRegistryStore(state_root=Path(tmp) / "prime")
            )
            now = _now()
            registry.register_known_service("agent_reach", now=now)

            outcome, _record, rejection = registry.register_known_service("agent_reach", now=now)
            if (
                outcome != ServiceRegistrationOutcome.REJECTED
                or rejection != ServiceRegistrationRejectionCode.DUPLICATE_REGISTRATION
            ):
                return False

            registry.revoke("agent_reach", now=now, reason="selftest")
            outcome, _record, rejection = registry.register_known_service(
                "agent_reach", now=now, allow_reregistration=True
            )
            if (
                outcome != ServiceRegistrationOutcome.REJECTED
                or rejection != ServiceRegistrationRejectionCode.REVOKED
            ):
                return False
            return True
    except Exception:  # noqa: BLE001
        return False


def _utc_timestamp() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def self_evolution_self_approval_guard_selftest() -> bool:
    """Real assertion against the actual ``sigil.self_evolution`` module: a
    proposer's own approval never counts toward the independent-review
    requirement (``assess_promotion_readiness`` excludes any review whose
    ``reviewer_identity`` equals the proposal's ``created_by``)."""
    try:
        from hermes_cli.prime.service_registry import import_ecosystem_module

        se = import_ecosystem_module("sigil.self_evolution")

        now = _utc_timestamp()
        evidence = se.EvolutionEvidenceRef(
            evidence_id="ev-selftest",
            kind="observation",
            content_digest="sha256:" + "a" * 64,
            provenance="selftest",
            observed_at=now,
            reference="tests/selftest/evidence.md",
        )
        opportunity = se.ImprovementOpportunity(
            opportunity_id="opp-selftest",
            category=se.ImprovementCategory.PERFORMANCE,
            title="selftest opportunity",
            problem_statement="selftest problem",
            affected_components=("component-a",),
            affected_integrations=(),
            observed_at=now,
            evidence=(evidence,),
        )
        budget = se.EvolutionBudget(
            maximum_cost_usd="0.00",
            maximum_runtime_seconds=60,
            maximum_attempts=1,
            maximum_compute_units=1,
            maximum_input_bytes=1024,
            maximum_output_bytes=1024,
        )
        risk = se.RiskAssessment(
            level=se.RiskLevel.LOW,
            risk_factors=("factor-a",),
            blast_radius=("isolated-sandbox",),
            mitigations=("mitigation-a",),
            requires_security_review=False,
            requires_financial_review=False,
        )
        experiment = se.ExperimentPlan(
            experiment_id="exp-selftest",
            hypothesis="selftest hypothesis",
            control_description="control",
            treatment_description="treatment",
            success_metrics=("metric-a",),
            guardrail_metrics=("guardrail-a",),
            required_tests=("test-a",),
            certification_requirements=("cert-a",),
            budget=budget,
            isolated=True,
            paper_only=True,
        )
        rollback = se.RollbackPlan(
            rollback_id="rb-selftest",
            trigger_conditions=("condition-a",),
            rollback_steps=("step-a",),
            verification_tests=("test-a",),
            maximum_recovery_seconds=60,
        )
        proposal = se.ImprovementProposal(
            proposal_id="proposal-selftest",
            opportunity_id=opportunity.opportunity_id,
            opportunity_digest=opportunity.opportunity_digest,
            title="selftest proposal",
            summary="selftest summary",
            expected_benefits=("benefit-a",),
            affected_components=("component-a",),
            affected_integrations=(),
            risk=risk,
            experiment=experiment,
            rollback=rollback,
            minimum_independent_reviews=1,
            created_at=now,
            created_by="agent-proposer",
        )

        self_approval = se.IndependentReview(
            review_id="review-self",
            reviewer_identity="agent-proposer",  # same as created_by — must not count
            reviewed_at=now,
            decision=se.ReviewDecision.APPROVED,
            scope=("proposal",),
            evidence_digest="sha256:" + "b" * 64,
            comments_reference="tests/selftest/comments.md",
        )
        assessment = se.assess_promotion_readiness(
            proposal,
            reviews=(self_approval,),
            result=None,
            evidence_complete=True,
            certification_results={},
        )
        if assessment.readiness == se.PromotionReadiness.READY:
            return False  # self-approval must never be sufficient
        if assessment.can_promote:
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


def ecosystem_evidence_integrity_selftest() -> bool:
    """Evidence produced by ecosystem-service registration is real,
    hash-chained, and tamper-evident — not merely trusted."""
    try:
        from hermes_cli.mission_control.service import MissionControlService
        from hermes_cli.mission_control.store import MissionControlStore
        from hermes_cli.prime.evidence import EvidenceStorageError, PrimeEvidenceStore
        from hermes_cli.prime.visibility import PrimeVisibilityService

        with tempfile.TemporaryDirectory(prefix="ecosystem-certify-evidence-") as tmp:
            root = Path(tmp)
            evidence_store = PrimeEvidenceStore(state_root=root / "evidence")
            mission_control = MissionControlService(store=MissionControlStore(root=root / "mc"))
            visibility = PrimeVisibilityService(mission_control, evidence_store)
            registry = EcosystemServiceRegistry(
                store=EcosystemServiceRegistryStore(state_root=root / "prime")
            )
            now = _now()
            outcome, record, rejection = registry.register_known_service("hermes_wiki", now=now)
            visibility.publish_service_registration(
                "selftest", service_key="hermes_wiki", outcome=outcome, record=record,
                rejection_code=rejection, now=now,
            )
            if not evidence_store.verify_chain():
                return False

            raw = evidence_store.evidence_path.read_text(encoding="utf-8").splitlines()
            tampered = raw[:-1] + [raw[-1].replace('"sequence":1', '"sequence":99')]
            evidence_store.evidence_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
            try:
                evidence_store.verify_chain()
                return False  # must have raised
            except EvidenceStorageError:
                return True
    except Exception:  # noqa: BLE001
        return False


def run_all_ecosystem_service_selftests() -> dict[str, bool]:
    return {
        "ecosystem_services_no_unsafe_drift": ecosystem_services_no_unsafe_drift_selftest(),
        "ecosystem_services_availability": ecosystem_services_availability_selftest(),
        "unverified_service_rejection": unverified_service_rejection_selftest(),
        "duplicate_and_revoked_service_rejection": duplicate_and_revoked_service_rejection_selftest(),
        "self_evolution_self_approval_guard": self_evolution_self_approval_guard_selftest(),
        "ecosystem_evidence_integrity": ecosystem_evidence_integrity_selftest(),
    }
