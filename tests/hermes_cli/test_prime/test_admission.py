from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.admission import (
    AdmissionOutcome,
    AdmissionRequest,
    CertificationStatus,
    PrimeAdmissionService,
)
from hermes_cli.prime.health import HealthReport, LivenessState, ReadinessState


def _now() -> int:
    return int(time.time())


def _healthy_report(now: int) -> HealthReport:
    return HealthReport(
        report_id="health_1",
        subject_identity_id="fid_node_x",
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )


def _base_request(now: int, **overrides) -> AdmissionRequest:
    fields = dict(
        request_id="req1",
        subject_identity_id="fid_node_x",
        role="titan",
        software_version="1.0.0",
        protocol_version=1,
        health=_healthy_report(now),
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence_ref_1",
        policy_version="prime-admission-policy-v1",
        identity_known_and_active=True,
        identity_revoked=False,
        quarantined=False,
        requested_at=now,
    )
    fields.update(overrides)
    return AdmissionRequest(**fields)


def test_fully_valid_request_is_admitted() -> None:
    now = _now()
    decision = PrimeAdmissionService().evaluate(_base_request(now), now=now)
    assert decision.outcome == AdmissionOutcome.ADMITTED
    assert decision.reason_codes == ()


def test_unknown_identity_is_denied() -> None:
    now = _now()
    request = _base_request(now, identity_known_and_active=False)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "identity_unknown_or_inactive" in decision.reason_codes


def test_revoked_identity_cannot_also_be_active() -> None:
    now = _now()
    with pytest.raises(ValidationError):
        _base_request(now, identity_known_and_active=True, identity_revoked=True)


def test_revoked_identity_is_denied() -> None:
    now = _now()
    request = _base_request(now, identity_known_and_active=False, identity_revoked=True)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "identity_revoked" in decision.reason_codes


def test_quarantined_subject_is_quarantined_not_merely_denied() -> None:
    now = _now()
    request = _base_request(now, quarantined=True)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.QUARANTINED


def test_missing_health_report_denies() -> None:
    now = _now()
    request = _base_request(now, health=None)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "missing_health_report" in decision.reason_codes


def test_stale_health_denies_even_though_report_present() -> None:
    now = _now()
    stale = HealthReport(
        report_id="health_stale",
        subject_identity_id="fid_node_x",
        observed_at=now - 10_000,
        expires_at=now + 100_000,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    request = _base_request(now, health=stale)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "health_not_usable" in decision.reason_codes


def test_healthy_report_alone_is_insufficient_without_certification() -> None:
    now = _now()
    request = _base_request(
        now,
        certification_status=CertificationStatus.NOT_CERTIFIED,
        certification_evidence_ref=None,
    )
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "certification_status_not_certified" in decision.reason_codes


def test_certified_status_without_evidence_ref_denies() -> None:
    now = _now()
    request = _base_request(now, certification_evidence_ref=None)
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "missing_certification_evidence" in decision.reason_codes


def test_unsupported_policy_version_denies() -> None:
    now = _now()
    request = _base_request(now, policy_version="unknown-policy-v9")
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "unsupported_policy_version" in decision.reason_codes


def test_active_restrictions_deny() -> None:
    now = _now()
    request = _base_request(now, restrictions=("read_only",))
    decision = PrimeAdmissionService().evaluate(request, now=now)
    assert decision.outcome == AdmissionOutcome.DENIED
    assert "subject_has_active_restrictions" in decision.reason_codes


def test_admitted_decision_expires_and_requires_revalidation() -> None:
    now = _now()
    decision = PrimeAdmissionService().evaluate(
        _base_request(now), now=now, revalidation_seconds=100
    )
    assert decision.is_current(now)
    assert decision.is_current(now + 50)
    assert not decision.is_current(now + 101)


def test_denied_decision_requires_reason_codes_at_construction() -> None:
    from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome as O

    with pytest.raises(ValidationError):
        AdmissionDecision(
            decision_id="padm_x",
            request_id="req1",
            subject_identity_id="fid_node_x",
            outcome=O.DENIED,
            reason_codes=(),
            policy_version="prime-admission-policy-v1",
            decided_at=_now(),
            revalidate_after=_now() + 300,
        )


def test_decision_grants_no_execution_authority_marker() -> None:
    now = _now()
    decision = PrimeAdmissionService().evaluate(_base_request(now), now=now)
    assert decision.grants_no_execution_authority() is None
    assert not hasattr(decision, "execution_authorized")
    assert not hasattr(decision, "broker_submission")
    assert not hasattr(decision, "remote_maintenance_authorized")


def test_admission_is_deterministic_pure_function() -> None:
    now = _now()
    request = _base_request(now)
    service = PrimeAdmissionService()
    first = service.evaluate(request, now=now)
    second = service.evaluate(request, now=now)
    assert first.decision_id == second.decision_id
