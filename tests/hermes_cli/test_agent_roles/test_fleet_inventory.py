from __future__ import annotations

import json

import pytest

from hermes_cli.agent_roles.fleet_inventory import (
    GovernedInventoryExecutor,
    InventoryApproval,
    InventoryEvidence,
    InventoryMode,
    InventoryProposal,
    InventoryResult,
    InventoryStep,
    InventoryTarget,
    SecretReference,
    certify_fleet_inventory,
    diagnose_fleet_inventory,
    redact_inventory_evidence,
)
from hermes_cli.agent_roles.fleet_inventory_visibility import (
    InventoryVisibilityService,
)
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def target() -> InventoryTarget:
    return InventoryTarget(
        target_id="fleet-sim",
        host_alias="fleet-fixture",
        user="fleet",
        credential=SecretReference(provider="runtime", key="fleet-ssh"),
        private_addresses=("10.45.0.1",),
    )


def step(
    command_id: str = "system_info",
    *,
    step_id: str = "step-1",
    mode: InventoryMode = InventoryMode.READ_ONLY,
    scope: str = "inventory:read",
) -> InventoryStep:
    return InventoryStep(
        step_id=step_id,
        command_id=command_id,
        mode=mode,
        required_scope=scope,
    )


def proposal(*steps: InventoryStep) -> InventoryProposal:
    return InventoryProposal.build(
        target_id="fleet-sim",
        steps=steps or (step(),),
        reason="collect governed fleet inventory",
        evidence_refs=(),
    )


def approval(
    item: InventoryProposal,
    *scopes: str,
) -> InventoryApproval:
    return InventoryApproval(
        approval_id="approval-1",
        proposal_id=item.proposal_id,
        proposal_checksum=item.checksum,
        scopes=scopes,
        actor_id="operator",
        approved_at=100,
        reason="approved exact inventory proposal",
    )


def evidence(
    command_id: str,
    output: str,
    *,
    ok: bool = True,
    timed_out: bool = False,
) -> InventoryEvidence:
    return InventoryEvidence.build(
        target=target(),
        command_id=command_id,
        result=InventoryResult(
            exit_code=0 if ok else 1,
            stdout=output,
            timed_out=timed_out,
        ),
        collected_at=100,
    )


def test_target_requires_secret_reference_and_configured_destination():
    with pytest.raises(ValueError):
        SecretReference(provider="runtime", key="token=actual-secret")
    with pytest.raises(ValueError):
        InventoryTarget(
            target_id="x",
            user="fleet",
            credential=SecretReference(provider="runtime", key="ref"),
        )
    with pytest.raises(ValueError):
        InventoryTarget(
            target_id="x",
            host_alias="one",
            endpoint="10.0.0.2",
            user="fleet",
            credential=SecretReference(provider="runtime", key="ref"),
        )


def test_redaction_covers_secret_classes_private_addresses_and_bounds():
    raw = """Authorization: Bearer abc123
token=hunter2 password=swordfish SECRET=value API_KEY=key
machine-id=0123456789abcdef0123456789abcdef
-----BEGIN OPENSSH PRIVATE KEY-----
private material
-----END OPENSSH PRIVATE KEY-----
peer=10.45.0.1
"""
    clean = redact_inventory_evidence(
        raw + ("x" * 20_000),
        private_addresses=("10.45.0.1",),
    )
    for secret in (
        "abc123",
        "hunter2",
        "swordfish",
        "private material",
        "10.45.0.1",
        "0123456789abcdef",
    ):
        assert secret not in clean
    assert len(clean) == 16_384


def test_evidence_and_proposal_identifiers_are_deterministic_and_tamper_evident():
    first = evidence("system_info", "Linux fixture")
    second = evidence("system_info", "Linux fixture")
    assert first == second

    first_proposal = proposal(step())
    second_proposal = proposal(step())
    assert first_proposal == second_proposal

    values = first_proposal.model_dump()
    values["checksum"] = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        InventoryProposal.model_validate(values)


def test_step_policy_rejects_arbitrary_or_mismatched_commands():
    with pytest.raises(ValueError, match="allow-listed"):
        step("rm_everything")
    with pytest.raises(ValueError, match="policy"):
        step(
            "listening_sockets",
            mode=InventoryMode.READ_ONLY,
            scope="inventory:read",
        )
    with pytest.raises(ValueError, match="unsafe"):
        InventoryStep(
            step_id="bad",
            command_id="system_info",
            mode=InventoryMode.READ_ONLY,
            required_scope="inventory:read",
            subject="x; reboot",
        )


def test_unapproved_execution_is_rejected_before_runner_call():
    item = proposal(step())
    calls = []

    with pytest.raises(PermissionError, match="inventory:read"):
        GovernedInventoryExecutor().execute(
            target=target(),
            proposal=item,
            approvals=(),
            runner=lambda argv, timeout: calls.append((argv, timeout))
            or InventoryResult(exit_code=0),
            timestamp=101,
        )

    assert calls == []


def test_exact_approval_executes_closed_read_only_command():
    item = proposal(step())
    calls = []

    execution = GovernedInventoryExecutor().execute(
        target=target(),
        proposal=item,
        approvals=(approval(item, "inventory:read"),),
        runner=lambda argv, timeout: calls.append((argv, timeout))
        or InventoryResult(exit_code=0, stdout="Linux fixture"),
        timestamp=101,
    )

    assert execution.state == "completed"
    assert execution.executed_steps == ("step-1",)
    assert calls[0][0][-2:] == ("uname", "-a")
    assert calls[0][1] == 20


def test_privileged_read_requires_exact_scope():
    privileged = step(
        "listening_sockets",
        mode=InventoryMode.PRIVILEGED_READ_ONLY,
        scope="inventory:privileged_read",
    )
    item = proposal(privileged)

    with pytest.raises(PermissionError, match="inventory:privileged_read"):
        GovernedInventoryExecutor().execute(
            target=target(),
            proposal=item,
            approvals=(approval(item, "inventory:read"),),
            runner=lambda argv, timeout: InventoryResult(exit_code=0),
            timestamp=101,
        )


def test_failure_and_timeout_produce_bounded_failed_evidence_and_stop():
    item = proposal(
        step("system_info", step_id="one"),
        step("os_release", step_id="two"),
    )
    results = [
        InventoryResult(
            exit_code=0,
            stdout="token=actual-secret peer=10.45.0.1",
            timed_out=True,
        ),
        InventoryResult(exit_code=0, stdout="must not execute"),
    ]
    calls = []

    execution = GovernedInventoryExecutor().execute(
        target=target(),
        proposal=item,
        approvals=(approval(item, "inventory:read"),),
        runner=lambda argv, timeout: calls.append(argv) or results.pop(0),
        timestamp=101,
    )

    assert execution.state == "failed"
    assert execution.executed_steps == ()
    assert len(execution.evidence) == 1
    assert execution.evidence[0].timed_out
    assert "actual-secret" not in execution.evidence[0].output
    assert "10.45.0.1" not in execution.evidence[0].output
    assert len(calls) == 1


def test_diagnosis_is_evidence_backed_and_deterministic():
    items = (
        evidence("container_list", "root uid=0"),
        evidence("failed_services", "broken.service failed"),
        evidence("disk_usage", "/dev/root 100 95 5 95% /"),
        evidence("system_info", "failure", ok=False),
    )

    first = diagnose_fleet_inventory(items)
    second = diagnose_fleet_inventory(reversed(items))

    assert first == second
    assert {item.code.value for item in first} == {
        "container_running_as_root",
        "failed_services_present",
        "disk_pressure",
        "inventory_command_failed",
    }
    assert all(item.evidence_refs for item in first)


def test_certification_requires_matching_completed_secret_free_execution():
    item = proposal(step())
    execution = GovernedInventoryExecutor().execute(
        target=target(),
        proposal=item,
        approvals=(approval(item, "inventory:read"),),
        runner=lambda argv, timeout: InventoryResult(
            exit_code=0,
            stdout="Linux fixture",
        ),
        timestamp=101,
    )

    certification = certify_fleet_inventory(item, execution)
    assert certification.certified
    assert all(result for _, result in certification.checks)

    failed = execution.model_copy(update={"state": "failed"})
    assert not certify_fleet_inventory(item, failed).certified


def test_persisted_evidence_is_json_serializable_and_secret_free():
    item = evidence(
        "system_info",
        "token=actual-secret peer=10.45.0.1",
    )
    encoded = json.dumps(item.model_dump(mode="json"))
    assert "actual-secret" not in encoded
    assert "10.45.0.1" not in encoded


def test_proposal_is_idempotently_visible_in_mission_control(tmp_path):
    mission = MissionControlService(store=MissionControlStore(tmp_path))
    visibility = InventoryVisibilityService(mission)
    item = proposal(step())

    visibility.publish_proposal("project-fleet", item, "operator", 100)
    visibility.publish_proposal("project-fleet", item, "operator", 100)

    records = visibility.list_records("project-fleet")
    assert len(records) == 1
    assert records[0]["record"]["proposal_id"] == item.proposal_id


def test_approval_execution_and_certification_are_visible(tmp_path):
    mission = MissionControlService(store=MissionControlStore(tmp_path))
    visibility = InventoryVisibilityService(mission)
    item = proposal(step())
    approved = approval(item, "inventory:read")
    execution = GovernedInventoryExecutor().execute(
        target=target(),
        proposal=item,
        approvals=(approved,),
        runner=lambda argv, timeout: InventoryResult(exit_code=0, stdout="ok"),
        timestamp=101,
    )
    certification = certify_fleet_inventory(item, execution)

    visibility.publish_approval("project-fleet", approved)
    visibility.publish_execution(
        "project-fleet",
        execution,
        "inventory-runner",
        101,
    )
    visibility.publish_certification(
        "project-fleet",
        item.proposal_id,
        certification,
        "certifier",
        102,
    )

    assert [record["kind"] for record in visibility.list_records("project-fleet")] == [
        "approval",
        "execution",
        "certification",
    ]
