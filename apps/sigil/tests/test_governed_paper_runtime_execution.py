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
    monkeypatch.setenv("SIGIL_ASSET_CATALOG_MODE", "demo")


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


def test_validated_production_proposal_fills_only_in_local_simulator() -> None:
    state = runtime._initial_state(NOW)
    state["positions"] = []
    state["balances"].update(
        {
            "cash": "10000.00",
            "portfolio_value": "10000.00",
            "reserved_cash": "0.00",
            "buying_power": "10000.00",
            "equity": "10000.00",
            "realized_pnl": "0.00",
            "unrealized_pnl": "0.00",
            "total_account_value": "10000.00",
        }
    )
    state["automation"]["state"] = "running"
    production = {
        "broker_submission": False,
        "last_proposal": {
            "proposal_id": "SIGIL-V21-PRP-VALIDATED",
            "strategy_id": "sigil-liquid-trend",
            "strategy_version": "2.1.0",
            "symbol": "PEN",
            "side": "buy",
            "proposed_notional": "25.00",
            "reference_price": "12.50",
            "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
            "status": "admitted_in_shadow",
            "evidence_identity": "evidence-checksum",
        },
    }

    executed = runtime._admit_production_proposal_to_local_simulator(
        state,
        production,
        sequence=1,
        execution_id="cycle-production-1",
        now=NOW,
    )

    assert executed is True
    assert state["positions"][0]["symbol"] == "PEN"
    assert state["positions"][0]["market_value"] == "25.00"
    assert state["last_execution"]["broker_submission_attempted"] is False
    assert state["broker_submission_available"] is False
    assert state["execution_authorized"] is False
    assert any(
        event["status"] == "production_paper_simulated"
        and event["details"]["notional"] == "25.00"
        and event["details"]["broker_submission_attempted"] is False
        for event in state["audit"]
    )


def test_production_local_simulator_fails_closed_on_expired_or_duplicate_proposal() -> None:
    state = runtime._initial_state(NOW)
    state["positions"] = []
    state["balances"].update(
        {
            "cash": "10000.00",
            "reserved_cash": "0.00",
            "buying_power": "10000.00",
            "equity": "10000.00",
            "realized_pnl": "0.00",
            "unrealized_pnl": "0.00",
            "total_account_value": "10000.00",
        }
    )
    state["automation"]["state"] = "running"
    proposal = {
        "proposal_id": "SIGIL-V21-PRP-EXPIRED",
        "strategy_id": "sigil-liquid-trend",
        "strategy_version": "2.1.0",
        "symbol": "PEN",
        "side": "buy",
        "proposed_notional": "25.00",
        "reference_price": "12.50",
        "expires_at": (NOW - timedelta(seconds=1)).isoformat(),
        "status": "admitted_in_shadow",
        "evidence_identity": "evidence-checksum",
    }

    assert (
        runtime._admit_production_proposal_to_local_simulator(
            state,
            {"broker_submission": False, "last_proposal": proposal},
            sequence=2,
            execution_id="cycle-production-2",
            now=NOW,
        )
        is False
    )
    assert state["positions"] == []
    assert state["executions"] == []
    assert state["audit"][0]["details"]["reason"] == "production_proposal_expired"


def _marked_position_state() -> dict[str, object]:
    state = runtime._initial_state(NOW)
    state["positions"] = [
        {
            "symbol": "AAPL",
            "quantity": "1",
            "average_cost": "100.00",
            "market_value": "100.00",
            "unrealized_pnl": "0.00",
            "entry_at": NOW.isoformat(),
            "entry_proposal_id": "SIGIL-V21-PRP-MARK",
            "strategy_id": "sigil-liquid-trend",
            "strategy_version": "2.1.0",
            "exit_plan": {
                "stop_loss_percent": "0.05",
                "take_profit_percent": "0.10",
                "maximum_holding_days": 10,
            },
        }
    ]
    state["balances"].update(
        {
            "cash": "9900.00",
            "reserved_cash": "0.00",
            "buying_power": "9900.00",
            "equity": "10000.00",
            "portfolio_value": "100.00",
            "realized_pnl": "0.00",
            "unrealized_pnl": "0.00",
            "total_account_value": "10000.00",
        }
    )
    return state


def test_position_monitor_persists_fresh_marks_and_exposes_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sigil.desktop_bridge import production_research

    state = _marked_position_state()
    fresh_mark = {
        "AAPL": {
            "status": "fresh",
            "price": "105.00",
            "timestamp": NOW.isoformat(),
            "source": "alpaca_iex_latest_quote_midpoint",
            "evidence_identity": "mark-evidence",
        }
    }
    monkeypatch.setattr(
        production_research,
        "collect_local_position_marks",
        lambda _symbols, *, now: fresh_mark,
    )
    result = runtime._monitor_local_simulated_positions(
        state, sequence=3, execution_id="cycle-mark-3", now=NOW
    )
    assert result["status"] == "fresh"
    assert state["positions"][0]["mark_price"] == "105.00"
    assert state["positions"][0]["mark_source"] == "alpaca_iex_latest_quote_midpoint"
    assert state["positions"][0]["unrealized_pnl"] == "5.00"
    assert state["balances"]["unrealized_pnl"] == "5.00"

    unavailable = {
        "AAPL": {
            "status": "stale",
            "timestamp": NOW.isoformat(),
            "source": "alpaca_iex_latest_quote_midpoint",
            "evidence_identity": "stale-evidence",
            "reason": "stale_quote",
        }
    }
    monkeypatch.setattr(
        production_research,
        "collect_local_position_marks",
        lambda _symbols, *, now: unavailable,
    )
    result = runtime._monitor_local_simulated_positions(
        state, sequence=4, execution_id="cycle-mark-4", now=NOW
    )
    assert result["status"] == "unavailable"
    assert state["positions"][0]["mark_status"] == "stale"
    assert state["positions"][0]["unrealized_pnl_status"] == "stale"
    assert state["balances"]["unrealized_pnl_status"] == "unavailable"
    assert state["positions"][0]["unrealized_pnl"] == "5.00"


def test_governed_local_stop_exit_preserves_realized_pnl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sigil.desktop_bridge import production_research

    state = _marked_position_state()
    monkeypatch.setattr(
        production_research,
        "collect_local_position_marks",
        lambda _symbols, *, now: {
            "AAPL": {
                "status": "fresh",
                "price": "94.00",
                "timestamp": now.isoformat(),
                "source": "alpaca_iex_latest_quote_midpoint",
                "evidence_identity": "stop-evidence",
            }
        },
    )
    result = runtime._monitor_local_simulated_positions(
        state, sequence=5, execution_id="cycle-exit-5", now=NOW
    )
    assert result["exit_triggered"] == "protective_stop"
    assert state["positions"] == []
    assert state["balances"]["realized_pnl"] == "-6.00"
    assert state["closed_positions"][0]["exit_trigger"] == "protective_stop"
    assert state["orders"][result["exit_order_id"]]["side"] == "SELL"
    assert state["last_execution"]["broker_submission_attempted"] is False


def test_paper_automation_state_survives_runtime_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    state_directory = tmp_path / "restart-safe-paper-state"
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(state_directory))

    paused = runtime.control_paper_cycle("pause", now=NOW)

    assert paused["automation"]["state"] == "paused"

    state_path = state_directory / "runtime-state.json"
    assert state_path.exists()

    envelope = json.loads(state_path.read_text(encoding="utf-8"))
    assert envelope["payload"]["automation"]["state"] == "paused"
    assert len(envelope["sha256"]) == 64

    restored = runtime.runtime_snapshot(now=NOW + timedelta(seconds=1))

    assert restored["automation"]["state"] == "paused"
    assert restored["revision"] >= paused["revision"]
    assert restored["broker_submission_available"] is False


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


def test_running_healthy_runtime_visibility_projection() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    snapshot = runtime.runtime_snapshot(now=NOW)
    visibility = snapshot["runtime_visibility"]

    assert visibility["operational_state"] == "running"
    assert visibility["health"] == "healthy"
    assert visibility["paper_execution_available"] is True
    assert visibility["counts"]["cycles"] == 1
    assert visibility["counts"]["proposals"] == 1


def test_paused_runtime_visibility_projection() -> None:
    snapshot = runtime.control_paper_cycle("pause", now=NOW)
    visibility = snapshot["runtime_visibility"]

    assert visibility["operational_state"] == "paused"
    assert visibility["pause_cause"] == "manual"
    assert any(
        reason["code"] == "automation_paused"
        for reason in visibility["blocking_reasons"]
    )


def test_stopped_runtime_visibility_projection() -> None:
    snapshot = runtime.runtime_snapshot(now=NOW)

    assert snapshot["runtime_visibility"]["operational_state"] == "stopped"
    assert any(
        reason["code"] == "automation_stopped"
        for reason in snapshot["runtime_visibility"]["blocking_reasons"]
    )


def test_inactive_authorization_is_a_visibility_blocker() -> None:
    snapshot = runtime.control_paper_authorization("revoke", now=NOW)

    assert snapshot["runtime_visibility"]["paper_execution_available"] is False
    assert any(
        reason["code"] == "authorization_revoked"
        and reason["severity"] == "critical"
        for reason in snapshot["runtime_visibility"]["blocking_reasons"]
    )


def test_unhealthy_runtime_auto_pause_is_visible_and_recovery_stays_paused() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    with runtime._locked_state() as (state_path, state):
        state["balances"]["buying_power"] = "1.00"
        runtime._persist(state_path, state)

    unhealthy = runtime.runtime_snapshot(now=NOW + timedelta(seconds=1))

    assert unhealthy["automation"]["state"] == "paused"
    assert unhealthy["runtime_visibility"]["pause_cause"] == "safety"
    assert unhealthy["runtime_visibility"]["health"] == "degraded"
    assert any(
        reason["code"] == "automation_safety_paused"
        and reason["requires_manual_resume"] is True
        for reason in unhealthy["runtime_visibility"]["blocking_reasons"]
    )
    assert unhealthy["audit"][0]["status"] == "safety_paused"

    with runtime._locked_state() as (state_path, state):
        state["balances"]["buying_power"] = state["balances"]["cash"]
        runtime._persist(state_path, state)

    recovered = runtime.runtime_snapshot(now=NOW + timedelta(seconds=2))

    assert recovered["runtime_visibility"]["health"] == "healthy"
    assert recovered["automation"]["state"] == "paused"
    assert recovered["runtime_visibility"]["pause_cause"] == "safety"
    assert "explicitly resume" in recovered["runtime_visibility"]["next_action"]


def test_broker_unavailable_is_separate_from_local_paper_availability() -> None:
    snapshot = runtime.runtime_snapshot(now=NOW)
    visibility = snapshot["runtime_visibility"]

    assert visibility["paper_execution_available"] is True
    assert visibility["broker_submission_available"] is False
    broker_reason = next(
        reason
        for reason in visibility["blocking_reasons"]
        if reason["code"] == "broker_submission_unavailable"
    )
    assert broker_reason["severity"] == "info"
    assert "local paper simulation remains separate" in broker_reason["summary"]


def test_alpha_1_3_persisted_state_upgrades_safely(tmp_path) -> None:
    snapshot = runtime.runtime_snapshot(now=NOW)
    snapshot["schema_version"] = 3
    snapshot["automation"].pop("pause_cause")
    snapshot["automation"].pop("pause_reason")
    snapshot.pop("runtime_visibility")
    state_path = tmp_path / "paper-state" / "runtime-state.json"
    state_path.write_text(
        json.dumps({"payload": snapshot, "sha256": runtime._digest(snapshot)})
    )

    upgraded = runtime.runtime_snapshot(now=NOW + timedelta(seconds=1))

    assert upgraded["schema_version"] == runtime.SCHEMA_VERSION
    assert upgraded["automation"]["pause_cause"] is None
    assert upgraded["runtime_visibility"]["health"] == "healthy"
    assert upgraded["broker_submission_available"] is False
