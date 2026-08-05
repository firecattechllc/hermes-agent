from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from hermes_cli.prime.certification import (
    CheckSeverity,
    FleetCertification,
    FleetCertificationCheck,
    FleetCertificationStatus,
    certify_fleet,
)


def _now() -> int:
    return int(time.time())


def _all_pass_kwargs(now: int, **overrides) -> dict:
    fields = dict(
        evaluated_identity_ids=("fid_node_x",),
        identity_registry_conflict_free=True,
        event_schema_valid=True,
        evidence_chain_valid=True,
        health_protocol_compatible=True,
        admission_default_deny_selftest_passed=True,
        sigil_contract_restrictions_selftest_passed=True,
        remote_maintenance_default_deny_selftest_passed=True,
        stage1_regression_passed=True,
        ecosystem_services_no_unsafe_drift_selftest_passed=True,
        ecosystem_services_availability_selftest_passed=True,
        ecosystem_unverified_service_rejection_selftest_passed=True,
        ecosystem_duplicate_and_revoked_service_rejection_selftest_passed=True,
        ecosystem_self_evolution_self_approval_guard_selftest_passed=True,
        ecosystem_evidence_integrity_selftest_passed=True,
        policy_version="prime-admission-policy-v1",
        certifier_identity_id="prime",
        now=now,
        revalidation_seconds=3600,
    )
    fields.update(overrides)
    return fields


def test_all_checks_pass_yields_certified() -> None:
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now))
    assert cert.status == FleetCertificationStatus.CERTIFIED
    assert cert.reason_codes == ()


def test_missing_stage1_confirmation_blocks_not_certifies() -> None:
    """A certification can never claim CERTIFIED without positively
    confirming Stage 1 still holds — None (not run) is treated the same as
    a failure for status purposes, and produces a distinct reason code."""
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now, stage1_regression_passed=None))
    assert cert.status == FleetCertificationStatus.BLOCKED
    assert "stage1_regression_not_confirmed" in cert.reason_codes


def test_stage1_regression_failure_blocks() -> None:
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now, stage1_regression_passed=False))
    assert cert.status == FleetCertificationStatus.BLOCKED
    assert "stage1_regression_failed" in cert.reason_codes


def test_critical_failure_yields_failed_not_blocked() -> None:
    now = _now()
    cert = certify_fleet(
        **_all_pass_kwargs(now, admission_default_deny_selftest_passed=False)
    )
    assert cert.status == FleetCertificationStatus.FAILED


def test_critical_failure_dominates_blocking_failure() -> None:
    now = _now()
    cert = certify_fleet(
        **_all_pass_kwargs(
            now,
            admission_default_deny_selftest_passed=False,  # critical
            health_protocol_compatible=False,  # blocking
        )
    )
    assert cert.status == FleetCertificationStatus.FAILED


def test_certification_id_is_deterministic() -> None:
    now = _now()
    kwargs = _all_pass_kwargs(now)
    a = certify_fleet(**kwargs)
    b = certify_fleet(**kwargs)
    assert a.certification_id == b.certification_id


def test_status_cannot_be_set_inconsistently_with_checks() -> None:
    now = _now()
    checks = (
        FleetCertificationCheck(
            check_id="x",
            passed=False,
            severity=CheckSeverity.CRITICAL,
            reason_code="x_failed",
        ),
    )
    with pytest.raises(ValidationError):
        FleetCertification(
            certification_id="pcert_bad",
            status=FleetCertificationStatus.CERTIFIED,  # inconsistent with a failed critical check
            checks=checks,
            reason_codes=("x_failed",),
            evaluated_identity_ids=(),
            policy_version="prime-admission-policy-v1",
            evidence_refs=(),
            event_refs=(),
            certifier_identity_id="prime",
            issued_at=now,
            revalidate_after=now + 3600,
        )


def test_certification_grants_no_operational_authority_marker() -> None:
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now))
    assert cert.grants_no_operational_authority() is None
    assert not hasattr(cert, "execution_authorized")
    assert not hasattr(cert, "operational_authority")


_CRITICAL_ECOSYSTEM_FIELDS = [
    "ecosystem_services_no_unsafe_drift_selftest_passed",
    "ecosystem_unverified_service_rejection_selftest_passed",
    "ecosystem_duplicate_and_revoked_service_rejection_selftest_passed",
    "ecosystem_self_evolution_self_approval_guard_selftest_passed",
    "ecosystem_evidence_integrity_selftest_passed",
]


@pytest.mark.parametrize("field", _CRITICAL_ECOSYSTEM_FIELDS)
def test_missing_critical_ecosystem_selftest_confirmation_fails_not_certifies(field: str) -> None:
    """None (never run) fails closed — mirrors stage1_regression_passed's
    convention, but as CRITICAL since these guard core safety invariants."""
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now, **{field: None}))
    assert cert.status == FleetCertificationStatus.FAILED
    assert f"{field.removesuffix('_passed')}_not_confirmed" in cert.reason_codes


@pytest.mark.parametrize("field", _CRITICAL_ECOSYSTEM_FIELDS)
def test_failed_critical_ecosystem_selftest_fails_certification(field: str) -> None:
    now = _now()
    cert = certify_fleet(**_all_pass_kwargs(now, **{field: False}))
    assert cert.status == FleetCertificationStatus.FAILED
    assert f"{field.removesuffix('_passed')}_failed" in cert.reason_codes


def test_missing_ecosystem_availability_selftest_blocks_not_fails() -> None:
    """The one BLOCKING (optional-service) ecosystem check must never
    escalate to FAILED on its own — a missing optional service blocks
    CERTIFIED status without being conflated with a core-safety failure."""
    now = _now()
    cert = certify_fleet(
        **_all_pass_kwargs(now, ecosystem_services_availability_selftest_passed=None)
    )
    assert cert.status == FleetCertificationStatus.BLOCKED
    assert "ecosystem_services_availability_selftest_not_confirmed" in cert.reason_codes


def test_failed_ecosystem_availability_selftest_blocks_not_fails() -> None:
    now = _now()
    cert = certify_fleet(
        **_all_pass_kwargs(now, ecosystem_services_availability_selftest_passed=False)
    )
    assert cert.status == FleetCertificationStatus.BLOCKED
    assert "ecosystem_services_availability_selftest_failed" in cert.reason_codes


def test_ecosystem_availability_failure_does_not_mask_a_critical_failure() -> None:
    """A failed optional-service availability check must never make a
    concurrent core-safety failure look merely BLOCKED instead of FAILED —
    CRITICAL always dominates BLOCKING."""
    now = _now()
    cert = certify_fleet(
        **_all_pass_kwargs(
            now,
            ecosystem_services_availability_selftest_passed=False,
            ecosystem_services_no_unsafe_drift_selftest_passed=False,
        )
    )
    assert cert.status == FleetCertificationStatus.FAILED


def test_certify_fleet_omitting_ecosystem_params_defaults_to_failed() -> None:
    """Omitting the new parameters entirely (not just passing None) must
    also fail closed — they default to None, not True."""
    now = _now()
    kwargs = _all_pass_kwargs(now)
    for field in (
        *_CRITICAL_ECOSYSTEM_FIELDS,
        "ecosystem_services_availability_selftest_passed",
    ):
        del kwargs[field]
    cert = certify_fleet(**kwargs)
    assert cert.status == FleetCertificationStatus.FAILED


def test_certify_fleet_with_real_ecosystem_selftests_is_certified() -> None:
    """End-to-end: the actual (non-mocked)
    hermes_cli.prime.ecosystem_service_certification selftests, run for
    real, are sufficient to certify."""
    from hermes_cli.prime.ecosystem_service_certification import (
        run_all_ecosystem_service_selftests,
    )

    now = _now()
    results = run_all_ecosystem_service_selftests()
    cert = certify_fleet(
        **_all_pass_kwargs(
            now,
            ecosystem_services_no_unsafe_drift_selftest_passed=results[
                "ecosystem_services_no_unsafe_drift"
            ],
            ecosystem_services_availability_selftest_passed=results[
                "ecosystem_services_availability"
            ],
            ecosystem_unverified_service_rejection_selftest_passed=results[
                "unverified_service_rejection"
            ],
            ecosystem_duplicate_and_revoked_service_rejection_selftest_passed=results[
                "duplicate_and_revoked_service_rejection"
            ],
            ecosystem_self_evolution_self_approval_guard_selftest_passed=results[
                "self_evolution_self_approval_guard"
            ],
            ecosystem_evidence_integrity_selftest_passed=results[
                "ecosystem_evidence_integrity"
            ],
        )
    )
    assert cert.status == FleetCertificationStatus.CERTIFIED
