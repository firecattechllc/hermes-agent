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
    ExecutionEnvironmentIdentity,
    GovernedPaperExecutionService,
    PaperExecutionPolicy,
    PaperExecutionStore,
    client_order_id,
)
from sigil.autonomous_paper.alpaca import AlpacaPaperTransportError


class FakeAlpaca:
    def __init__(self, store: PaperExecutionStore | None = None) -> None:
        self.calls: list[tuple[str, str, object | None]] = []
        self.store = store
        self.clock_open = True
        self.positions: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self.lookup: dict[str, Any] | None = None
        self.ambiguous_submit = False
        self.intent_was_durable = False

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: object | None,
        timeout: float,
    ) -> tuple[int, object]:
        del timeout
        assert url.startswith(f"{ALPACA_PAPER_BASE_URL}/v2/")
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
                self.intent_was_durable = bool(
                    state["order_intents"]
                    and state["order_intents"][0]["status"] == "submission_pending"
                )
            if self.ambiguous_submit:
                raise AlpacaPaperTransportError("request_timeout", ambiguous=True)
            assert isinstance(body, dict)
            return 201, {
                "id": "paper-order-1",
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "status": "accepted",
                "notional": body["notional"],
                "filled_qty": "0",
            }
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
        average_dollar_volume=Decimal("50000000"),
        strategy_score=Decimal("0.80"),
        confidence=Decimal("0.85"),
        expected_setup_positive=True,
        evidence_complete=True,
    )
    return replace(value, **changes)


def activate(service: GovernedPaperExecutionService) -> dict[str, Any]:
    return service.activate()


def evaluate(
    service: GovernedPaperExecutionService,
    research: list[CandidateResearch],
    **changes: Any,
) -> dict[str, Any]:
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


def save_state(service: GovernedPaperExecutionService, **changes: Any) -> None:
    with service.store.locked() as state:
        state.update(changes)
        service.store.save(state)


def test_default_install_is_paper_only_disabled_and_kill_switch_persists(tmp_path):
    service, fake = service_for(tmp_path)
    first = service.status()
    restarted = GovernedPaperExecutionService(service.store, service.client).status()
    assert first == restarted
    assert first["environment"] == "paper"
    assert first["broker_base_url"] == ALPACA_PAPER_BASE_URL
    assert first["broker_submission"] is False
    assert first["live_execution"] is False
    assert first["activated"] is False
    assert first["kill_switch"] is True
    assert fake.calls == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"application_environment": ""}, "identities must be paper"),
        ({"broker_environment": "live"}, "identities must be paper"),
        ({"credential_environment": "ambiguous"}, "identities must be paper"),
        (
            {"broker_base_url": "https://api.alpaca.markets"},
            "must be the paper endpoint",
        ),
        ({"live_execution": True}, "permanently disabled"),
        (
            {"broker_submission": True, "paper_account_authenticated": False},
            "requires authenticated",
        ),
        ({"order_mutations": True}, "must exactly match"),
    ],
)
def test_invalid_execution_environment_combinations_fail_closed(change, message):
    values = {
        "application_environment": "paper",
        "broker_environment": "paper",
        "broker_base_url": ALPACA_PAPER_BASE_URL,
        "credential_environment": "paper",
        "broker_submission": False,
        "live_execution": False,
        "order_mutations": False,
        "certification_mode": False,
        "paper_account_authenticated": False,
    }
    values.update(change)
    with pytest.raises(ValueError, match=message):
        ExecutionEnvironmentIdentity(**values)


def test_activation_authenticates_and_reconciles_before_enabling(tmp_path):
    service, fake = service_for(tmp_path)
    fake.positions = [{"symbol": "MSFT", "qty": "0.1", "market_value": "10"}]
    status = activate(service)
    assert status["activated"] is True
    assert status["broker_submission"] is True
    assert status["live_execution"] is False
    assert status["open_positions"] == 1
    assert [call[0:2] for call in fake.calls] == [
        ("GET", f"{ALPACA_PAPER_BASE_URL}/v2/account"),
        ("GET", f"{ALPACA_PAPER_BASE_URL}/v2/positions"),
        (
            "GET",
            f"{ALPACA_PAPER_BASE_URL}/v2/orders?status=open&direction=asc",
        ),
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"maximum_order_notional": Decimal("1000.01")},
        {"maximum_open_positions": 11},
        {"maximum_deployed_capital": Decimal("10000.01")},
        {"maximum_new_positions_per_cycle": 2},
        {"maximum_pending_entry_orders": 2},
    ],
)
def test_governed_policy_cannot_weaken_hard_caps(change):
    with pytest.raises(ValueError):
        PaperExecutionPolicy(**change)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"asset_class": "option"}, "unsupported_asset"),
        ({"asset_class": "crypto"}, "unsupported_asset"),
        ({"exchange": "OTC"}, "otc_forbidden"),
        ({"name": "Daily 3X Bull ETF"}, "leveraged_or_inverse_forbidden"),
        ({"leveraged_or_inverse": True}, "leveraged_or_inverse_forbidden"),
        ({"fractionable": False}, "not_fractionable"),
        ({"tradable": False}, "not_tradable"),
        ({"quote_age_seconds": 31}, "stale_quote"),
        ({"bars_age_seconds": 901}, "stale_bars"),
        (
            {"quote_bid": Decimal("99"), "quote_ask": Decimal("101")},
            "excess_spread",
        ),
        (
            {"average_dollar_volume": Decimal("999999")},
            "insufficient_liquidity",
        ),
        ({"confidence": Decimal("0.74")}, "insufficient_confidence"),
        ({"strategy_score": Decimal("0")}, "non_positive_setup"),
        ({"expected_setup_positive": False}, "non_positive_setup"),
        ({"evidence_complete": False}, "incomplete_evidence"),
        ({"conflicting_evidence": True}, "conflicting_evidence"),
    ],
)
def test_candidate_quality_and_asset_restrictions_are_exact(tmp_path, changes, reason):
    service, fake = service_for(tmp_path)
    status = evaluate(service, [candidate(**changes)], submit=False)
    assert reason in status["last_rejection"]["reasons"]
    assert status["progress"]["state"] == "no_qualified_candidate"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("evaluate_change", "reason"),
    [
        ({"catalog_fresh": False}, "stale_catalog"),
        ({"portfolio_fresh": False}, "stale_portfolio_reconciliation"),
        ({"runtime_healthy": False}, "runtime_degraded"),
        ({"audit_available": False}, "audit_unavailable"),
    ],
)
def test_runtime_freshness_and_health_gates_fail_closed(
    tmp_path, evaluate_change, reason
):
    service, _ = service_for(tmp_path)
    status = evaluate(service, [candidate()], submit=False, **evaluate_change)
    assert reason in status["last_rejection"]["reasons"]


def test_deterministic_ranking_and_client_order_ids(tmp_path):
    service, _ = service_for(tmp_path)
    status = evaluate(
        service,
        [
            candidate("ZZZ", strategy_score=Decimal("0.90")),
            candidate("AAA", strategy_score=Decimal("0.90")),
            candidate("BBB", strategy_score=Decimal("0.80")),
        ],
        submit=False,
    )
    assert status["progress"]["state"] == "order_admitted"
    assert service.recent("proposals")["items"][0]["symbol"] == "AAA"
    proposal_id = service.recent("proposals")["items"][0]["proposal_id"]
    assert client_order_id(proposal_id) == client_order_id(proposal_id)
    assert client_order_id(proposal_id).startswith("sigil-paper-")
    assert len(client_order_id(proposal_id)) <= 48


def test_order_intent_is_flushed_before_exactly_once_paper_submission(tmp_path):
    service, fake = service_for(tmp_path)
    activate(service)
    status = evaluate(service, [candidate()])
    post = [call for call in fake.calls if call[0] == "POST"]
    assert len(post) == 1
    assert fake.intent_was_durable is True
    body = post[0][2]
    assert body["notional"] == "900.00"
    assert body["side"] == "buy"
    assert body["time_in_force"] == "day"
    assert body["extended_hours"] is False
    assert status["last_order_intent"]["status"] == "accepted"
    assert status["progress"]["state"] == "order_accepted"


def test_timeout_reconciles_by_client_id_without_resubmission(tmp_path):
    service, fake = service_for(tmp_path)
    activate(service)
    fake.ambiguous_submit = True
    fake.lookup = {
        "id": "paper-existing",
        "client_order_id": "replaced-by-service",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "status": "accepted",
    }
    status = evaluate(service, [candidate()])
    assert len([call for call in fake.calls if call[0] == "POST"]) == 1
    assert len([call for call in fake.calls if "by_client_order_id" in call[1]]) == 1
    assert status["last_order_intent"]["status"] == "accepted"


def test_restart_restores_and_reconciles_unresolved_intent(tmp_path):
    service, fake = service_for(tmp_path)
    save_state(
        service,
        order_intents=[
            {
                "intent_id": "intent-1",
                "client_order_id": "sigil-paper-existing",
                "status": "submission_pending",
            }
        ],
    )
    fake.lookup = {
        "id": "paper-existing",
        "client_order_id": "sigil-paper-existing",
        "symbol": "AAPL",
        "status": "partially_filled",
        "filled_qty": "0.1",
    }
    restarted = GovernedPaperExecutionService(service.store, service.client)
    status = restarted.reconcile()
    assert status["last_order_intent"]["status"] == "partially_filled"
    fill = restarted.recent("fills")["items"][0]
    assert fill["filled_qty"] == "0.1"
    assert fill["status"] == "partially_filled"
    assert len([call for call in fake.calls if call[0] == "POST"]) == 0


def test_market_closed_pause_and_kill_switch_prevent_submission(tmp_path):
    service, fake = service_for(tmp_path)
    activate(service)
    fake.clock_open = False
    assert evaluate(service, [candidate()])["progress"]["state"] == "awaiting_market_hours"
    service.pause()
    evaluate(service, [candidate("MSFT")])
    service.pause(emergency=True)
    restarted = GovernedPaperExecutionService(service.store, service.client).status()
    assert restarted["kill_switch"] is True
    assert restarted["broker_submission"] is False
    assert len([call for call in fake.calls if call[0] == "POST"]) == 0


def test_position_pending_duplicate_and_cap_gates(tmp_path):
    service, _ = service_for(tmp_path)
    save_state(
        service,
        positions=[
            {"symbol": "AAPL", "market_value": "25"},
            *[{"symbol": f"P{index}", "market_value": "25"} for index in range(9)],
        ],
        orders=[{"symbol": "NVDA", "status": "accepted"}],
    )
    status = evaluate(service, [candidate("AAPL"), candidate("NVDA"), candidate("TSLA")])
    reasons = {
        reason
        for item in service.recent("rejections")["items"]
        for reason in item["reasons"]
    }
    assert {"duplicate_symbol", "position_limit_reached", "pending_order_limit_reached"} <= reasons
    assert status["progress"]["state"] == "no_qualified_candidate"


@pytest.mark.parametrize("cash", ["100.00", "99.99"])
def test_cash_buffer_is_never_consumed(tmp_path, cash):
    service, fake = service_for(tmp_path)
    activate(service)
    save_state(service, paper_cash=cash)
    status = evaluate(service, [candidate()])
    assert status["progress"]["state"] == "proposal_rejected"
    assert status["last_rejection"]["reasons"] == [
        "governed allocation or cash buffer exhausted"
    ]
    assert len([call for call in fake.calls if call[0] == "POST"]) == 0


def test_deployed_cap_and_max_three_positions_are_enforced(tmp_path):
    service, fake = service_for(tmp_path)
    activate(service)
    save_state(
        service,
        positions=[
            *[{"symbol": f"P{index}", "market_value": "25"} for index in range(10)],
        ],
    )
    status = evaluate(service, [candidate()])
    assert status["progress"]["state"] == "no_qualified_candidate"
    assert "position_limit_reached" in status["last_rejection"]["reasons"]
    assert len([call for call in fake.calls if call[0] == "POST"]) == 0


def test_one_winner_can_trade_before_full_catalog_traversal(tmp_path):
    service, fake = service_for(tmp_path)
    activate(service)
    research = [candidate(f"S{index:02d}") for index in range(25)]
    status = evaluate(service, research, cursor=25, total_eligible=12984)
    assert len([call for call in fake.calls if call[0] == "POST"]) == 1
    assert status["progress"]["coverage_percent"] < 1
    assert len(service.recent("proposals")["items"]) == 1


def test_no_candidate_batch_advances_with_explicit_progress(tmp_path):
    service, fake = service_for(tmp_path)
    status = service.record_batch_progress(
        [f"S{index:02d}" for index in range(25)],
        cursor=50,
        batch_number=2,
        total_eligible=12984,
        next_cycle_at="2026-07-29T18:00:00Z",
    )
    assert status["progress"]["current_cursor"] == 50
    assert status["progress"]["current_batch"] == 2
    assert status["progress"]["state"] == "market_data_unavailable"
    assert status["progress"]["leading_rejection_reasons"] == {
        "validated_market_research_unavailable": 25
    }
    assert fake.calls == []


def test_projection_normalizes_legacy_market_data_status(tmp_path):
    service, _ = service_for(tmp_path)
    save_state(
        service,
        progress={
            "state": "awaiting_fresh_data",
            "leading_rejection_reasons": {"validated_market_research_unavailable": 25},
        },
    )
    assert service.status()["progress"]["state"] == "market_data_unavailable"


def test_batches_and_recent_views_are_bounded(tmp_path):
    service, _ = service_for(tmp_path)
    with pytest.raises(ValueError, match="cannot exceed 25"):
        evaluate(service, [candidate(f"S{index}") for index in range(26)])
    evaluate(service, [candidate(f"S{index}") for index in range(25)], submit=False)
    page = service.recent("candidates", offset=5, limit=500)
    assert page["limit"] == 100
    assert len(page["items"]) == 20
    assert page["environment"] == "paper"
    assert page["live_execution"] is False


def test_no_secret_or_live_trading_url_is_persisted(tmp_path):
    service, _ = service_for(tmp_path)
    activate(service)
    serialized = service.store.path.read_text(encoding="utf-8")
    assert "paper-key" not in serialized
    assert "paper-secret" not in serialized
    assert "https://api.alpaca.markets" not in serialized
    assert "paper-api.alpaca.markets" not in serialized


def test_production_execution_sources_cannot_select_live_trading_url():
    source_root = Path(__file__).parents[1] / "src" / "sigil" / "autonomous_paper"
    contents = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*.py")
    )
    forbidden = "https://" + "api.alpaca.markets"
    assert forbidden not in contents
    assert contents.count(ALPACA_PAPER_BASE_URL) >= 1


def test_ordinary_certification_path_performs_no_order_mutation(tmp_path):
    service, fake = service_for(tmp_path)
    service.status()
    evaluate(service, [candidate()], submit=False)
    service.recent("audit")
    assert all(method == "GET" for method, _, _ in fake.calls)
    assert not fake.calls


def test_store_checksum_corruption_fails_closed(tmp_path):
    service, _ = service_for(tmp_path)
    service.deactivate()
    envelope = json.loads(service.store.path.read_text(encoding="utf-8"))
    envelope["payload"]["broker_submission"] = True
    service.store.path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(RuntimeError, match="integrity validation failed"):
        service.status()
