from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
from types import MappingProxyType

import pytest

from sigil.execution import (
    ExecutionJournalEvent,
    ExecutionJournalEventType,
    ExecutionRecoveryClassification,
    ExecutionRecoveryInspection,
)
from sigil.integrations.providers import FinancialDataTransportError, FinancialDataValidationError
from sigil.integrations.providers.public_execution import (
    PUBLIC_EXECUTION_PROVIDER_ID,
    PublicTransportResult,
    _PublicGovernedTransport,
    protected_account_binding,
)
from sigil.portfolio import (
    BrokerageAccountState,
    BrokerageExecution,
    BrokerageOrderState,
    BrokeragePortfolioSnapshot,
    BrokeragePosition,
    PortfolioFreshnessPolicy,
    PortfolioReconciliationService,
    PortfolioStateDiscrepancyCode,
    PortfolioStateProvenance,
    PublicPortfolioStateProvider,
    provider_response_digest,
)


NOW = datetime(2026, 7, 23, 16, 0, tzinfo=timezone.utc)
ACCOUNT_ID = "acct_step11"
CLIENT_ID = "123e4567-e89b-42d3-a456-426614174000"
PROVIDER_ID = "provider-order-001"
DIGEST = "1" * 64


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in Step 11 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def account(**changes: object) -> BrokerageAccountState:
    values: dict[str, object] = {
        "provider_id": PUBLIC_EXECUTION_PROVIDER_ID,
        "account_binding": protected_account_binding(ACCOUNT_ID),
        "broker_account_id": ACCOUNT_ID,
        "account_type": "CASH",
        "account_status": "ACTIVE",
        "currency": "USD",
        "trading_eligible": True,
        "cash_balance": "1000",
        "available_cash": "900",
        "settled_cash": "800",
        "unsettled_cash": "100",
        "buying_power": "900",
        "equity": "1500",
        "provider_timestamp": NOW,
        "acquired_at": NOW + timedelta(seconds=1),
        "response_digest": DIGEST,
    }
    values.update(changes)
    return BrokerageAccountState(**values)  # type: ignore[arg-type]


def position(symbol: str = "AAPL", **changes: object) -> BrokeragePosition:
    values: dict[str, object] = {
        "symbol": symbol,
        "instrument_type": "EQUITY",
        "quantity": "2",
        "available_quantity": "2",
        "average_cost": "100",
        "current_market_value": "220",
        "last_price": "110",
        "unrealized_gain_loss": "20",
        "currency": "USD",
        "provider_timestamp": NOW,
        "acquired_at": NOW + timedelta(seconds=1),
    }
    values.update(changes)
    return BrokeragePosition(**values)  # type: ignore[arg-type]


def order(**changes: object) -> BrokerageOrderState:
    values: dict[str, object] = {
        "client_order_id": CLIENT_ID,
        "provider_order_id": PROVIDER_ID,
        "symbol": "AAPL",
        "instrument_type": "EQUITY",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": "2",
        "notional": None,
        "limit_price": "100",
        "time_in_force": "DAY",
        "broker_status": "ACKNOWLEDGED",
        "filled_quantity": "0",
        "average_fill_price": None,
        "submitted_at": NOW,
        "updated_at": NOW,
        "terminal": False,
    }
    values.update(changes)
    return BrokerageOrderState(**values)  # type: ignore[arg-type]


def execution(**changes: object) -> BrokerageExecution:
    values: dict[str, object] = {
        "provider_execution_id": "fill-001",
        "provider_order_id": PROVIDER_ID,
        "client_order_id": CLIENT_ID,
        "symbol": "AAPL",
        "side": "BUY",
        "filled_quantity": "2",
        "fill_price": "100",
        "fees": "0",
        "executed_at": NOW,
        "settlement_metadata": (("settlement_date", "2026-07-24"),),
    }
    values.update(changes)
    return BrokerageExecution(**values)  # type: ignore[arg-type]


def freshness(**changes: object) -> PortfolioFreshnessPolicy:
    values: dict[str, object] = {
        "maximum_account_state_age": timedelta(minutes=5),
        "maximum_position_state_age": timedelta(minutes=5),
        "maximum_open_order_age": timedelta(minutes=5),
        "allowed_future_clock_skew": timedelta(seconds=5),
        "maximum_acquisition_duration": timedelta(seconds=10),
        "maximum_positions": 10,
        "maximum_orders": 10,
        "maximum_executions": 10,
    }
    values.update(changes)
    return PortfolioFreshnessPolicy(**values)  # type: ignore[arg-type]


def provenance(operation: str = "account_state") -> PortfolioStateProvenance:
    return PortfolioStateProvenance(
        provider_id=PUBLIC_EXECUTION_PROVIDER_ID,
        account_binding=protected_account_binding(ACCOUNT_ID),
        operation=operation,
        endpoint_identity=f"/userapigateway/trading/{{accountId}}/{operation}",
        provider_timestamp=NOW,
        acquired_at=NOW + timedelta(seconds=1),
        response_digest=DIGEST,
    )


def snapshot(**changes: object) -> BrokeragePortfolioSnapshot:
    values: dict[str, object] = {
        "account_binding": protected_account_binding(ACCOUNT_ID),
        "account": account(),
        "positions": (position(),),
        "orders": (order(),),
        "executions": (execution(),),
        "provenance": (provenance(),),
        "acquired_started_at": NOW,
        "acquired_completed_at": NOW + timedelta(seconds=1),
        "positions_complete": True,
        "orders_complete": True,
        "executions_complete": True,
    }
    values.update(changes)
    return BrokeragePortfolioSnapshot(**values)  # type: ignore[arg-type]


def test_valid_account_position_open_order_terminal_order_execution_and_snapshot() -> None:
    assert account().cash_balance == "1000"
    assert position().position_digest
    assert order().terminal is False
    assert order(broker_status="FILLED", terminal=True).terminal is True
    assert execution().fill_price == "100"
    assert snapshot().complete is True


def test_snapshot_ordering_hash_stability_and_material_change() -> None:
    first = snapshot(positions=(position("MSFT"), position("AAPL")))
    second = snapshot(positions=(position("AAPL"), position("MSFT")))
    assert tuple(item.symbol for item in first.positions) == ("AAPL", "MSFT")
    assert first.snapshot_id == second.snapshot_id
    assert replace(first, account=replace(first.account, equity="1501"), snapshot_id="").snapshot_id != first.snapshot_id


def test_correct_binding_and_mismatch_rejection() -> None:
    assert account().account_binding == protected_account_binding(ACCOUNT_ID)
    with pytest.raises(FinancialDataValidationError, match="binding mismatch"):
        account(account_binding=protected_account_binding("other"))


@pytest.mark.parametrize(
    ("factory", "changes"),
    [
        (position, {"instrument_type": "OPTION"}),
        (position, {"currency": "EUR"}),
        (order, {"side": "SHORT"}),
        (order, {"order_type": "STOP"}),
        (position, {"quantity": "-1"}),
        (position, {"quantity": "NaN"}),
        (position, {"last_price": "Infinity"}),
        (order, {"submitted_at": datetime(2026, 7, 23)}),
        (order, {"updated_at": NOW - timedelta(seconds=1)}),
        (order, {"broker_status": "MYSTERY"}),
        (order, {"quantity": None, "notional": None}),
        (order, {"quantity": "1", "notional": "10"}),
        (order, {"order_type": "MARKET", "limit_price": "1"}),
    ],
)
def test_invalid_normalized_values(factory: object, changes: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError):
        factory(**changes)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"account": account(provider_timestamp=NOW - timedelta(minutes=6))}, "stale_account"),
        ({"positions": (position(provider_timestamp=NOW - timedelta(minutes=6)),)}, "stale_positions"),
        ({
            "orders": (
                order(
                    submitted_at=NOW - timedelta(minutes=6),
                    updated_at=NOW - timedelta(minutes=6),
                ),
            )
        }, "stale_orders"),
        ({"account": account(provider_timestamp=NOW + timedelta(seconds=7))}, "future_timestamp"),
        ({"positions_complete": False}, "partial_or_truncated"),
        ({"positions_complete": False, "positions_truncated": True}, "partial_or_truncated"),
        ({"orders_complete": False, "orders_truncated": True}, "partial_or_truncated"),
        ({"executions_complete": False, "executions_truncated": True}, "partial_or_truncated"),
        ({"acquired_completed_at": NOW + timedelta(seconds=11)}, "acquisition_too_slow"),
    ],
)
def test_pretrade_fail_closed(changes: dict[str, object], reason: str) -> None:
    eligible, reasons = snapshot(**changes).pre_trade_eligibility(
        freshness(), now=NOW + timedelta(seconds=1), expected_account_id=ACCOUNT_ID
    )
    assert eligible is False
    assert reason in reasons


def test_pretrade_complete_fresh_snapshot_is_eligible() -> None:
    assert snapshot().pre_trade_eligibility(
        freshness(), now=NOW + timedelta(seconds=1), expected_account_id=ACCOUNT_ID
    ) == (True, ())


def test_pretrade_wrong_expected_account_fails_closed() -> None:
    eligible, reasons = snapshot().pre_trade_eligibility(
        freshness(), now=NOW + timedelta(seconds=1), expected_account_id="other"
    )
    assert not eligible and "wrong_account" in reasons


@pytest.mark.parametrize(
    "field",
    ["positions_complete", "orders_complete", "executions_complete"],
)
def test_each_missing_component_is_explicitly_partial(field: str) -> None:
    value = snapshot(**{field: False})
    assert value.complete is False


def test_snapshot_identity_changes_with_completeness_metadata() -> None:
    assert snapshot().snapshot_id != snapshot(executions_complete=False).snapshot_id


def test_client_order_id_mismatch_classification() -> None:
    report = PortfolioReconciliationService().reconcile(
        snapshot(),
        FakeJournal(client_id="other-client", provider_id=PROVIDER_ID),
    )
    assert PortfolioStateDiscrepancyCode.MISMATCHED_CLIENT_ORDER_ID in codes(report)


@pytest.mark.parametrize(
    "changes",
    [
        {"positions": (position("AAPL"), position("AAPL"))},
        {"orders": (order(), replace(order(), symbol="MSFT"))},
        {"orders": (order(), replace(order(), provider_order_id="other-provider"))},
        {"executions": (execution(), replace(execution(), provider_order_id="other-provider"))},
    ],
)
def test_duplicate_state_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(FinancialDataValidationError, match="duplicate"):
        snapshot(**changes)


@pytest.mark.parametrize(
    ("changes", "policy_changes", "reason"),
    [
        ({"positions": (position(), position("MSFT"))}, {"maximum_positions": 1}, "positions_limit_exceeded"),
        ({"orders": (order(), replace(order(), client_order_id="client-2", provider_order_id="provider-2"))}, {"maximum_orders": 1}, "orders_limit_exceeded"),
        ({"executions": (execution(), replace(execution(), provider_execution_id="fill-2"))}, {"maximum_executions": 1}, "executions_limit_exceeded"),
    ],
)
def test_maximum_counts_fail_pretrade(
    changes: dict[str, object], policy_changes: dict[str, object], reason: str
) -> None:
    eligible, reasons = snapshot(**changes).pre_trade_eligibility(
        freshness(**policy_changes), now=NOW + timedelta(seconds=1), expected_account_id=ACCOUNT_ID
    )
    assert not eligible and reason in reasons


def test_provider_response_digest_is_deterministic() -> None:
    assert provider_response_digest({"b": 2, "a": 1}) == provider_response_digest({"a": 1, "b": 2})


@pytest.mark.parametrize("key", ["authorization", "Authorization", "access_token", "api_key", "secret"])
def test_secret_bearing_provider_values_rejected(key: str) -> None:
    with pytest.raises(FinancialDataValidationError, match="secret-bearing"):
        provider_response_digest({key: "fictional"})


class FakeResponse:
    def __init__(self, payload: object, *, content_type: str = "application/json") -> None:
        self.raw = json.dumps(payload).encode()
        self.headers = {"Content-Type": content_type}

    def getcode(self) -> int:
        return 200

    def read(self, size: int) -> bytes:
        return self.raw[:size]


class RecordingOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def __call__(self, request: object, timeout: float) -> object:
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_exact_get_only_account_paths_host_and_timeout() -> None:
    opener = RecordingOpener([FakeResponse({}) for _ in range(3)])
    transport = _PublicGovernedTransport(opener=opener, timeout_seconds=3)
    transport.list_accounts("runtime-token")
    transport.account_portfolio(ACCOUNT_ID, "runtime-token")
    transport.get_history(ACCOUNT_ID, "runtime-token")
    assert [request.method for request in opener.requests] == ["GET"] * 3
    assert all(request.host == "api.public.com" for request in opener.requests)
    assert [request.selector for request in opener.requests] == [
        "/userapigateway/trading/account",
        f"/userapigateway/trading/{ACCOUNT_ID}/portfolio/v2",
        f"/userapigateway/trading/{ACCOUNT_ID}/history",
    ]
    assert opener.timeouts == [3.0] * 3


def test_transport_timeout_oversize_and_malformed_are_bounded() -> None:
    with pytest.raises(FinancialDataTransportError, match="failed"):
        _PublicGovernedTransport(opener=RecordingOpener([TimeoutError()])).get_history(
            ACCOUNT_ID, "token"
        )
    with pytest.raises(FinancialDataTransportError, match="maximum"):
        _PublicGovernedTransport(
            opener=RecordingOpener([FakeResponse({"long": "x" * 100})]), max_response_bytes=10
        ).get_history(ACCOUNT_ID, "token")
    malformed = FakeResponse({})
    malformed.raw = b"{"
    with pytest.raises(FinancialDataValidationError, match="malformed"):
        _PublicGovernedTransport(opener=RecordingOpener([malformed])).get_history(
            ACCOUNT_ID, "token"
        )


class StaticTokens:
    def __init__(self) -> None:
        self.calls = 0

    def get(self) -> str:
        self.calls += 1
        return "runtime-only-token"


class StaticTransport:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, str]] = []

    def _result(self, name: str, account_id: str, token: str) -> PublicTransportResult:
        self.calls.append((name, account_id, token))
        return PublicTransportResult(self.payloads[name], 200, DIGEST, 10, f"/userapigateway/trading/{{accountId}}/{name}")

    def list_accounts(self, token: str) -> PublicTransportResult:
        return self._result("accounts", "unscoped", token)

    def account_portfolio(self, account_id: str, token: str) -> PublicTransportResult:
        return self._result("portfolio", account_id, token)

    def get_history(self, account_id: str, token: str) -> PublicTransportResult:
        return self._result("history", account_id, token)


def acquisition_payloads() -> dict[str, object]:
    stamp = NOW.isoformat()
    return {
        "accounts": {
            "accounts": [{
                "accountId": ACCOUNT_ID, "accountType": "BROKERAGE",
                "brokerageAccountType": "CASH", "tradePermissions": "BUY_AND_SELL",
            }],
        },
        "portfolio": {
            "accountId": ACCOUNT_ID, "accountType": "BROKERAGE", "cash": "1000",
            "totalAccountValue": "1500",
            "buyingPower": {"cashOnlyBuyingPower": "900"},
            "availableToWithdraw": {"cashOnlyAvailableToWithdraw": "900"},
            "positions": [{
                "instrument": {"symbol": "AAPL", "type": "EQUITY"}, "quantity": "2",
                "currentValue": "220", "lastPrice": {"lastPrice": "110", "timestamp": stamp},
                "instrumentGain": {"gainValue": "20", "timestamp": stamp},
                "costBasis": {"unitCost": "100", "lastUpdate": stamp},
            }],
            "orders": [{
                "orderId": CLIENT_ID, "instrument": {"symbol": "AAPL", "type": "EQUITY"},
                "createdAt": stamp, "type": "LIMIT", "side": "BUY", "status": "NEW",
                "quantity": "2", "limitPrice": "100",
                "expiration": {"timeInForce": "DAY"}, "filledQuantity": "0",
            }],
        },
        "history": {
            "transactions": [{
                "timestamp": stamp, "id": "fill-001", "type": "TRADE", "symbol": "AAPL",
                "securityType": "EQUITY", "side": "BUY", "quantity": "2",
                "principalAmount": "100", "fees": "0",
            }],
        },
    }


def test_offline_acquisition_normalizes_complete_state_and_runtime_credentials() -> None:
    tokens = StaticTokens()
    transport = StaticTransport(acquisition_payloads())
    times = iter((NOW, NOW + timedelta(seconds=1)))
    provider = PublicPortfolioStateProvider(
        token_manager=tokens,  # type: ignore[arg-type]
        transport=transport,  # type: ignore[arg-type]
        wall_clock=lambda: next(times),
    )
    result = provider.acquire(ACCOUNT_ID, freshness())
    assert result.complete and result.account.cash_balance == "1000"
    assert tokens.calls == 3
    assert all(call[2] == "runtime-only-token" for call in transport.calls)
    assert "runtime-only-token" not in repr(result)


def test_offline_acquisition_rejects_account_mismatch_and_limits() -> None:
    payloads = acquisition_payloads()
    payloads["portfolio"]["accountId"] = "wrong"  # type: ignore[index]
    provider = PublicPortfolioStateProvider(
        token_manager=StaticTokens(),  # type: ignore[arg-type]
        transport=StaticTransport(payloads),  # type: ignore[arg-type]
        wall_clock=lambda: NOW,
    )
    with pytest.raises(FinancialDataValidationError, match="binding mismatch"):
        provider.acquire(ACCOUNT_ID, freshness())


def journal_event(terms: dict[str, object]) -> ExecutionJournalEvent:
    return ExecutionJournalEvent(
        journal_version=1,
        execution_id="execution-1",
        sequence=1,
        event_type=ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
        payload=MappingProxyType({"order_terms": terms}),
        previous_entry_hash="0" * 64,
        entry_hash="2" * 64,
        created_at=NOW,
    )


class FakeJournal:
    def __init__(
        self,
        classification: ExecutionRecoveryClassification = ExecutionRecoveryClassification.RECONCILIATION_REQUIRED,
        *,
        terms: dict[str, object] | None = None,
        client_id: str | None = CLIENT_ID,
        provider_id: str | None = PROVIDER_ID,
    ) -> None:
        self.classification = classification
        self.client_id = client_id
        self.provider_id = provider_id
        self.terms = terms or {
            "account_binding": protected_account_binding(ACCOUNT_ID),
            "symbol": "AAPL", "side": "BUY", "quantity": "2", "notional_amount": None,
            "order_type": "LIMIT", "limit_price": "100",
        }
        self.write_calls = 0

    def audit(self) -> tuple[ExecutionRecoveryInspection, ...]:
        return (ExecutionRecoveryInspection(
            "execution-1", self.classification, 1,
            ExecutionJournalEventType.SUBMISSION_INTENT_RECORDED,
            self.client_id, self.provider_id,
        ),)

    def read_events(self, execution_id: str) -> tuple[ExecutionJournalEvent, ...]:
        assert execution_id == "execution-1"
        return (journal_event(self.terms),)

    def __getattr__(self, name: str) -> object:
        if name.startswith(("record", "save", "append", "consume")):
            self.write_calls += 1
            raise AssertionError("reconciliation attempted mutation")
        raise AttributeError(name)


def codes(report: object) -> set[PortfolioStateDiscrepancyCode]:
    return {item.code for item in report.discrepancies}  # type: ignore[attr-defined]


def test_journal_intent_match_and_ambiguous_resolution_are_deterministic_read_only() -> None:
    journal = FakeJournal()
    service = PortfolioReconciliationService()
    first = service.reconcile(snapshot(), journal)
    second = service.reconcile(snapshot(), journal)
    assert {PortfolioStateDiscrepancyCode.MATCHED, PortfolioStateDiscrepancyCode.AMBIGUOUS_RESOLVED} <= codes(first)
    assert first.report_id == second.report_id
    assert journal.write_calls == 0


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("account_binding", protected_account_binding("other"), PortfolioStateDiscrepancyCode.MISMATCHED_ACCOUNT),
        ("symbol", "MSFT", PortfolioStateDiscrepancyCode.MISMATCHED_SYMBOL),
        ("side", "SELL", PortfolioStateDiscrepancyCode.MISMATCHED_SIDE),
        ("quantity", "3", PortfolioStateDiscrepancyCode.MISMATCHED_QUANTITY),
        ("notional_amount", "200", PortfolioStateDiscrepancyCode.MISMATCHED_NOTIONAL),
        ("order_type", "MARKET", PortfolioStateDiscrepancyCode.MISMATCHED_ORDER_TYPE),
        ("limit_price", "101", PortfolioStateDiscrepancyCode.MISMATCHED_LIMIT_PRICE),
    ],
)
def test_reconciliation_term_mismatches(
    field: str, value: str, code: PortfolioStateDiscrepancyCode
) -> None:
    journal = FakeJournal()
    journal.terms[field] = value
    assert code in codes(PortfolioReconciliationService().reconcile(snapshot(), journal))


def test_absent_unjournaled_identifier_terminal_and_cancellation_classifications() -> None:
    service = PortfolioReconciliationService()
    absent = service.reconcile(snapshot(orders=()), FakeJournal())
    assert {PortfolioStateDiscrepancyCode.JOURNAL_ORDER_ABSENT, PortfolioStateDiscrepancyCode.RECONCILIATION_REQUIRED} <= codes(absent)
    unjournaled = service.reconcile(snapshot(), FakeJournal(client_id="missing", provider_id="missing"))
    assert PortfolioStateDiscrepancyCode.UNJOURNALED_BROKER_ORDER in codes(unjournaled)
    provider_mismatch = service.reconcile(snapshot(), FakeJournal(provider_id="other"))
    assert PortfolioStateDiscrepancyCode.MISMATCHED_PROVIDER_ORDER_ID in codes(provider_mismatch)
    terminal = snapshot(orders=(order(broker_status="FILLED", terminal=True),))
    assert PortfolioStateDiscrepancyCode.BROKER_TERMINAL_NOT_RECORDED in codes(
        service.reconcile(terminal, FakeJournal())
    )
    cancelled = snapshot(orders=(order(broker_status="CANCELLED", terminal=True),))
    assert PortfolioStateDiscrepancyCode.CANCELLATION_RESOLVED in codes(
        service.reconcile(
            cancelled,
            FakeJournal(ExecutionRecoveryClassification.CANCELLATION_RECONCILIATION_REQUIRED),
        )
    )
    conflict = service.reconcile(
        snapshot(), FakeJournal(ExecutionRecoveryClassification.COMPLETE)
    )
    assert PortfolioStateDiscrepancyCode.TERMINAL_STATE_CONFLICT in codes(conflict)


def test_corrupt_journal_classification() -> None:
    report = PortfolioReconciliationService().reconcile(
        snapshot(), FakeJournal(ExecutionRecoveryClassification.CORRUPT)
    )
    assert PortfolioStateDiscrepancyCode.CORRUPT_JOURNAL in codes(report)


def test_no_generated_state_artifacts_in_repository(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    PortfolioReconciliationService().reconcile(snapshot(), FakeJournal())
    assert tuple(tmp_path.iterdir()) == before
