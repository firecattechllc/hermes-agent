from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sigil.autonomous_paper import (
    ALPACA_PAPER_BASE_URL,
    AlpacaPaperClient,
    GovernedPaperExecutionService,
    PaperExecutionStore,
)
from sigil.autonomous_paper.alpaca import AlpacaPaperTransportError

NOW = datetime(2026, 7, 29, 15, 30, tzinfo=UTC)


class ExitTransport:
    def __init__(self, store: PaperExecutionStore) -> None:
        self.store = store
        self.calls: list[tuple[str, str, object | None]] = []
        self.ambiguous = False
        self.lookup: dict[str, Any] | None = None
        self.intent_persisted_before_post = False

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: object | None,
        timeout: float,
    ) -> tuple[int, object]:
        del headers, timeout
        assert url.startswith(f"{ALPACA_PAPER_BASE_URL}/v2/")
        self.calls.append((method, url, body))
        if url.endswith("/v2/clock"):
            return 200, {"is_open": True}
        if "/v2/orders:by_client_order_id?" in url:
            return (200, self.lookup) if self.lookup else (404, {})
        if url.endswith("/v2/account"):
            return 200, {"id": "paper", "status": "ACTIVE", "cash": "1000"}
        if url.endswith("/v2/positions"):
            return 200, []
        if "/v2/orders?" in url:
            return 200, []
        if url.endswith("/v2/orders") and method == "POST":
            envelope = json.loads(self.store.path.read_text())
            self.intent_persisted_before_post = (
                envelope["payload"]["exit_intents"][0]["status"] == "submission_pending"
            )
            if self.ambiguous:
                raise AlpacaPaperTransportError("request_timeout", ambiguous=True)
            assert isinstance(body, dict)
            return 201, {
                "id": "exit-order",
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": "sell",
                "status": "accepted",
                "qty": body["qty"],
            }
        raise AssertionError(f"unexpected request {method} {url}")


def setup_service(
    tmp_path: Path,
    *,
    entered_at: datetime = NOW - timedelta(days=1),
    symbol: str = "AAPL",
) -> tuple[GovernedPaperExecutionService, ExitTransport]:
    store = PaperExecutionStore(tmp_path.resolve())
    transport = ExitTransport(store)
    service = GovernedPaperExecutionService(
        store,
        AlpacaPaperClient("key", "secret", transport=transport),
    )
    with store.locked() as state:
        state.update(
            {
                "activated": True,
                "broker_submission": True,
                "kill_switch": False,
                "paused": False,
                "reconciliation_complete": True,
                "positions": [
                    {
                        "symbol": symbol,
                        "qty": "0.25",
                        "market_value": "25",
                    }
                ],
                "fills": [
                    {
                        "fill_id": "entry-fill",
                        "client_order_id": "entry-order",
                        "symbol": symbol,
                        "side": "buy",
                        "filled_qty": "0.25",
                        "entry_basis": "100",
                        "status": "filled",
                    }
                ],
                "exit_plans": [
                    {
                        "exit_plan_id": "plan-aapl",
                        "symbol": symbol,
                        "entry_client_order_id": "entry-order",
                        "entry_basis": "100",
                        "entry_quantity": "0.25",
                        "entered_at": entered_at.isoformat().replace("+00:00", "Z"),
                        "stop_price": "95",
                        "profit_price": "110",
                        "maximum_holding_days": 10,
                        "status": "paper_position_monitoring",
                    }
                ],
            }
        )
        store.save(state)
    return service, transport


@pytest.mark.parametrize(
    ("price", "trigger"),
    [
        (Decimal("94.99"), "protective_stop"),
        (Decimal("110.01"), "profit_taking"),
    ],
)
def test_price_trigger_persists_exit_intent_before_paper_submission(tmp_path, price, trigger):
    service, transport = setup_service(tmp_path)
    status = service.monitor_positions({"AAPL": price}, now=NOW)
    assert transport.intent_persisted_before_post is True
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    body = next(call for call in transport.calls if call[0] == "POST")[2]
    assert body["side"] == "sell"
    assert body["qty"] == "0.25"
    assert body["time_in_force"] == "day"
    assert body["extended_hours"] is False
    assert status["last_exit_intent"]["trigger"] == trigger


def test_maximum_hold_and_strategy_invalidation_trigger_exits(tmp_path):
    held, _ = setup_service(tmp_path / "held", entered_at=NOW - timedelta(days=11))
    result = held.monitor_positions({"AAPL": Decimal(100)}, now=NOW)
    assert result["last_exit_intent"]["trigger"] == "maximum_holding_period"
    invalid, _ = setup_service(tmp_path / "invalid")
    result = invalid.monitor_positions(
        {"AAPL": Decimal(100)},
        now=NOW,
        invalidated_symbols=frozenset({"AAPL"}),
    )
    assert result["last_exit_intent"]["trigger"] == "strategy_invalidation"


def test_duplicate_exit_is_blocked(tmp_path):
    service, transport = setup_service(tmp_path)
    service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    service.monitor_positions({"AAPL": Decimal(93)}, now=NOW + timedelta(minutes=1))
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1


def test_exit_timeout_reconciles_by_client_id_without_resubmission(tmp_path):
    service, transport = setup_service(tmp_path)
    transport.ambiguous = True
    transport.lookup = {
        "id": "existing-exit",
        "symbol": "AAPL",
        "side": "sell",
        "status": "accepted",
        "qty": "0.25",
    }
    result = service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    assert len([call for call in transport.calls if call[0] == "POST"]) == 1
    assert len([call for call in transport.calls if "by_client_order_id" in call[1]]) == 1
    assert result["last_exit_intent"]["status"] == "accepted"


def test_pause_blocks_protective_exit_but_emergency_is_explicit(tmp_path):
    service, transport = setup_service(tmp_path)
    with service.store.locked() as state:
        state["paused"] = True
        service.store.save(state)
    service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    assert not [call for call in transport.calls if call[0] == "POST"]
    result = service.monitor_positions({"AAPL": Decimal(100)}, now=NOW, emergency=True)
    assert result["last_exit_intent"]["trigger"] == "emergency_exit"


def test_deactivated_monitor_makes_no_broker_call(tmp_path):
    service, transport = setup_service(tmp_path)
    with service.store.locked() as state:
        state["activated"] = False
        state["broker_submission"] = False
        state["kill_switch"] = True
        service.store.save(state)
    result = service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    assert result["broker_submission"] is False
    assert transport.calls == []


def test_external_position_is_not_adopted_or_managed(tmp_path):
    service, transport = setup_service(tmp_path, symbol="EXTERNAL")
    with service.store.locked() as state:
        state["fills"] = []
        state["exit_plans"] = []
        service.store.save(state)
    result = service.monitor_positions({"EXTERNAL": Decimal(1)}, now=NOW)
    assert result["degraded_conditions"] == ["unmanaged_paper_position"]
    assert result["exit_plan_count"] == 0
    assert not [call for call in transport.calls if call[0] == "POST"]


def test_partial_exit_fill_reconciles_after_restart(tmp_path):
    service, transport = setup_service(tmp_path)
    service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    client_id = service.status()["last_exit_intent"]["client_order_id"]
    transport.lookup = {
        "id": "exit-order",
        "client_order_id": client_id,
        "symbol": "AAPL",
        "side": "sell",
        "status": "partially_filled",
        "qty": "0.25",
        "filled_qty": "0.10",
        "filled_avg_price": "94",
    }
    restarted = GovernedPaperExecutionService(service.store, service.client)
    result = restarted.reconcile()
    assert result["last_exit_intent"]["status"] == "partially_filled"
    assert restarted.recent("fills")["items"][0]["side"] == "sell"
    assert restarted.recent("exit_plans")["items"][0]["status"] == ("paper_exit_partial")


def test_live_execution_remains_false_during_exit_lifecycle(tmp_path):
    service, _ = setup_service(tmp_path)
    result = service.monitor_positions({"AAPL": Decimal(94)}, now=NOW)
    assert result["environment"] == "paper"
    assert result["live_execution"] is False
    assert result["broker_base_url"] == ALPACA_PAPER_BASE_URL
