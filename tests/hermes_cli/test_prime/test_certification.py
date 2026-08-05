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
