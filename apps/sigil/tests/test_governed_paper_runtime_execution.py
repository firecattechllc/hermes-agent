from datetime import datetime, timezone

import pytest

from sigil.desktop_bridge import runtime


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
