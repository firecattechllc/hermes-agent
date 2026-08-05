"""Fleet certification.

Fleet Unification Stage 2H (and the Stage 9 capstone). Deterministically
certifies the whole Stage 2 control plane: canonical identities, Mission
Control event schema/integrity, evidence integrity and linkage, health
freshness and protocol compatibility, Prime admission default-deny behavior,
the Sigil contract's paper-only/advisory restrictions, remote-maintenance
default-deny behavior, and — critically — that the pre-existing, immutable
Stage 1 safety and certification baseline still passes unmodified.

The status derivation mirrors the pattern already established by
``hermes_cli.agent_roles.system_integration_certification.SystemIntegrationCertification``:
status is *derived* deterministically from the individual check results via
a model validator, never set directly by a caller, and the certification ID
is content-addressed from the certified payload.

Fleet certification grants no operational or execution authority. A
``CERTIFIED`` result means the control plane's governance invariants held
during evaluation — nothing more.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FLEET_CERTIFICATION_SCHEMA_VERSION = 1
SUPPORTED_FLEET_CERTIFICATION_SCHEMA_VERSIONS = frozenset({1})


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_FLEET_CERTIFICATION_SCHEMA_VERSIONS:
        raise ValueError(
            f"fleet certification schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_FLEET_CERTIFICATION_SCHEMA_VERSIONS)})"
        )
    return version


class FleetCertificationStatus(str, Enum):
    CERTIFIED = "certified"
    BLOCKED = "blocked"
    FAILED = "failed"


class CheckSeverity(str, Enum):
    CRITICAL = "critical"
    BLOCKING = "blocking"
    INFO = "info"


class FleetCertificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    check_id: str = Field(..., min_length=1, max_length=128)
    passed: bool
    severity: CheckSeverity
    reason_code: Optional[str] = Field(default=None, max_length=128)
    detail: Optional[str] = Field(default=None, max_length=1024)


class FleetCertification(BaseModel):
    """A deterministic, content-addressed fleet certification result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    certification_id: str = Field(..., min_length=1, max_length=160)
    schema_version: int = Field(default=FLEET_CERTIFICATION_SCHEMA_VERSION)
    status: FleetCertificationStatus
    checks: Tuple[FleetCertificationCheck, ...]
    reason_codes: Tuple[str, ...]
    evaluated_identity_ids: Tuple[str, ...]
    policy_version: str = Field(..., min_length=1, max_length=64)
    evidence_refs: Tuple[str, ...]
    event_refs: Tuple[str, ...]
    certifier_identity_id: str = Field(..., min_length=1, max_length=128)
    issued_at: int = Field(..., ge=0)
    revalidate_after: int = Field(..., ge=0)
    correlation_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @model_validator(mode="after")
    def _status_matches_checks(self) -> "FleetCertification":
        critical_failed = any(
            not c.passed and c.severity == CheckSeverity.CRITICAL for c in self.checks
        )
        blocking_failed = any(
            not c.passed and c.severity == CheckSeverity.BLOCKING for c in self.checks
        )
        if critical_failed and self.status != FleetCertificationStatus.FAILED:
            raise ValueError("a critical check failure must produce FAILED status")
        if (
            not critical_failed
            and blocking_failed
            and self.status != FleetCertificationStatus.BLOCKED
        ):
            raise ValueError(
                "a blocking check failure (with no critical failure) must "
                "produce BLOCKED status"
            )
        if (
            not critical_failed
            and not blocking_failed
            and self.status != FleetCertificationStatus.CERTIFIED
        ):
            raise ValueError("a clean check run must produce CERTIFIED status")
        return self

    def grants_no_operational_authority(self) -> None:
        """Documentation no-op — see identity/admission for the same
        convention. A CERTIFIED result never itself authorizes execution."""
        return None


def _default_stage1_python(repo_root: Path) -> str:
    """Prefer the ``apps/sigil`` virtualenv interpreter, since
    ``verify_certification_evidence.py`` imports the installed ``sigil``
    package, which is not necessarily importable from whichever interpreter
    happens to be running the caller (e.g. the repository-root virtualenv
    used for ``hermes_cli`` tests). Falls back to ``sys.executable`` if that
    virtualenv is not present."""
    sigil_python = repo_root / "apps" / "sigil" / ".venv" / "bin" / "python"
    if sigil_python.exists():
        return str(sigil_python)
    return sys.executable


def run_stage1_regression(
    *,
    repo_root: Path,
    timeout_seconds: int = 300,
    python_executable: Optional[str] = None,
) -> Tuple[bool, str]:
    """Invoke the immutable Stage 1 verification scripts unmodified.

    Runs both ``apps/sigil/scripts/verify_certification_evidence.py`` and
    ``apps/sigil/scripts/verify_public_execution_isolation.py`` exactly as
    they exist on disk, via subprocess, and reports pass/fail. This function
    never edits, mocks, or bypasses either script — a failure here is a real
    regression, not a test artifact.

    Returns ``(passed, detail)``. This is intentionally *not* called by
    :func:`certify_fleet` implicitly (which stays a pure function of its
    inputs for deterministic unit testing) — callers must run this
    separately and pass the boolean result in as
    ``stage1_regression_passed``.
    """
    python_executable = python_executable or _default_stage1_python(repo_root)
    scripts = [
        repo_root / "apps" / "sigil" / "scripts" / "verify_certification_evidence.py",
        repo_root
        / "apps"
        / "sigil"
        / "scripts"
        / "verify_public_execution_isolation.py",
    ]
    details = []
    all_passed = True
    for script in scripts:
        if not script.exists():
            return False, f"missing Stage 1 verification script: {script}"
        result = subprocess.run(
            [python_executable, str(script)],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        passed = result.returncode == 0
        all_passed = all_passed and passed
        details.append(
            f"{script.name}: exit={result.returncode} "
            f"stdout_tail={result.stdout[-500:]!r} stderr_tail={result.stderr[-500:]!r}"
        )
    return all_passed, "; ".join(details)


def certify_fleet(
    *,
    evaluated_identity_ids: Tuple[str, ...],
    identity_registry_conflict_free: bool,
    event_schema_valid: bool,
    evidence_chain_valid: bool,
    health_protocol_compatible: bool,
    admission_default_deny_selftest_passed: bool,
    sigil_contract_restrictions_selftest_passed: bool,
    remote_maintenance_default_deny_selftest_passed: bool,
    stage1_regression_passed: Optional[bool],
    policy_version: str,
    certifier_identity_id: str,
    now: int,
    revalidation_seconds: int,
    evidence_refs: Tuple[str, ...] = (),
    event_refs: Tuple[str, ...] = (),
    correlation_id: Optional[str] = None,
) -> FleetCertification:
    """Deterministically certify the fleet control plane. Pure function.

    ``stage1_regression_passed`` is deliberately ``Optional[bool]``: if the
    caller has not actually run :func:`run_stage1_regression` (or an
    equivalent check) and passes ``None``, certification is BLOCKED rather
    than silently proceeding as if Stage 1 were unaffected — a fleet
    certification can never claim to be CERTIFIED without positively
    confirming the immutable Stage 1 baseline still holds.
    """
    checks: list[FleetCertificationCheck] = [
        FleetCertificationCheck(
            check_id="identity_registry_conflict_free",
            passed=identity_registry_conflict_free,
            severity=CheckSeverity.CRITICAL,
            reason_code=None
            if identity_registry_conflict_free
            else "identity_conflict_detected",
        ),
        FleetCertificationCheck(
            check_id="event_schema_valid",
            passed=event_schema_valid,
            severity=CheckSeverity.CRITICAL,
            reason_code=None if event_schema_valid else "event_schema_invalid",
        ),
        FleetCertificationCheck(
            check_id="evidence_chain_valid",
            passed=evidence_chain_valid,
            severity=CheckSeverity.CRITICAL,
            reason_code=None if evidence_chain_valid else "evidence_chain_invalid",
        ),
        FleetCertificationCheck(
            check_id="health_protocol_compatible",
            passed=health_protocol_compatible,
            severity=CheckSeverity.BLOCKING,
            reason_code=None
            if health_protocol_compatible
            else "health_protocol_incompatible",
        ),
        FleetCertificationCheck(
            check_id="admission_default_deny_selftest",
            passed=admission_default_deny_selftest_passed,
            severity=CheckSeverity.CRITICAL,
            reason_code=None
            if admission_default_deny_selftest_passed
            else "admission_default_deny_selftest_failed",
        ),
        FleetCertificationCheck(
            check_id="sigil_contract_restrictions_selftest",
            passed=sigil_contract_restrictions_selftest_passed,
            severity=CheckSeverity.CRITICAL,
            reason_code=None
            if sigil_contract_restrictions_selftest_passed
            else "sigil_contract_restrictions_selftest_failed",
        ),
        FleetCertificationCheck(
            check_id="remote_maintenance_default_deny_selftest",
            passed=remote_maintenance_default_deny_selftest_passed,
            severity=CheckSeverity.CRITICAL,
            reason_code=None
            if remote_maintenance_default_deny_selftest_passed
            else "remote_maintenance_default_deny_selftest_failed",
        ),
        FleetCertificationCheck(
            check_id="stage1_regression",
            passed=bool(stage1_regression_passed),
            severity=CheckSeverity.BLOCKING,
            reason_code=(
                None
                if stage1_regression_passed is True
                else "stage1_regression_not_confirmed"
                if stage1_regression_passed is None
                else "stage1_regression_failed"
            ),
        ),
    ]

    critical_failed = any(
        not c.passed and c.severity == CheckSeverity.CRITICAL for c in checks
    )
    blocking_failed = any(
        not c.passed and c.severity == CheckSeverity.BLOCKING for c in checks
    )

    if critical_failed:
        status = FleetCertificationStatus.FAILED
    elif blocking_failed:
        status = FleetCertificationStatus.BLOCKED
    else:
        status = FleetCertificationStatus.CERTIFIED

    reason_codes = tuple(c.reason_code for c in checks if c.reason_code is not None)

    payload = {
        "evaluated_identity_ids": list(evaluated_identity_ids),
        "checks": [c.model_dump(mode="json") for c in checks],
        "status": status.value,
        "policy_version": policy_version,
        "issued_at": now,
    }
    certification_id = f"pcert_{_digest(payload)[:24]}"

    return FleetCertification(
        certification_id=certification_id,
        status=status,
        checks=tuple(checks),
        reason_codes=reason_codes,
        evaluated_identity_ids=evaluated_identity_ids,
        policy_version=policy_version,
        evidence_refs=evidence_refs,
        event_refs=event_refs,
        certifier_identity_id=certifier_identity_id,
        issued_at=now,
        revalidate_after=now + revalidation_seconds,
        correlation_id=correlation_id,
    )
