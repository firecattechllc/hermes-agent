from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.admission import AdmissionDecision, AdmissionOutcome
from hermes_cli.prime.health import HealthReport, LivenessState, ReadinessState
from hermes_cli.prime.sigil_contract import (
    SigilContractOutcome,
    SigilContractRequest,
    SigilContractResponse,
    SigilRejectionCode,
    evaluate_sigil_contract_request,
)


def _now() -> int:
    return int(time.time())


def _admitted(now: int, subject: str = "sigil") -> AdmissionDecision:
    return AdmissionDecision(
        decision_id=f"padm_{subject}",
        request_id="req1",
        subject_identity_id=subject,
        outcome=AdmissionOutcome.ADMITTED,
        reason_codes=(),
        policy_version="prime-admission-policy-v1",
        decided_at=now,
        revalidate_after=now + 300,
    )


def _healthy(now: int, subject: str = "sigil") -> HealthReport:
    return HealthReport(
        report_id=f"health_{subject}",
        subject_identity_id=subject,
        observed_at=now,
        expires_at=now + 300,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )


def _request(now: int, **overrides) -> SigilContractRequest:
    fields = dict(
        request_id="sreq1",
        correlation_id="corr1",
        caller_identity_id="hermes-fleet",
        service_identity_id="sigil",
        operation="advisory_valuation",
        requested_at=now,
    )
    fields.update(overrides)
    return SigilContractRequest(**fields)


def test_request_cannot_be_non_advisory() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), advisory=False)


def test_request_cannot_disable_paper_only() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), paper_only=False)


def test_request_cannot_permit_broker_submission() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), broker_submission_denied=False)


def test_request_cannot_grant_execution_authority() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), execution_authority_denied=False)


def test_request_cannot_permit_production_mutation() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), production_mutation_denied=False)


def test_request_cannot_self_address() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), caller_identity_id="sigil", service_identity_id="sigil")


def test_unsupported_operation_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), operation="live_broker_order_submission")


def test_unsupported_contract_version_rejected_at_construction() -> None:
    with pytest.raises(ValidationError):
        _request(_now(), contract_version=99)


def test_response_can_never_grant_execution_authority() -> None:
    with pytest.raises(ValidationError):
        SigilContractResponse(
            request_id="sreq1",
            correlation_id="corr1",
            outcome=SigilContractOutcome.ACCEPTED,
            evidence_refs=("ev1",),
            completed_at=_now(),
            execution_authority_granted=True,
        )


def test_response_can_never_grant_broker_submission() -> None:
    with pytest.raises(ValidationError):
        SigilContractResponse(
            request_id="sreq1",
            correlation_id="corr1",
            outcome=SigilContractOutcome.ACCEPTED,
            evidence_refs=("ev1",),
            completed_at=_now(),
            broker_submission_granted=True,
        )


def test_accepted_response_requires_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        SigilContractResponse(
            request_id="sreq1",
            correlation_id="corr1",
            outcome=SigilContractOutcome.ACCEPTED,
            evidence_refs=(),
            completed_at=_now(),
        )


def test_rejected_response_requires_rejection_code() -> None:
    with pytest.raises(ValidationError):
        SigilContractResponse(
            request_id="sreq1",
            correlation_id="corr1",
            outcome=SigilContractOutcome.REJECTED,
            completed_at=_now(),
        )


def test_evaluate_admits_when_all_preconditions_met() -> None:
    now = _now()
    request = _request(now)
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=_admitted(now, "hermes-fleet"),
        service_admission=_admitted(now, "sigil"),
        caller_health=_healthy(now, "hermes-fleet"),
        service_health=_healthy(now, "sigil"),
        now=now,
    )
    assert admitted is True
    assert code is None


def test_caller_not_admitted_denies() -> None:
    now = _now()
    request = _request(now)
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=None,
        service_admission=_admitted(now, "sigil"),
        caller_health=_healthy(now, "hermes-fleet"),
        service_health=_healthy(now, "sigil"),
        now=now,
    )
    assert admitted is False
    assert code == SigilRejectionCode.CALLER_NOT_ADMITTED


def test_service_not_admitted_denies() -> None:
    now = _now()
    request = _request(now)
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=_admitted(now, "hermes-fleet"),
        service_admission=None,
        caller_health=_healthy(now, "hermes-fleet"),
        service_health=_healthy(now, "sigil"),
        now=now,
    )
    assert admitted is False
    assert code == SigilRejectionCode.SERVICE_NOT_ADMITTED


def test_expired_admission_denies_even_if_outcome_was_admitted() -> None:
    now = _now()
    stale_admission = AdmissionDecision(
        decision_id="padm_stale",
        request_id="req1",
        subject_identity_id="hermes-fleet",
        outcome=AdmissionOutcome.ADMITTED,
        reason_codes=(),
        policy_version="prime-admission-policy-v1",
        decided_at=now - 10_000,
        revalidate_after=now - 5_000,
    )
    request = _request(now)
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=stale_admission,
        service_admission=_admitted(now, "sigil"),
        caller_health=_healthy(now, "hermes-fleet"),
        service_health=_healthy(now, "sigil"),
        now=now,
    )
    assert admitted is False
    assert code == SigilRejectionCode.CALLER_NOT_ADMITTED


def test_unusable_health_denies_despite_admission() -> None:
    now = _now()
    stale_health = HealthReport(
        report_id="health_stale",
        subject_identity_id="sigil",
        observed_at=now - 10_000,
        expires_at=now + 100_000,
        liveness=LivenessState.ALIVE,
        readiness=ReadinessState.READY,
    )
    request = _request(now)
    admitted, code = evaluate_sigil_contract_request(
        request,
        caller_admission=_admitted(now, "hermes-fleet"),
        service_admission=_admitted(now, "sigil"),
        caller_health=_healthy(now, "hermes-fleet"),
        service_health=stale_health,
        now=now,
    )
    assert admitted is False
    assert code == SigilRejectionCode.SERVICE_HEALTH_NOT_USABLE
