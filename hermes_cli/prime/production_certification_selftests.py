"""Production-grade selftests for the ``certify_fleet`` boolean inputs.

Fleet Unification live-runtime work. :func:`hermes_cli.prime.certification.certify_fleet`
requires six boolean parameters
(``identity_registry_conflict_free``, ``event_schema_valid``,
``health_protocol_compatible``, ``admission_default_deny_selftest_passed``,
``sigil_contract_restrictions_selftest_passed``,
``remote_maintenance_default_deny_selftest_passed``) that, before this
module, had no production implementation anywhere in the repository — only
``tests/hermes_cli/test_prime/test_certification.py`` supplied them, and only
as hardcoded ``True`` fixture values. Passing hardcoded ``True`` from a real
CLI would certify a fleet without ever having checked anything, which is
exactly the "fabricated evidence" failure mode fleet certification exists to
prevent.

Every function below actually drives the real module it names and returns
``True`` only if every assertion inside it held — the same discipline
:mod:`hermes_cli.prime.live_runtime_certification` already uses for its five
``live_runtime_*`` booleans. ``sigil_contract_restrictions_selftest`` is a
thin wrapper around that module's existing ``sigil_isolation_selftest``
rather than a duplicate, since that check already covers the exact same
ground (Sigil contract safety-field locks + unadmitted-caller rejection).

``remote_maintenance_default_deny_selftest`` deliberately covers only the
default-deny (negative) path — proving a request with no supplied admission
is denied — rather than also constructing a full positive-admission case,
which would require building a complete, checksum-matched ``RepairProposal``/
``RepairApproval`` pair from :mod:`hermes_cli.agent_roles.remote_maintenance`
that is unrelated to what "default deny" asks. The name matches the scope.
"""

from __future__ import annotations

import time

from hermes_cli.prime.admission import (
    AdmissionRequest,
    CertificationStatus as AdmissionCertificationStatus,
    PrimeAdmissionService,
)
from hermes_cli.prime.evidence import EVIDENCE_SCHEMA_VERSION, EvidenceRecord, SensitivityTier
from hermes_cli.prime.health import (
    DEFAULT_MAX_REPORT_AGE_SECONDS,
    HEALTH_PROTOCOL_VERSION,
    HealthReport,
    LivenessState,
    ReadinessState,
    evaluate_health,
)
from hermes_cli.prime.identity import (
    FleetIdentity,
    IdentityConflictError,
    IdentityKind,
    IdentityRegistry,
    IdentitySource,
)
from hermes_cli.prime.live_runtime_certification import sigil_isolation_selftest
from hermes_cli.prime.remote_maintenance_governance import (
    evaluate_maintenance_request,
)


def _now() -> int:
    return int(time.time())


def identity_registry_conflict_free_selftest() -> bool:
    """Prove :class:`IdentityRegistry` actually detects a colliding identity.

    Real assertions: re-registering the identical identity from the same
    source is idempotent (no conflict); registering a different source under
    the same ``(kind, natural_key)`` without ``allow_supersede`` raises
    :class:`IdentityConflictError` rather than silently overwriting it.
    """
    try:
        registry = IdentityRegistry()
        now = _now()
        original = FleetIdentity(
            kind=IdentityKind.NODE,
            natural_key="selftest-node",
            source=IdentitySource.NATIVE,
            source_reference="production_certification_selftests:original",
            registered_at=now,
        )
        registry.register(original)

        # Same source/source_reference re-registered — must not raise.
        registry.register(original)

        colliding = FleetIdentity(
            kind=IdentityKind.NODE,
            natural_key="selftest-node",
            source=IdentitySource.SIGIL_FLEET,
            source_reference="production_certification_selftests:colliding",
            registered_at=now,
        )
        try:
            registry.register(colliding)
            return False  # a genuine cross-source collision must have raised
        except IdentityConflictError:
            pass

        # allow_supersede=True must still be able to intentionally replace it.
        registry.register(colliding, allow_supersede=True)
        resolved = registry.resolve(IdentityKind.NODE, "selftest-node")
        if resolved is None or resolved.source != IdentitySource.SIGIL_FLEET:
            return False
        return True
    except Exception:  # noqa: BLE001 - any unexpected exception is a failed selftest
        return False


def event_schema_valid_selftest() -> bool:
    """Prove the evidence/event schema-version gate actually rejects drift.

    Real assertions: a real :class:`EvidenceRecord` built via ``.build()``
    carries the current, supported schema version; constructing one with an
    unsupported version explicitly is rejected by its validator rather than
    silently accepted.
    """
    try:
        record = EvidenceRecord.build(
            kind="selftest_event",
            producer_identity_id="prime-certification-selftest",
            subject_identity_id=None,
            provenance="production_certification_selftests",
            timestamp=_now(),
            redacted_summary="event schema selftest record",
            sensitivity=SensitivityTier.INTERNAL,
        )
        if record.schema_version != EVIDENCE_SCHEMA_VERSION:
            return False

        try:
            EvidenceRecord(
                evidence_id="pevd_selftest",
                schema_version=EVIDENCE_SCHEMA_VERSION + 999,
                kind="selftest_event",
                producer_identity_id="prime-certification-selftest",
                subject_identity_id=None,
                provenance="production_certification_selftests",
                timestamp=_now(),
                redacted_summary="must be rejected",
                content_hash="0" * 64,
            )
            return False  # an unsupported schema version must have raised
        except ValueError:
            return True
    except Exception:  # noqa: BLE001
        return False


def health_protocol_compatible_selftest() -> bool:
    """Prove the health protocol version gate actually rejects incompatibility.

    Real assertions: a current-version, fully healthy report evaluates clean
    (no findings); a report carrying an unsupported protocol version is
    flagged ``UNSUPPORTED_VERSION`` by :func:`evaluate_health`, never
    silently treated as compatible.
    """
    try:
        now = _now()
        healthy = HealthReport(
            report_id="health_selftest_ok",
            subject_identity_id="fid_selftest",
            protocol_version=HEALTH_PROTOCOL_VERSION,
            observed_at=now,
            expires_at=now + DEFAULT_MAX_REPORT_AGE_SECONDS,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
        )
        if evaluate_health(healthy, now=now) != ():
            return False

        # HealthReport's own validator only accepts supported versions, so an
        # incompatible version must be constructed via model_construct to
        # bypass validation and exercise evaluate_health's own defense.
        incompatible = HealthReport.model_construct(
            report_id="health_selftest_bad_version",
            subject_identity_id="fid_selftest",
            protocol_version=HEALTH_PROTOCOL_VERSION + 999,
            observed_at=now,
            expires_at=now + DEFAULT_MAX_REPORT_AGE_SECONDS,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
            dependency_health={},
            reason_codes=(),
            checks=(),
            evidence_refs=(),
        )
        findings = evaluate_health(incompatible, now=now)
        return len(findings) == 1 and findings[0].value == "unsupported_version"
    except Exception:  # noqa: BLE001
        return False


def admission_default_deny_selftest() -> bool:
    """Prove :class:`PrimeAdmissionService` actually defaults to denied.

    Real assertions: an unknown/inactive identity is denied with
    ``identity_unknown_or_inactive``; the identical request shape, but with
    every precondition genuinely satisfied (known+active, healthy,
    certified), is admitted — proving the negative case isn't merely a
    service that always says no.
    """
    try:
        service = PrimeAdmissionService()
        now = _now()

        unknown_request = AdmissionRequest(
            request_id="selftest-admission-unknown",
            subject_identity_id="fid_selftest_unknown",
            role="titan",
            software_version="1.0.0",
            protocol_version=1,
            health=None,
            certification_status=AdmissionCertificationStatus.UNKNOWN,
            policy_version="prime-admission-policy-v1",
            identity_known_and_active=False,
            identity_revoked=False,
            quarantined=False,
            requested_at=now,
        )
        denied = service.evaluate(unknown_request, now=now)
        if denied.outcome.value != "denied" or "identity_unknown_or_inactive" not in denied.reason_codes:
            return False

        healthy_report = HealthReport(
            report_id="health_selftest_admission",
            subject_identity_id="fid_selftest_known",
            observed_at=now,
            expires_at=now + DEFAULT_MAX_REPORT_AGE_SECONDS,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
        )
        valid_request = AdmissionRequest(
            request_id="selftest-admission-valid",
            subject_identity_id="fid_selftest_known",
            role="titan",
            software_version="1.0.0",
            protocol_version=1,
            health=healthy_report,
            certification_status=AdmissionCertificationStatus.CERTIFIED,
            certification_evidence_ref="evidence://selftest-admission",
            policy_version="prime-admission-policy-v1",
            identity_known_and_active=True,
            identity_revoked=False,
            quarantined=False,
            requested_at=now,
        )
        admitted = service.evaluate(valid_request, now=now)
        return admitted.outcome.value == "admitted" and admitted.is_current(now)
    except Exception:  # noqa: BLE001
        return False


def sigil_contract_restrictions_selftest() -> bool:
    """Prove Sigil contract safety-field locks and admission gating hold.

    Delegates to :func:`hermes_cli.prime.live_runtime_certification.sigil_isolation_selftest`,
    which already drives real ``SigilContractRequest`` construction (rejecting
    any attempt to unset ``paper_only``/``broker_submission_denied``/
    ``execution_authority_denied``/``advisory``) and confirms an unadmitted
    caller is never admitted — the exact ground this selftest's name covers.
    """
    return sigil_isolation_selftest()


def remote_maintenance_default_deny_selftest() -> bool:
    """Prove remote-maintenance governance actually defaults to denied.

    Real assertion: :func:`evaluate_maintenance_request` denies a request
    when no requester/target admission or health was supplied at all — the
    default-deny path this selftest is named for. See module docstring for
    why the positive (fully-admitted) path is out of scope here.
    """
    try:
        from hermes_cli.agent_roles.remote_maintenance import (
            CommandMode,
            RepairProposal,
            RepairStep,
            RiskLevel,
        )
        from hermes_cli.prime.remote_maintenance_governance import (
            GovernedMaintenanceRequest,
            MaintenanceWindow,
        )

        now = _now()
        step = RepairStep(
            step_id="selftest-step",
            command_id="service_state",
            mode=CommandMode.READ_ONLY,
            rollback_command_id="service_state",
        )
        proposal = RepairProposal.build(
            target_id="selftest-target",
            risk=RiskLevel.LOW,
            expected_downtime="none",
            finding_refs=(),
            steps=(step,),
            evidence_refs=(),
        )
        request = GovernedMaintenanceRequest(
            request_id="selftest-maintenance",
            correlation_id="selftest-maintenance-corr",
            requester_identity_id="fid_selftest_requester",
            target_identity_id="fid_selftest_target",
            proposal=proposal,
            approvals=(),
            approval_issued_at=(),
            window=MaintenanceWindow(starts_at=now - 60, ends_at=now + 3600),
            dry_run=True,
            requested_at=now,
        )
        decision = evaluate_maintenance_request(
            request,
            requester_admission=None,
            requester_health=None,
            target_admission=None,
            target_health=None,
            now=now,
        )
        return (
            decision.outcome.value == "denied"
            and "requester_not_admitted" in decision.reason_codes
            and "target_not_admitted" in decision.reason_codes
        )
    except Exception:  # noqa: BLE001
        return False


def run_all_production_certification_selftests() -> dict[str, bool]:
    """Run every production certification selftest; return name->passed."""
    return {
        "identity_registry_conflict_free": identity_registry_conflict_free_selftest(),
        "event_schema_valid": event_schema_valid_selftest(),
        "health_protocol_compatible": health_protocol_compatible_selftest(),
        "admission_default_deny": admission_default_deny_selftest(),
        "sigil_contract_restrictions": sigil_contract_restrictions_selftest(),
        "remote_maintenance_default_deny": remote_maintenance_default_deny_selftest(),
    }
