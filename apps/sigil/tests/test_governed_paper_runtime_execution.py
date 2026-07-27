import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sigil.desktop_bridge import runtime
from sigil.desktop_bridge.paper_execution import evaluate_runtime_health

NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path / "paper-state"))


def buy(order_id: str = "ORD-1") -> dict[str, object]:
    return {
        "order_id": order_id,
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": "4",
        "reference_price": "100",
        "environment": "paper",
    }


def test_buy_partial_fill_and_recovery() -> None:
    submitted = runtime.submit_paper_order(buy(), now=NOW)
    assert submitted["status"] == "open"

    partial = runtime.simulate_paper_fill("ORD-1", {"AAPL": "90"}, quantity="1.5", now=NOW)
    assert partial["status"] == "partially_filled"
    complete = runtime.simulate_paper_fill("ORD-1", {"AAPL": "100"}, now=NOW)
    assert complete["status"] == "filled"

    recovered = runtime.runtime_snapshot(now=NOW)
    assert recovered["orders"]["ORD-1"]["status"] == "filled"
    assert recovered["balances"]["cash"] == "9615.00"
    assert recovered["balances"]["reserved_cash"] == "0.00"
    assert any(event["status"] == "order_filled" for event in recovered["audit"])


def test_cancel_releases_buying_power() -> None:
    runtime.submit_paper_order(buy(), now=NOW)
    cancelled = runtime.cancel_paper_order("ORD-1", now=NOW)

    assert cancelled["status"] == "cancelled"
    snapshot = runtime.runtime_snapshot(now=NOW)
    assert snapshot["balances"]["reserved_cash"] == "0.00"
    assert snapshot["balances"]["buying_power"] == snapshot["balances"]["cash"]


def test_rejection_is_audited_and_live_is_refused() -> None:
    rejected = runtime.submit_paper_order({**buy(), "order_id": "ORD-2", "quantity": "100000"}, now=NOW)
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError, match="only local paper"):
        runtime.submit_paper_order({**buy(), "order_id": "ORD-3", "environment": "live"}, now=NOW)


def test_limit_and_stop_orders_remain_deterministic() -> None:
    limit = runtime.submit_paper_order({**buy(), "order_id": "ORD-4", "order_type": "LIMIT", "limit_price": "90"}, now=NOW)
    assert runtime.simulate_paper_fill(limit["id"], {"AAPL": "91"}, now=NOW)["status"] == "open"
    assert runtime.simulate_paper_fill(limit["id"], {"AAPL": "90"}, now=NOW)["status"] == "filled"
    stop = runtime.submit_paper_order({**buy(), "order_id": "ORD-5", "order_type": "STOP", "stop_price": "110"}, now=NOW)
    assert runtime.simulate_paper_fill(stop["id"], {"AAPL": "120"}, now=NOW)["status"] == "open"


def test_mission_control_status_is_paper_only() -> None:
    runtime.submit_paper_order(buy(), now=NOW)
    status = runtime.runtime_mission_control_status()

    assert status["paper_mode"] is True
    assert status["broker_execution_disabled"] is True
    assert status["runtime_health"] == "healthy"
    assert len(status["open_orders"]) == 1


def test_monthly_authorization_gates_automatic_paper_buys_and_sells() -> None:
    initial = runtime.runtime_snapshot(now=NOW)
    authorization = initial["paper_authorization"]
    assert authorization["status"] == "active"
    assert authorization["authorization_month"] == "2026-07"
    assert authorization["automatic_monthly_policy"] is True
    assert authorization["scope"] == [
        "automatic-paper-approval",
        "simulated-paper-buy",
        "simulated-paper-sell",
    ]

    runtime.control_paper_cycle("start", now=NOW)
    first = runtime.runtime_snapshot(now=NOW)
    second = runtime.runtime_snapshot(now=NOW + timedelta(seconds=5))

    assert first["proposals"][0]["status"] == "approved"
    assert first["proposals"][0]["side"] == "BUY"
    assert Decimal(first["proposals"][0]["estimated_notional"]) > Decimal(25)
    assert second["proposals"][0]["side"] == "SELL"
    assert second["executions"][0]["side"] == "SELL"
    assert second["executions"][1]["side"] == "BUY"
    assert all(
        execution["broker_submission_attempted"] is False
        for execution in second["executions"]
    )
    assert any(
        event["status"] == "paper_auto_approved"
        and event["details"]["authorization_id"]
        == authorization["authorization_id"]
        for event in second["audit"]
    )
    assert any(
        event["status"] == "paper_executed"
        and event["details"]["side"] == "SELL"
        for event in second["audit"]
    )


def test_monthly_revocation_is_fail_closed_until_next_calendar_month() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    revoked = runtime.control_paper_authorization("revoke", now=NOW)
    assert revoked["paper_authorization"]["status"] == "revoked"
    assert revoked["automation"]["state"] == "paused"
    assert any(
        event["status"] == "authorization_revoked" for event in revoked["audit"]
    )
    with pytest.raises(ValueError, match="revoked for this calendar month"):
        runtime.control_paper_authorization(
            "grant", now=NOW + timedelta(days=1)
        )
    with pytest.raises(ValueError, match="monthly paper authorization"):
        runtime.control_paper_cycle("start", now=NOW + timedelta(days=1))

    next_month = runtime.runtime_snapshot(
        now=datetime(2026, 8, 1, tzinfo=timezone.utc)
    )
    assert next_month["paper_authorization"]["status"] == "active"
    assert next_month["paper_authorization"]["authorization_month"] == "2026-08"
    assert next_month["automation"]["state"] == "paused"
    assert any(
        event["status"] == "monthly_authorization_started"
        and event["details"]["authorization_month"] == "2026-08"
        for event in next_month["audit"]
    )


def test_revoked_running_state_pauses_fail_closed() -> None:
    with runtime._locked_state() as (state_path, state):
        state["automation"]["state"] = "running"
        state["automation"]["next_cycle_at"] = NOW.isoformat()
        state["paper_authorization"].update(
            {"status": "revoked", "authorization_month": "2026-07"}
        )
        runtime._persist(state_path, state)

    snapshot = runtime.runtime_snapshot(now=NOW)

    assert snapshot["automation"]["state"] == "paused"
    assert snapshot["automation"]["cycle_count"] == 0
    assert snapshot["proposals"] == []
    assert snapshot["broker_submission_available"] is False


def test_paper_sell_is_rejected_before_fill_when_quantity_exceeds_holdings() -> None:
    rejected = runtime.submit_paper_order(
        {
            "order_id": "ORD-SELL-TOO-MUCH",
            "symbol": "MSFT",
            "side": "SELL",
            "order_type": "MARKET",
            "quantity": "999",
            "reference_price": "452.80",
            "environment": "paper",
        },
        now=NOW,
    )

    assert rejected["status"] == "rejected"
    assert rejected["reason"] == "insufficient_position_quantity"
    snapshot = runtime.runtime_snapshot(now=NOW)
    assert any(
        event["status"] == "order_rejected"
        and event["details"]["reason"] == "insufficient_position_quantity"
        for event in snapshot["audit"]
    )


def test_reset_requires_exact_confirmation_and_preserves_auditable_evidence(
    tmp_path,
) -> None:
    runtime.control_paper_authorization("grant", now=NOW)
    runtime.control_paper_cycle("start", now=NOW)
    populated = runtime.runtime_snapshot(now=NOW)
    assert populated["positions"]
    assert populated["executions"]

    with pytest.raises(ValueError, match="exact paper reset confirmation"):
        runtime.reset_paper_runtime("reset", now=NOW + timedelta(seconds=1))

    reset = runtime.reset_paper_runtime(
        "RESET LOCAL PAPER PORTFOLIO",
        now=NOW + timedelta(seconds=2),
    )

    assert reset["positions"] == []
    assert reset["proposals"] == []
    assert reset["executions"] == []
    assert reset["reconciliation"] == []
    assert reset["balances"]["cash"] == "10000.00"
    assert reset["balances"]["total_account_value"] == "10000.00"
    assert reset["automation"]["state"] == "stopped"
    assert reset["paper_authorization"]["status"] == "active"
    assert reset["paper_authorization"]["authorization_month"] == "2026-07"
    assert reset["broker_submission_available"] is False
    assert reset["audit"][0]["status"] == "paper_runtime_reset"
    assert reset["audit"][0]["details"]["settings_preserved"] is True
    assert reset["audit"][0]["details"]["credentials_preserved"] is True

    evidence_path = tmp_path / "paper-state" / "runtime-reset-audit.jsonl"
    evidence = json.loads(evidence_path.read_text().strip())
    assert evidence["record"]["scope"] == "local-paper-runtime-only"
    assert evidence["record"]["provider_mutation"] is False
    assert evidence["record"]["broker_submission_attempted"] is False
    assert len(evidence["record"]["previous_state_sha256"]) == 64
    assert len(evidence["sha256"]) == 64


def test_runtime_health_requires_reconciliation_when_flagged() -> None:
    state = runtime.runtime_snapshot(now=NOW)
    state["reconciliation"].insert(
        0,
        {
            "order_id": "ORD-RECOVERY",
            "status": "ambiguous",
            "required": True,
        },
    )

    assert evaluate_runtime_health(state) == "recovery_required"
    assert state["runtime_health"] == "recovery_required"


def test_runtime_health_detects_corrupt_negative_balance() -> None:
    state = runtime.runtime_snapshot(now=NOW)
    state["balances"]["reserved_cash"] = "-1.00"

    assert evaluate_runtime_health(state) == "corrupt"
    assert state["runtime_health"] == "corrupt"


def test_runtime_health_detects_balance_drift() -> None:
    state = runtime.runtime_snapshot(now=NOW)
    state["balances"]["buying_power"] = "1.00"

    assert evaluate_runtime_health(state) == "degraded"
    assert state["runtime_health"] == "degraded"


def test_runtime_health_requires_recovery_for_invalid_open_order() -> None:
    state = runtime.runtime_snapshot(now=NOW)
    state["orders"]["ORD-BROKEN"] = {
        "id": "ORD-BROKEN",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": "1",
        "filled_quantity": "0",
        "reserved_cash": "100.00",
        "status": "open",
    }

    assert evaluate_runtime_health(state) == "recovery_required"
    assert state["runtime_health"] == "recovery_required"


def test_runtime_health_is_healthy_for_consistent_state() -> None:
    state = runtime.runtime_snapshot(now=NOW)

    assert evaluate_runtime_health(state) == "healthy"
    assert state["runtime_health"] == "healthy"
