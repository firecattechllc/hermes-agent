from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.prime.certification import FleetCertificationStatus
from hermes_cli.prime.certification_cli import main, run_certification
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime


def test_run_certification_without_state_root_fails_closed(tmp_path: Path) -> None:
    payload, status = run_certification(
        repo_root=tmp_path,
        state_root=None,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
    )
    # No real evidence chain or fleet state was ever inspected — this must
    # never silently report CERTIFIED.
    assert status != FleetCertificationStatus.CERTIFIED
    assert payload["evaluated_identity_ids"] == []
    assert payload["checks_detail"]["evidence_chain_valid"] is False


def test_run_certification_with_real_state_root_evaluates_real_fleet(tmp_path: Path) -> None:
    state_root = tmp_path / "prime-state"
    runtime = FleetRuntime(state_root=state_root, project_id="certification-cli-test")
    now = 1_800_000_000
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="cert-cli-test-titan",
            natural_key="titan",
            role=FleetNodeRole.TITAN,
            endpoint="http://titan.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )

    payload, status = run_certification(
        repo_root=tmp_path,
        state_root=state_root,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
    )
    assert len(payload["evaluated_identity_ids"]) == 1
    assert payload["checks_detail"]["evidence_chain_valid"] is True
    # stage1 skipped -> BLOCKED at best, never CERTIFIED, never FAILED from a
    # missing check that was never actually run.
    assert status == FleetCertificationStatus.BLOCKED


def test_cli_main_writes_json_and_exits_nonzero_without_state(
    tmp_path: Path, capsys
) -> None:
    out_path = tmp_path / "cert.json"
    exit_code = main(
        [
            "--repo-root",
            str(tmp_path),
            "--skip-stage1",
            "--out",
            str(out_path),
        ]
    )
    assert exit_code != 0
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["status"] != "certified"
    captured = capsys.readouterr()
    assert '"status"' in captured.out
