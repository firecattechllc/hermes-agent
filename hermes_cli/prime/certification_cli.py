"""CLI wrapper for :func:`hermes_cli.prime.certification.certify_fleet`.

Fleet Unification live-runtime work. ``certify_fleet`` is deliberately a
pure function of caller-supplied booleans (see its docstring) — nothing in
this repository, before this module, actually ran every real check those
booleans represent and assembled them into one certification call. This is
that CLI: it runs the Stage 1 regression scripts, every live-runtime
selftest (:mod:`hermes_cli.prime.live_runtime_certification`), every
production-certification selftest
(:mod:`hermes_cli.prime.production_certification_selftests`), and, when
pointed at a real deployed ``--state-root``, the actual evidence-chain
integrity check and the real set of registered fleet node identities —
never a hardcoded ``True``.

Without ``--state-root`` this still runs every selftest for real, but
``evidence_chain_valid`` is reported ``False`` (there is no real evidence
journal to check) and ``evaluated_identity_ids`` is empty — which correctly
drives the overall result to ``FAILED``/``BLOCKED`` rather than fabricating a
CERTIFIED result for a fleet that was never actually inspected. This matches
the "do not certify mocks as deployed hardware" requirement: a repo-only run
is expected to fail closed, not report CERTIFIED.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from hermes_cli.prime.certification import (
    FleetCertificationStatus,
    certify_fleet,
    run_stage1_regression,
)
from hermes_cli.prime.evidence import EvidenceStorageError
from hermes_cli.prime.fleet_runtime import DEFAULT_POLICY_VERSION, FleetRuntime
from hermes_cli.prime.live_runtime_certification import run_all_live_runtime_selftests
from hermes_cli.prime.production_certification_selftests import (
    run_all_production_certification_selftests,
)


def _real_evidence_chain_valid(state_root: Optional[Path]) -> bool:
    if state_root is None:
        return False
    try:
        runtime = FleetRuntime(state_root=state_root)
        return runtime.visibility._evidence_store.verify_chain()  # noqa: SLF001
    except EvidenceStorageError:
        return False


def _real_evaluated_identity_ids(state_root: Optional[Path]) -> tuple[str, ...]:
    if state_root is None:
        return ()
    runtime = FleetRuntime(state_root=state_root)
    return tuple(node.identity_id for node in runtime.registry.all())


def run_certification(
    *,
    repo_root: Path,
    state_root: Optional[Path],
    certifier_identity_id: str,
    policy_version: str = DEFAULT_POLICY_VERSION,
    revalidation_seconds: int = 3600,
    skip_stage1: bool = False,
) -> tuple[dict[str, Any], FleetCertificationStatus]:
    """Run every real check and assemble one :class:`FleetCertification`.

    Returns ``(payload, status)`` where ``payload`` is the JSON-serializable
    certification result plus a ``checks_detail`` breakdown of every
    selftest that fed it, for a human or CI job to inspect exactly which
    check failed rather than only the aggregate status.
    """
    now = int(time.time())

    stage1_passed: Optional[bool]
    stage1_detail = "skipped"
    if skip_stage1:
        stage1_passed = None
    else:
        stage1_passed, stage1_detail = run_stage1_regression(repo_root=repo_root)

    production_selftests = run_all_production_certification_selftests()
    live_runtime_selftests = run_all_live_runtime_selftests()
    evidence_chain_valid = _real_evidence_chain_valid(state_root)
    evaluated_identity_ids = _real_evaluated_identity_ids(state_root)

    certification = certify_fleet(
        evaluated_identity_ids=evaluated_identity_ids,
        identity_registry_conflict_free=production_selftests["identity_registry_conflict_free"],
        event_schema_valid=production_selftests["event_schema_valid"],
        evidence_chain_valid=evidence_chain_valid,
        health_protocol_compatible=production_selftests["health_protocol_compatible"],
        admission_default_deny_selftest_passed=production_selftests["admission_default_deny"],
        sigil_contract_restrictions_selftest_passed=production_selftests[
            "sigil_contract_restrictions"
        ],
        remote_maintenance_default_deny_selftest_passed=production_selftests[
            "remote_maintenance_default_deny"
        ],
        stage1_regression_passed=stage1_passed,
        live_runtime_fleet_registry_and_heartbeat_selftest_passed=live_runtime_selftests[
            "fleet_registry_and_heartbeat"
        ],
        live_runtime_dispatch_routing_and_model_configuration_selftest_passed=live_runtime_selftests[
            "dispatch_routing_and_model_configuration"
        ],
        live_runtime_desktop_use_and_operator_approval_selftest_passed=live_runtime_selftests[
            "desktop_use_and_operator_approval"
        ],
        live_runtime_sigil_isolation_selftest_passed=live_runtime_selftests["sigil_isolation"],
        live_runtime_evidence_integrity_selftest_passed=live_runtime_selftests[
            "evidence_integrity"
        ],
        policy_version=policy_version,
        certifier_identity_id=certifier_identity_id,
        now=now,
        revalidation_seconds=revalidation_seconds,
    )

    payload: dict[str, Any] = certification.model_dump(mode="json")
    payload["checks_detail"] = {
        "stage1_regression": {"passed": stage1_passed, "detail": stage1_detail},
        "production_selftests": production_selftests,
        "live_runtime_selftests": live_runtime_selftests,
        "evidence_chain_valid": evidence_chain_valid,
        "state_root_evaluated": str(state_root) if state_root is not None else None,
    }
    return payload, certification.status


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hermes-prime-certify",
        description=(
            "Run every real Prime fleet certification check and report a "
            "certify_fleet() result. Exits 0 only when status is CERTIFIED."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root (for Stage 1 regression scripts). Defaults to this checkout.",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=None,
        help=(
            "Prime's real deployed state root (HERMES_PRIME_STATE_ROOT). "
            "Without this, evidence_chain_valid and evaluated_identity_ids "
            "cannot reflect real fleet state and certification fails closed."
        ),
    )
    parser.add_argument(
        "--certifier-identity-id",
        default="prime-certification-cli",
        help="Identity recorded as having issued this certification.",
    )
    parser.add_argument(
        "--skip-stage1",
        action="store_true",
        help="Skip the Stage 1 regression scripts (they require the apps/sigil venv).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write the JSON certification payload to.",
    )
    args = parser.parse_args(argv)

    payload, status = run_certification(
        repo_root=args.repo_root,
        state_root=args.state_root,
        certifier_identity_id=args.certifier_identity_id,
        skip_stage1=args.skip_stage1,
    )

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    return 0 if status == FleetCertificationStatus.CERTIFIED else 1


if __name__ == "__main__":
    sys.exit(main())
