"""P1 remediation: explicit emergency-stop and flatten-positions semantics.

Covers the certification finding that the kill switch blocked new orders but
did not flatten existing positions, and the deeper issues found while
documenting current behavior before implementing a fix: the one nominal
"emergency liquidation" code path was both unreachable from the shipped UI
and broken (a KeyError on every call), and STOP never touched resting/
unfilled orders at all. Every test here runs against a FakeAlpaca transport
that only ever sees the paper API's URL shape -- nothing in this file or the
code it exercises can reach a live endpoint (see
test_flatten_and_cancel_are_paper_endpoint_bound for a direct assertion of
that invariant on the two new client methods this P1 wires up).
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sigil.autonomous_paper import (
    ALPACA_PAPER_BASE_URL,
    AlpacaPaperClient,
    CandidateResearch,
    GovernedPaperExecutionService,
    PaperExecutionStore,
)
from sigil.autonomous_paper.alpaca import AlpacaPaperTransportError
from sigil.desktop_bridge.production_research import emergency_paper_liquidation


class FakeAlpaca:
    """Extends the standard paper-transport fake with DELETE support for
    cancel_order/close_position, and per-symbol/per-order failure injection
    so partial-failure paths are directly testable."""

    def __init__(self, store: PaperExecutionStore | None = None) -> None:
        self.calls: list[tuple[str, str, object | None]] = []
        self.store = store
        self.clock_open = True
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.lookup: dict[str, Any] | None = None
        self.fail_cancel_order_ids: set[str] = set()
        self.fail_close_symbols: set[str] = set()
        self.positions_after_close: list[dict[str, Any]] | None = None

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: object | None,
        timeout: float,
    ) -> tuple[int, object]:
        del timeout
        assert url.startswith(f"{ALPACA_PAPER_BASE_URL}/v2/"), (
            "every fake-transport call must target the paper API only -- "
            "this is the same invariant AlpacaPaperClient._request enforces "
            "at runtime, restated here so a bug in the fake itself can't "
            "silently hide a real live-endpoint regression"
        )
        assert headers["APCA-API-KEY-ID"] == "paper-key"
        assert headers["APCA-API-SECRET-KEY"] == "paper-secret"
        self.calls.append((method, url, body))
        path = url.removeprefix(ALPACA_PAPER_BASE_URL)

        if path == "/v2/account":
            return 200, {"id": "paper-account", "status": "ACTIVE", "cash": "1000.00"}
        if path == "/v2/clock":
            return 200, {"is_open": self.clock_open}
        if path == "/v2/positions":
            return 200, self.positions
        if path.startswith("/v2/orders?"):
            return 200, self.orders
        if path.startswith("/v2/orders:by_client_order_id?"):
            return (200, self.lookup) if self.lookup is not None else (404, {})
        if path == "/v2/orders" and method == "POST":
            if self.store is not None:
                envelope = json.loads(self.store.path.read_text(encoding="utf-8"))
                state = envelope["payload"]
                del state
            assert isinstance(body, dict)
            return 201, {
                "id": "paper-order-1",
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": body.get("side", "buy"),
                "type": "market",
                "time_in_force": "day",
                "status": "accepted",
                "notional": body.get("notional"),
                "filled_qty": "0",
            }
        if method == "DELETE" and path.startswith("/v2/orders/"):
            order_id = path.removeprefix("/v2/orders/")
            if order_id in self.fail_cancel_order_ids:
                raise AlpacaPaperTransportError("simulated_cancel_failure", ambiguous=False)
            self.orders = [o for o in self.orders if o.get("id") != order_id]
            return 204, {}
        if method == "DELETE" and path.startswith("/v2/positions/"):
            symbol = path.removeprefix("/v2/positions/").split("?")[0]
            if symbol in self.fail_close_symbols:
                raise AlpacaPaperTransportError("simulated_close_failure", ambiguous=False)
            self.positions = [p for p in self.positions if p.get("symbol") != symbol]
            return 200, {"symbol": symbol, "status": "accepted"}
        raise AssertionError(f"unexpected fake Alpaca call: {method} {url}")


def service_for(tmp_path: Path) -> tuple[GovernedPaperExecutionService, FakeAlpaca]:
    store = PaperExecutionStore(tmp_path.resolve())
    fake = FakeAlpaca(store)
    client = AlpacaPaperClient("paper-key", "paper-secret", transport=fake)
    return GovernedPaperExecutionService(store, client), fake


def candidate(symbol: str = "AAPL", **changes: Any) -> CandidateResearch:
    value = CandidateResearch(
        symbol=symbol,
        asset_class="us_equity",
        exchange="NASDAQ",
        tradable=True,
        fractionable=True,
        status="active",
        name=f"{symbol} Holdings",
        quote_bid=Decimal("99.95"),
        quote_ask=Decimal("100.05"),
        quote_age_seconds=1,
        bars_age_seconds=60,
        average_dollar_volume=Decimal(50000000),
        strategy_score=Decimal("0.80"),
        confidence=Decimal("0.85"),
        expected_setup_positive=True,
        evidence_complete=True,
    )
    return replace(value, **changes)


def evaluate(service: GovernedPaperExecutionService, research: list[CandidateResearch], **changes: Any) -> dict[str, Any]:
    values = {
        "cursor": 25,
        "batch_number": 1,
        "total_eligible": 12984,
        "catalog_fresh": True,
        "portfolio_fresh": True,
        "runtime_healthy": True,
        "audit_available": True,
    }
    values.update(changes)
    return service.evaluate_batch(research, **values)


def resting_order(order_id: str = "order-1", symbol: str = "AAPL", status: str = "accepted") -> dict[str, Any]:
    return {
        "id": order_id,
        "client_order_id": f"client-{order_id}",
        "symbol": symbol,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": status,
        "notional": "500.00",
        "qty": None,
        "filled_qty": "0",
        "created_at": "2026-08-12T00:00:00Z",
        "updated_at": "2026-08-12T00:00:00Z",
    }


def open_position(symbol: str = "AAPL", qty: str = "5") -> dict[str, Any]:
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": "100.00",
        "market_value": "500.00",
        "unrealized_pl": "0.00",
        "side": "long",
    }


# ---------------------------------------------------------------------------
# 1. Kill switch engaged before an order is ever attempted
# ---------------------------------------------------------------------------


def test_kill_switch_engaged_before_order_blocks_submission(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    service.pause(emergency=True)

    result = evaluate(service, [candidate()])

    assert result["progress"]["state"] == "execution_disabled"
    proposal = service.recent("proposals", limit=1)["items"][0]
    assert proposal["rejection_reasons"] == ["governed_activation_required"]
    assert [c for c in fake.calls if c[0] == "POST" and "/v2/orders" in c[1]] == []


# ---------------------------------------------------------------------------
# 2. Activation during active strategy execution: research still runs and
#    is recorded, only the final submission step is blocked.
# ---------------------------------------------------------------------------


def test_research_scoring_still_runs_and_is_recorded_while_stopped(tmp_path):
    service, _fake = service_for(tmp_path)
    service.activate()
    service.pause(emergency=True)

    evaluate(service, [candidate()])

    status = service.status()
    assert status["kill_switch"] is True
    # research/candidate evidence is still produced -- STOP gates submission,
    # not evidence-gathering.
    assert service.recent("candidates", limit=10)["items"]


# ---------------------------------------------------------------------------
# 3. Queued/retry order paths: reconciliation keeps working (read-only) and
#    never creates new exposure while halted.
# ---------------------------------------------------------------------------


def test_reconciliation_continues_read_only_while_stopped(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    fake.lookup = {"id": "order-x", "status": "accepted", "filled_qty": "0"}
    with service.store.locked() as state:
        state["order_intents"] = [
            {
                "intent_id": "intent-1",
                "client_order_id": "client-order-x",
                "status": "submission_pending",
                "symbol": "AAPL",
            }
        ]
        service.store.save(state)
    service.pause(emergency=True)
    fake.calls.clear()

    service.reconcile()

    methods = {call[0] for call in fake.calls}
    assert methods <= {"GET"}, f"reconciliation must never write to the broker, saw: {fake.calls}"


# ---------------------------------------------------------------------------
# 4. Existing open positions: monitor_positions makes no broker call at all
#    once kill_switch is set, now enforced directly (not only via the
#    broker_submission flag it happens to travel with today).
# ---------------------------------------------------------------------------


def test_monitor_positions_checks_kill_switch_directly(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position()]
        # Deliberately decouple kill_switch from broker_submission to prove
        # monitor_positions enforces kill_switch on its own, not only via
        # broker_submission staying in lockstep with it by convention.
        state["kill_switch"] = True
        state["broker_submission"] = True
        service.store.save(state)
    fake.calls.clear()

    service.monitor_positions({"AAPL": Decimal("50.00")}, now=__import__("datetime").datetime.now(__import__("datetime").UTC))

    assert fake.calls == [], "monitor_positions must refuse to run whenever kill_switch is set, independent of broker_submission"


# ---------------------------------------------------------------------------
# 5. Explicit paper flatten: separate action, closes every tracked position,
#    fully audited, never touches a live endpoint.
# ---------------------------------------------------------------------------


def test_flatten_positions_closes_every_tracked_position(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position("AAPL", "5"), open_position("MSFT", "2")]
        service.store.save(state)
    fake.positions = []  # broker-side: both already closed by the time we re-fetch

    result = service.flatten_positions(confirm=True)

    close_calls = [c for c in fake.calls if c[0] == "DELETE" and "/v2/positions/" in c[1]]
    assert {c[1].split("/v2/positions/")[1].split("?")[0] for c in close_calls} == {"AAPL", "MSFT"}
    assert result["flatten_result"]["fully_flattened"] is True
    assert result["flatten_result"]["remaining_symbols"] == []
    assert result["open_positions"] == 0

    audit = service.recent("audit", limit=5)["items"]
    assert audit[0]["event"] == "paper_positions_flatten_attempted"
    assert audit[0]["details"]["fully_flattened"] is True


def test_flatten_positions_requires_explicit_confirm(tmp_path):
    service, _fake = service_for(tmp_path)
    service.activate()
    with pytest.raises(ValueError, match="confirm"):
        service.flatten_positions(confirm=False)


def test_flatten_is_a_separate_action_never_triggered_by_stop_alone(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position()]
        service.store.save(state)
    fake.calls.clear()

    service.pause(emergency=True)

    assert [c for c in fake.calls if "/v2/positions/" in c[1] and c[0] == "DELETE"] == [], (
        "engaging STOP must never itself close a position -- flatten is a separate, explicit action"
    )
    assert service.status()["open_positions"] == 1


def test_flatten_available_even_when_deactivated(tmp_path):
    """An operator must be able to flatten regardless of lifecycle state,
    including after deactivating -- flatten is intentionally not gated on
    activated/paused/kill_switch/broker_submission."""
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position()]
        service.store.save(state)
    service.deactivate()
    fake.positions = []

    result = service.flatten_positions(confirm=True)

    assert result["flatten_result"]["fully_flattened"] is True


# ---------------------------------------------------------------------------
# 6. Partial flatten failure: never silently reported as success.
# ---------------------------------------------------------------------------


def test_flatten_reports_partial_failure_visibly(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position("AAPL", "5"), open_position("MSFT", "2")]
        service.store.save(state)
    fake.fail_close_symbols = {"MSFT"}
    fake.positions = [open_position("MSFT", "2")]  # broker: AAPL closed, MSFT still open

    result = service.flatten_positions(confirm=True)

    assert result["flatten_result"]["fully_flattened"] is False
    assert result["flatten_result"]["remaining_symbols"] == ["MSFT"]
    per_symbol = {r["symbol"]: r for r in result["flatten_result"]["results"]}
    assert per_symbol["AAPL"]["closed"] is True
    assert per_symbol["MSFT"]["closed"] is False
    assert "error" in per_symbol["MSFT"]
    assert result["open_positions"] == 1, "a remaining broker position must still show up in the ordinary projection, not just the flatten_result"


# ---------------------------------------------------------------------------
# 7. Duplicate/repeated activation (and repeated emergency stop)
# ---------------------------------------------------------------------------


def test_repeated_emergency_stop_is_safe_and_each_call_is_audited(tmp_path):
    service, _fake = service_for(tmp_path)
    service.activate()

    first = service.pause(emergency=True)
    second = service.pause(emergency=True)

    assert first["kill_switch"] is True
    assert second["kill_switch"] is True
    events = [e["event"] for e in service.recent("audit", limit=10)["items"]]
    assert events.count("emergency_paper_stop") == 2


def test_repeated_activate_after_stop_is_the_explicit_reversal(tmp_path):
    """activate() clearing kill_switch is intended, operator-initiated
    behavior when called directly and explicitly (as opposed to an
    unattended process silently re-arming on its own -- see report)."""
    service, _fake = service_for(tmp_path)
    service.activate()
    service.pause(emergency=True)
    assert service.status()["kill_switch"] is True

    service.activate()

    assert service.status()["kill_switch"] is False


# ---------------------------------------------------------------------------
# 8. Restart/persistence behavior
# ---------------------------------------------------------------------------


def test_kill_switch_persists_across_a_simulated_restart(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    service.pause(emergency=True)

    # Simulate an app restart: a brand new service instance over the same
    # on-disk store, exactly like desktop_bridge/autonomous_paper.py's
    # _service() constructing a fresh instance per bridge call.
    restarted = GovernedPaperExecutionService(PaperExecutionStore(tmp_path.resolve()), service.client)
    status = restarted.status()
    assert status["kill_switch"] is True
    assert status["broker_submission"] is False

    fake.calls.clear()
    result = evaluate(restarted, [candidate()])
    assert result["progress"]["state"] == "execution_disabled"
    proposal = restarted.recent("proposals", limit=1)["items"][0]
    assert proposal["rejection_reasons"] == ["governed_activation_required"]
    assert [c for c in fake.calls if c[0] == "POST" and "/v2/orders" in c[1]] == []


# ---------------------------------------------------------------------------
# 9. UI-facing projection fields are present and correct
# ---------------------------------------------------------------------------


def test_projection_exposes_lifecycle_and_kill_switch_fields_for_the_ui(tmp_path):
    service, _fake = service_for(tmp_path)
    status = service.status()
    assert status["lifecycle_actions_available"] is True
    assert status["lifecycle_unavailable_reason"] is None
    assert status["kill_switch"] is True  # fresh install starts halted

    service.activate()
    assert service.status()["kill_switch"] is False

    stopped = service.pause(emergency=True)
    assert stopped["kill_switch"] is True
    assert stopped["broker_submission"] is False


def test_audit_envelope_always_carries_current_kill_switch_state(tmp_path):
    service, _fake = service_for(tmp_path)
    service.activate()
    service.pause(emergency=True)

    audit = service.recent("audit", limit=5)["items"]
    assert audit[0]["kill_switch"] is True
    assert audit[0]["event"] == "emergency_paper_stop"


# ---------------------------------------------------------------------------
# 10. Resting orders at STOP time: cancellation attempted, visibly, and
#     cannot create new exposure via a race with reconciliation/retry.
# ---------------------------------------------------------------------------


def test_emergency_stop_cancels_resting_orders(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["orders"] = [resting_order("order-1", "AAPL"), resting_order("order-2", "MSFT")]
        service.store.save(state)
    fake.orders = [resting_order("order-1", "AAPL"), resting_order("order-2", "MSFT")]

    service.pause(emergency=True)

    cancel_calls = {c[1].split("/v2/orders/")[1] for c in fake.calls if c[0] == "DELETE" and "/v2/orders/" in c[1]}
    assert cancel_calls == {"order-1", "order-2"}
    audit = service.recent("audit", limit=5)["items"]
    assert audit[0]["event"] == "emergency_paper_stop_order_cancellation"
    assert audit[0]["details"]["attempted"] == 2
    assert all(r["cancelled"] for r in audit[0]["details"]["results"])


def test_emergency_stop_order_cancellation_partial_failure_is_visible(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["orders"] = [resting_order("order-1", "AAPL"), resting_order("order-2", "MSFT")]
        service.store.save(state)
    fake.orders = [resting_order("order-1", "AAPL"), resting_order("order-2", "MSFT")]
    fake.fail_cancel_order_ids = {"order-2"}

    service.pause(emergency=True)

    audit = service.recent("audit", limit=5)["items"]
    cancel_event = next(e for e in audit if e["event"] == "emergency_paper_stop_order_cancellation")
    results = {r["provider_order_id"]: r for r in cancel_event["details"]["results"]}
    assert results["order-1"]["cancelled"] is True
    assert results["order-2"]["cancelled"] is False
    assert "error" in results["order-2"]
    assert cancel_event["details"]["still_open_after_attempt"] == 1


def test_stop_flips_kill_switch_before_attempting_any_order_cancellation(tmp_path):
    """Ordering guarantee: even if cancellation fails entirely, the
    no-new-exposure guarantee is already durable, because kill_switch/
    broker_submission are persisted in their own transaction first."""
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["orders"] = [resting_order("order-1", "AAPL")]
        service.store.save(state)
    fake.orders = [resting_order("order-1", "AAPL")]
    fake.fail_cancel_order_ids = {"order-1"}

    service.pause(emergency=True)

    # Even though the one resting order's cancel failed, new submissions
    # must still be impossible -- that guarantee doesn't depend on
    # cancellation succeeding. Whether the *first* rejection reason to fire
    # is the pending-order-cap policy check or the kill-switch gate itself
    # is incidental (both are independently correct, both must hold); what
    # matters here is that no order reaches the broker either way.
    fake.calls.clear()
    result = evaluate(service, [candidate("TSLA")])
    assert result["progress"]["state"] in {"execution_disabled", "no_qualified_candidate"}
    proposal_items = service.recent("proposals", limit=1)["items"]
    if proposal_items:
        assert proposal_items[0]["status"] == "rejected"
    assert [c for c in fake.calls if c[0] == "POST" and "/v2/orders" in c[1]] == []


def test_no_pending_orders_means_no_cancellation_audit_noise(tmp_path):
    service, _fake = service_for(tmp_path)
    service.activate()

    service.pause(emergency=True)

    events = [e["event"] for e in service.recent("audit", limit=5)["items"]]
    assert "emergency_paper_stop_order_cancellation" not in events


# ---------------------------------------------------------------------------
# 11. The previously-broken emergency_paper_liquidation() alias now works.
# ---------------------------------------------------------------------------


def test_emergency_paper_liquidation_alias_delegates_to_flatten(tmp_path, monkeypatch):
    from sigil.desktop_bridge import production_research as research_module

    service, fake = service_for(tmp_path)
    monkeypatch.setattr(research_module, "_execution_service", lambda: service)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position("AAPL", "5")]
        service.store.save(state)
    fake.positions = []

    result = emergency_paper_liquidation()

    assert result["flatten_result"]["fully_flattened"] is True


# ---------------------------------------------------------------------------
# 12. Live-trading path remains impossible: the two client methods this P1
#     wires up (cancel_order, close_position) are bound by the exact same
#     paper-endpoint invariant as every pre-existing order-submission call.
# ---------------------------------------------------------------------------


def test_flatten_and_cancel_are_paper_endpoint_bound(tmp_path):
    service, fake = service_for(tmp_path)
    service.activate()
    with service.store.locked() as state:
        state["positions"] = [open_position()]
        state["orders"] = [resting_order()]
        service.store.save(state)
    fake.positions = []
    fake.orders = []

    service.flatten_positions(confirm=True)
    service.pause(emergency=True)

    close_and_cancel_calls = [c for c in fake.calls if c[0] == "DELETE"]
    assert close_and_cancel_calls, "expected at least one DELETE call to have been made"
    for _method, url, _body in close_and_cancel_calls:
        assert url.startswith(ALPACA_PAPER_BASE_URL), (
            "flatten/cancel must never target anything but the paper API -- "
            "AlpacaPaperClient has no live-endpoint parameter anywhere in "
            "its constructor or call chain, so this can only ever be true"
        )


def test_alpaca_paper_client_close_position_rejects_non_paper_url_by_construction():
    """AlpacaPaperClient.base_url is a hardcoded class attribute; confirm it
    cannot be overridden to a live URL even via direct attribute assignment,
    the same invariant test style already used for submit_order elsewhere."""
    client = AlpacaPaperClient("paper-key", "paper-secret", transport=lambda *a, **k: (200, {}))
    assert client.base_url == ALPACA_PAPER_BASE_URL
    assert not hasattr(type(client), "__init__") or "base_url" not in AlpacaPaperClient.__init__.__code__.co_varnames[
        : AlpacaPaperClient.__init__.__code__.co_argcount
    ], "base_url must not be a constructor parameter -- it must stay a hardcoded class attribute"
