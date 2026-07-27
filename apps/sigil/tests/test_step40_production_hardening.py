"""Step 40 production-hardening certification for the local paper runtime."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

from sigil.desktop_bridge import runtime

NOW = datetime(2026, 7, 27, 20, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(tmp_path / "step40-paper-state"),
    )


def paper_buy(order_id: str) -> dict[str, object]:
    return {
        "order_id": order_id,
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": "1",
        "reference_price": "100",
        "environment": "paper",
        "broker_submission": False,
    }


def test_runtime_recovers_persisted_order_after_module_reload() -> None:
    submitted = runtime.submit_paper_order(
        paper_buy("STEP40-RESTART-1"),
        now=NOW,
    )

    assert submitted["status"] == "open"

    reloaded_runtime = importlib.reload(runtime)
    recovered = reloaded_runtime.runtime_snapshot(now=NOW)

    assert recovered["orders"]["STEP40-RESTART-1"]["status"] == "open"
    assert recovered["balances"]["reserved_cash"] == "100.00"
    assert recovered["balances"]["buying_power"] == "9900.00"
    assert recovered["runtime_health"] == "healthy"


def test_duplicate_order_identifier_is_rejected_after_restart() -> None:
    runtime.submit_paper_order(
        paper_buy("STEP40-DUPLICATE-1"),
        now=NOW,
    )

    reloaded_runtime = importlib.reload(runtime)

    with pytest.raises(ValueError, match="order_id already exists"):
        reloaded_runtime.submit_paper_order(
            paper_buy("STEP40-DUPLICATE-1"),
            now=NOW,
        )

    snapshot = reloaded_runtime.runtime_snapshot(now=NOW)

    matching_orders = [
        order_id
        for order_id in snapshot["orders"]
        if order_id == "STEP40-DUPLICATE-1"
    ]
    assert matching_orders == ["STEP40-DUPLICATE-1"]


def test_reconciliation_requirement_blocks_automation_start() -> None:
    runtime.runtime_snapshot(now=NOW)

    with runtime._locked_state() as (state_path, state):
        state["reconciliation"].insert(
            0,
            {
                "order_id": "STEP40-AMBIGUOUS-1",
                "status": "submission-outcome-ambiguous",
                "required": True,
                "automatic_retry_allowed": False,
                "timestamp": NOW.isoformat().replace("+00:00", "Z"),
                "evidence_reference": "STEP40:FAILURE-INJECTION",
            },
        )
        runtime._persist(state_path, state)

    with pytest.raises(
        ValueError,
        match="runtime health is recovery_required",
    ):
        runtime.control_paper_cycle("start", now=NOW)

    snapshot = runtime.runtime_snapshot(now=NOW)

    assert snapshot["runtime_health"] == "recovery_required"
    assert snapshot["automation"]["state"] != "running"
    assert snapshot["automation"]["next_cycle_at"] is None


def test_running_automation_pauses_when_runtime_becomes_unsafe() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    running = runtime.runtime_snapshot(now=NOW)

    assert running["automation"]["state"] == "running"

    with runtime._locked_state() as (state_path, state):
        state["reconciliation"].insert(
            0,
            {
                "order_id": "STEP40-AMBIGUOUS-2",
                "status": "reconciliation-required",
                "required": True,
                "automatic_retry_allowed": False,
                "timestamp": NOW.isoformat().replace("+00:00", "Z"),
                "evidence_reference": "STEP40:AUTO-PAUSE",
            },
        )
        runtime._persist(state_path, state)

    recovered = runtime.runtime_snapshot(now=NOW)

    assert recovered["runtime_health"] == "recovery_required"
    assert recovered["automation"]["state"] == "paused"
    assert recovered["automation"]["next_cycle_at"] is None


def test_disconnected_runtime_is_not_reported_healthy() -> None:
    runtime.runtime_snapshot(now=NOW)

    with runtime._locked_state() as (state_path, state):
        state["connection"]["status"] = "disconnected"
        runtime._persist(state_path, state)

    status = runtime.runtime_mission_control_status()

    assert status["runtime_health"] == "degraded"
    assert status["paper_mode"] is True
    assert status["broker_execution_disabled"] is True


def test_corrupt_balance_fails_closed() -> None:
    runtime.runtime_snapshot(now=NOW)

    with runtime._locked_state() as (state_path, state):
        state["balances"]["reserved_cash"] = "-1.00"
        runtime._persist(state_path, state)

    snapshot = runtime.runtime_snapshot(now=NOW)

    assert snapshot["runtime_health"] == "corrupt"
    assert snapshot["automation"]["state"] != "running"

    with pytest.raises(
        ValueError,
        match="runtime health is corrupt",
    ):
        runtime.control_paper_cycle("start", now=NOW)
