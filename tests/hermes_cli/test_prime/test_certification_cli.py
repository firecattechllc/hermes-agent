from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.prime.certification import FleetCertificationStatus
from hermes_cli.prime.certification_cli import main, run_certification
from hermes_cli.prime.fleet_registry import (
    FleetNodeRecord,
    FleetNodeRegistrationRequest,
    FleetNodeRole,
)
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission


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
    runtime.registry._store.put(  # noqa: SLF001 - historical durable state fixture
        FleetNodeRecord(
            identity_id="fid_node_hydra_live_historical",
            natural_key="hydra-live",
            role=FleetNodeRole.HYDRA_LIVE,
            endpoint="http://hydra-live.invalid:3130",
            software_version="historical",
            protocol_version=1,
            registered_at=1,
            updated_at=1,
        )
    )

    payload, status = run_certification(
        repo_root=tmp_path,
        state_root=state_root,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
    )
    assert len(payload["evaluated_identity_ids"]) == 1
    assert "fid_node_hydra_live_historical" not in payload["evaluated_identity_ids"]
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


def _runtime_with_reported_models(tmp_path: Path, *models: str) -> tuple[Path, FleetRuntime]:
    state_root = tmp_path / "prime-state"
    runtime = FleetRuntime(state_root=state_root, project_id="model-alias-certification-test")
    now = 1_800_000_000
    runtime.register_node(
        FleetNodeRegistrationRequest(
            request_id="register-mac",
            natural_key="mac",
            role=FleetNodeRole.MAC,
            endpoint="http://mac.tailnet.internal:11434",
            software_version="1.0.0",
            protocol_version=1,
            requested_at=now,
        ),
        now=now,
    )
    runtime.ingest_heartbeat(
        HeartbeatSubmission(
            natural_key="mac",
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY,
            reported_model_inventory=models,
            submitted_at=now,
        ),
        now=now,
    )
    return state_root, runtime


def test_configured_model_exists_is_certifiable(tmp_path: Path) -> None:
    state_root, _ = _runtime_with_reported_models(tmp_path, "gemma4:12b")
    payload, _ = run_certification(
        repo_root=tmp_path,
        state_root=state_root,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
        node_model_aliases={"mac": {"primary_reasoning": "gemma4:12b"}},
    )
    assert payload["checks_detail"]["configured_model_aliases"] == {
        "passed": True,
        "configured_alias_count": 1,
        "missing": [],
        "automatic_fallback": False,
    }


def test_configured_model_absent_fails_certification_explicitly(tmp_path: Path) -> None:
    state_root, _ = _runtime_with_reported_models(tmp_path, "gemma4:12b")
    payload, status = run_certification(
        repo_root=tmp_path,
        state_root=state_root,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
        node_model_aliases={"mac": {"primary_reasoning": "missing-model"}},
    )
    detail = payload["checks_detail"]["configured_model_aliases"]
    assert status == FleetCertificationStatus.FAILED
    assert detail["passed"] is False
    assert detail["missing"] == [
        {
            "node": "mac",
            "alias": "primary_reasoning",
            "model": "missing-model",
            "reason": "configured_model_not_reported",
        }
    ]


def test_configured_model_absent_never_falls_back_to_installed_model(tmp_path: Path) -> None:
    state_root, _ = _runtime_with_reported_models(
        tmp_path, "gemma4:12b", "hermes-llama3.2:3b-64k"
    )
    payload, status = run_certification(
        repo_root=tmp_path,
        state_root=state_root,
        certifier_identity_id="test-certifier",
        skip_stage1=True,
        node_model_aliases={"mac": {"primary_reasoning": "missing-model"}},
    )
    detail = payload["checks_detail"]["configured_model_aliases"]
    assert status == FleetCertificationStatus.FAILED
    assert detail["automatic_fallback"] is False
    assert detail["missing"][0]["model"] == "missing-model"
