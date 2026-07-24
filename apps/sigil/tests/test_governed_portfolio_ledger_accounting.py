from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import multiprocessing
from pathlib import Path
import socket

import pytest

from sigil.accounting import (
    AccountingAdjustmentApproval,
    AccountingAdjustmentGovernance,
    CompletenessStatus,
    LedgerDiscrepancyCode,
    NormalizedBrokerActivityIngestor,
    PortfolioAccountingEngine,
    PortfolioAccountingPolicy,
    PortfolioAccountingUnavailable,
    PortfolioLedgerConflictError,
    PortfolioLedgerCorruptionError,
    PortfolioLedgerEvent,
    PortfolioLedgerEventType as Type,
    PortfolioLedgerReconciliationService,
    PortfolioLedgerRepository,
    PortfolioPerformanceService,
    PortfolioValuationService,
    canonical_bytes,
)
from sigil.integrations.providers import FinancialDataValidationError
from sigil.integrations.providers.public_execution import (
    PUBLIC_EXECUTION_PROVIDER_ID,
    protected_account_binding,
)
from sigil.portfolio import (
    BrokerageAccountState,
    BrokerageExecution,
    BrokeragePortfolioSnapshot,
    BrokeragePosition,
    PortfolioStateProvenance,
)


NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
ACCOUNT = protected_account_binding("step12-account")
LEDGER = "sigil-step12-primary"
DIGEST = "1" * 64
POLICY = PortfolioAccountingPolicy()


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in Step 12 tests")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def event(
    event_type: Type,
    payload: dict[str, object],
    *,
    source_id: str | None = None,
    effective_at: datetime = NOW,
    complete: bool = True,
    truncated: bool = False,
    **changes: object,
) -> PortfolioLedgerEvent:
    values: dict[str, object] = {
        "account_binding": ACCOUNT,
        "ledger_identity": LEDGER,
        "event_type": event_type,
        "source_identity": f"source-{source_id or event_type.value}",
        "source_provider": "fixture-provider",
        "source_record_id": source_id or f"record-{event_type.value}",
        "source_response_digest": DIGEST,
        "source_timestamp": effective_at,
        "effective_at": effective_at,
        "acquired_at": effective_at + timedelta(seconds=1),
        "currency": "USD",
        "payload": payload,
        "accounting_policy_version": POLICY.version,
        "source_complete": complete,
        "source_truncated": truncated,
    }
    values.update(changes)
    return PortfolioLedgerEvent(**values)  # type: ignore[arg-type]


def buy(
    *,
    source_id: str = "buy-1",
    quantity: str = "2",
    price: str = "100",
    fees: str = "2",
    taxes: str = "0",
    at: datetime = NOW,
) -> PortfolioLedgerEvent:
    gross = Decimal(quantity) * Decimal(price)
    net = gross + Decimal(fees) + Decimal(taxes)
    return event(
        Type.BUY_FILL,
        {
            "symbol": "AAPL",
            "instrument_type": "EQUITY",
            "provider_order_id": f"order-{source_id}",
            "client_order_id": f"client-{source_id}",
            "provider_execution_id": f"execution-{source_id}",
            "quantity": quantity,
            "fill_price": price,
            "gross_consideration": str(gross),
            "fees": fees,
            "taxes": taxes,
            "net_cash_impact": str(net),
        },
        source_id=source_id,
        effective_at=at,
    )


def sell(
    *,
    source_id: str = "sell-1",
    quantity: str = "1",
    price: str = "120",
    fees: str = "1",
    taxes: str = "0",
    at: datetime = NOW + timedelta(days=10),
) -> PortfolioLedgerEvent:
    gross = Decimal(quantity) * Decimal(price)
    net = gross - Decimal(fees) - Decimal(taxes)
    return event(
        Type.SELL_FILL,
        {
            "symbol": "AAPL",
            "instrument_type": "EQUITY",
            "provider_order_id": f"order-{source_id}",
            "client_order_id": f"client-{source_id}",
            "provider_execution_id": f"execution-{source_id}",
            "quantity": quantity,
            "fill_price": price,
            "gross_proceeds": str(gross),
            "fees": fees,
            "taxes": taxes,
            "net_proceeds": str(net),
        },
        source_id=source_id,
        effective_at=at,
    )


def repository(tmp_path: Path, **changes: object) -> PortfolioLedgerRepository:
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    return PortfolioLedgerRepository(root, **changes)  # type: ignore[arg-type]


def append_many(
    repo: PortfolioLedgerRepository, events: tuple[PortfolioLedgerEvent, ...]
) -> tuple[object, ...]:
    return tuple(
        repo.append(item, created_at=NOW + timedelta(days=index, seconds=2))
        for index, item in enumerate(events)
    )


def _concurrent_append(root: str, amount: str, gate: object, results: object) -> None:
    gate.wait()  # type: ignore[attr-defined]
    repo = PortfolioLedgerRepository(Path(root))
    try:
        repo.append(
            event(Type.CASH_DEPOSIT, {"amount": amount}, source_id="concurrent"),
            created_at=NOW + timedelta(seconds=2),
        )
        results.put("committed")  # type: ignore[attr-defined]
    except PortfolioLedgerConflictError:
        results.put("conflict")  # type: ignore[attr-defined]


def test_empty_ledger_initialization_and_audit(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    account_dir = repo.initialize_account(ACCOUNT, LEDGER)
    assert account_dir.is_dir()
    assert repo.audit(ACCOUNT, LEDGER) == ()


def test_first_event_identity_canonical_serialization_and_hash_chain(tmp_path: Path) -> None:
    first = event(Type.CASH_DEPOSIT, {"amount": "100"})
    duplicate_model = event(Type.CASH_DEPOSIT, {"amount": "100"})
    assert first.event_identity == duplicate_model.event_identity
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    repo = repository(tmp_path)
    entry = repo.append(first, created_at=NOW + timedelta(seconds=2))
    assert entry.sequence == 1
    assert entry.previous_entry_hash == "0" * 64
    assert len(entry.entry_hash) == 64
    assert repo.audit(ACCOUNT, LEDGER) == (entry,)


def test_exact_idempotent_append_and_conflicting_duplicate(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    original = event(Type.CASH_DEPOSIT, {"amount": "100"})
    first = repo.append(original, created_at=NOW + timedelta(seconds=2))
    assert repo.append(original, created_at=NOW + timedelta(seconds=3)) == first
    with pytest.raises(PortfolioLedgerConflictError, match="conflicting duplicate"):
        repo.append(
            event(Type.CASH_DEPOSIT, {"amount": "101"}),
            created_at=NOW + timedelta(seconds=3),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: {**value, "entry_hash": "0" * 64}, "modified"),
        (lambda value: {**value, "sequence": 2}, "sequence"),
        (lambda value: {**value, "previous_entry_hash": "f" * 64}, "hash link"),
        (lambda value: {**value, "ledger_version": 999}, "version"),
        (lambda value: {**value, "account_binding": "other"}, "cross-account"),
        (lambda value: {**value, "payload": {"amount": "999"}}, "modified"),
    ],
)
def test_persisted_corruption_detection(
    tmp_path: Path, mutation: object, message: str
) -> None:
    repo = repository(tmp_path)
    entry = repo.append(
        event(Type.CASH_DEPOSIT, {"amount": "100"}), created_at=NOW + timedelta(seconds=2)
    )
    path = next(item for item in repo.root.rglob("*.json"))
    value = json.loads(path.read_bytes())
    path.write_bytes(canonical_bytes(mutation(value)))  # type: ignore[operator]
    with pytest.raises(PortfolioLedgerCorruptionError, match=message):
        repo.audit(entry.account_binding, entry.ledger_identity)


def test_filename_tampering_sequence_gap_reorder_and_truncation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    append_many(
        repo,
        (
            event(Type.CASH_DEPOSIT, {"amount": "100"}, source_id="one"),
            event(Type.INTEREST, {"amount": "1"}, source_id="two", effective_at=NOW + timedelta(days=1)),
        ),
    )
    paths = sorted(repo.root.rglob("*.json"))
    paths[1].rename(paths[1].with_name(paths[1].name.replace("0000000002", "0000000003")))
    with pytest.raises(PortfolioLedgerCorruptionError, match="sequence gap"):
        repo.audit(ACCOUNT, LEDGER)
    paths[1].with_name(paths[1].name.replace("0000000002", "0000000003")).rename(paths[1])
    paths[1].write_bytes(paths[1].read_bytes()[:10])
    with pytest.raises(PortfolioLedgerCorruptionError, match="truncated"):
        repo.audit(ACCOUNT, LEDGER)


def test_unexpected_file_symlink_and_absolute_path_boundaries(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    root.mkdir()
    (root / "unexpected").write_text("no")
    with pytest.raises(PortfolioLedgerCorruptionError, match="unexpected"):
        PortfolioLedgerRepository(root).audit(ACCOUNT, LEDGER)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(PortfolioLedgerCorruptionError, match="non-symlink"):
        PortfolioLedgerRepository(link)
    with pytest.raises(PortfolioLedgerCorruptionError, match="absolute"):
        PortfolioLedgerRepository(Path("relative"))
    with pytest.raises(FinancialDataValidationError):
        PortfolioLedgerRepository(root).initialize_account("../escape", LEDGER)


def test_atomic_failure_leaves_no_record(tmp_path: Path) -> None:
    def fail(_temporary: Path, _target: Path) -> None:
        raise OSError("injected atomic failure")

    repo = repository(tmp_path, before_replace=fail)
    with pytest.raises(OSError, match="injected"):
        repo.append(
            event(Type.CASH_DEPOSIT, {"amount": "1"}), created_at=NOW + timedelta(seconds=2)
        )
    assert tuple(repo.root.rglob("*.json")) == ()


def test_concurrent_writer_protection_commits_one_conflicting_source(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    context = multiprocessing.get_context("fork")
    gate = context.Barrier(2)
    results = context.Queue()
    workers = (
        context.Process(
            target=_concurrent_append,
            args=(str(repo.root), amount, gate, results),
        )
        for amount in ("1", "2")
    )
    processes = tuple(workers)
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0
    assert sorted((results.get(timeout=1), results.get(timeout=1))) == [
        "committed",
        "conflict",
    ]
    assert len(repo.audit(ACCOUNT, LEDGER)) == 1


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"max_record_bytes": 100}, "record size"),
        ({"max_account_records": 1}, "ledger length"),
        ({"max_repository_records": 1}, "record count"),
        ({"max_repository_bytes": 100}, "byte size"),
    ],
)
def test_repository_bounds(
    tmp_path: Path, limits: dict[str, int], expected: str
) -> None:
    repo = repository(tmp_path, **limits)
    first = event(Type.CASH_DEPOSIT, {"amount": "1"}, source_id="first")
    if "max_record_bytes" in limits or "max_repository_bytes" in limits:
        with pytest.raises(PortfolioLedgerConflictError, match=expected):
            repo.append(first, created_at=NOW + timedelta(seconds=2))
    else:
        repo.append(first, created_at=NOW + timedelta(seconds=2))
        with pytest.raises(PortfolioLedgerConflictError, match=expected):
            repo.append(
                event(Type.INTEREST, {"amount": "1"}, source_id="second"),
                created_at=NOW + timedelta(seconds=3),
            )


@pytest.mark.parametrize(
    ("payload", "changes"),
    [
        ({"amount": "1"}, {"currency": "EUR"}),
        ({"amount": 1.0}, {}),
        ({"amount": "NaN"}, {}),
        ({"amount": "Infinity"}, {}),
        ({"amount": "1", "authorization": "forbidden"}, {}),
        ({"amount": "1", "api-key": "forbidden"}, {}),
        ({"amount": "1"}, {"source_timestamp": datetime(2026, 7, 24)}),
        ({"amount": "1"}, {"source_timestamp": NOW + timedelta(hours=1)}),
    ],
)
def test_invalid_exact_inputs_rejected(
    payload: dict[str, object], changes: dict[str, object]
) -> None:
    with pytest.raises(FinancialDataValidationError):
        event(Type.CASH_DEPOSIT, payload, **changes)


def test_arbitrary_event_payload_and_unsupported_instrument_rejected() -> None:
    with pytest.raises((KeyError, AttributeError, ValueError)):
        PortfolioLedgerEvent(
            ACCOUNT,
            LEDGER,
            "arbitrary",  # type: ignore[arg-type]
            "source",
            "provider",
            "record",
            DIGEST,
            NOW,
            NOW,
            NOW + timedelta(seconds=1),
            "USD",
            {},
            POLICY.version,
        )
    with pytest.raises(FinancialDataValidationError, match="not allowed"):
        event(Type.CASH_DEPOSIT, {"amount": "1", "memo": "x"})
    with pytest.raises(FinancialDataValidationError, match="instrument"):
        buy().payload | {"instrument_type": "OPTION"}
        event(Type.BUY_FILL, {**buy().payload, "instrument_type": "OPTION"})


def test_cash_activity_and_cumulative_replay(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    entries = append_many(
        repo,
        (
            event(Type.ACCOUNT_OPENING_BALANCE, {"cash": "10"}, source_id="open"),
            event(Type.CASH_DEPOSIT, {"amount": "100"}, source_id="deposit", effective_at=NOW + timedelta(days=1)),
            event(Type.CASH_WITHDRAWAL, {"amount": "20"}, source_id="withdraw", effective_at=NOW + timedelta(days=2)),
            event(Type.DIVIDEND, {"amount": "5", "symbol": "AAPL"}, source_id="dividend", effective_at=NOW + timedelta(days=3)),
            event(Type.INTEREST, {"amount": "2"}, source_id="interest", effective_at=NOW + timedelta(days=4)),
            event(Type.FEE, {"amount": "1"}, source_id="fee", effective_at=NOW + timedelta(days=5)),
            event(Type.TAX_WITHHOLDING, {"amount": "0.5"}, source_id="tax", effective_at=NOW + timedelta(days=6)),
        ),
    )
    state = PortfolioAccountingEngine().replay(entries, POLICY)  # type: ignore[arg-type]
    assert state.current_cash == "95.5"
    assert state.net_external_cash_flow == "80"
    assert state.cumulative_dividends == "5"
    assert state.cumulative_interest == "2"
    assert state.cumulative_fees == "1"
    assert state.cumulative_withholding == "0.5"
    assert state.state_digest == replace(state, state_digest="").state_digest


def test_fifo_lots_partial_sale_fee_allocation_realized_gain(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    entries = append_many(
        repo,
        (
            event(Type.CASH_DEPOSIT, {"amount": "1000"}, source_id="cash"),
            buy(source_id="buy-one", quantity="2", price="100", fees="2"),
            buy(source_id="buy-two", quantity="1", price="110", fees="0", at=NOW + timedelta(days=1)),
            sell(source_id="sale", quantity="2.5", price="120", fees="2.5"),
        ),
    )
    state = PortfolioAccountingEngine().replay(entries, POLICY)  # type: ignore[arg-type]
    assert state.current_cash == "985.5"
    assert dict(state.position_quantities) == {"AAPL": "0.5"}
    assert dict(state.cost_basis_by_symbol) == {"AAPL": "55"}
    assert len(state.realized_records) == 2
    assert state.cumulative_realized_gain_loss == "40.5"
    assert state.realized_records[0].allocated_disposal_fees == "2"


def test_fifo_realized_loss_and_excess_sell_rejected(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    entries = append_many(
        repo,
        (
            buy(quantity="1", price="100", fees="0"),
            sell(quantity="1", price="90", fees="0"),
        ),
    )
    state = PortfolioAccountingEngine().replay(entries, POLICY)  # type: ignore[arg-type]
    assert state.cumulative_realized_gain_loss == "-10"
    extra = repo.append(
        sell(source_id="extra", quantity="1"), created_at=NOW + timedelta(days=20)
    )
    with pytest.raises(PortfolioAccountingUnavailable, match="exceeds known"):
        PortfolioAccountingEngine().replay((*entries, extra), POLICY)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("event_type", "numerator", "denominator", "quantity", "unit_basis"),
    [
        (Type.STOCK_SPLIT, "2", "1", "4", "50.5"),
        (Type.REVERSE_SPLIT, "1", "2", "1", "202"),
    ],
)
def test_split_and_reverse_split_preserve_basis(
    tmp_path: Path,
    event_type: Type,
    numerator: str,
    denominator: str,
    quantity: str,
    unit_basis: str,
) -> None:
    repo = repository(tmp_path)
    entries = append_many(
        repo,
        (
            buy(),
            event(
                event_type,
                {
                    "symbol": "AAPL",
                    "instrument_type": "EQUITY",
                    "numerator": numerator,
                    "denominator": denominator,
                },
                source_id="split",
                effective_at=NOW + timedelta(days=1),
            ),
        ),
    )
    state = PortfolioAccountingEngine().replay(entries, POLICY)  # type: ignore[arg-type]
    assert dict(state.position_quantities) == {"AAPL": quantity}
    assert state.open_lots[0].remaining_basis == "202"
    assert state.open_lots[0].unit_cost == unit_basis


def test_complete_partial_stale_valuation_and_unrealized(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    state = PortfolioAccountingEngine().replay(
        append_many(repo, (buy(),)), POLICY  # type: ignore[arg-type]
    )
    service = PortfolioValuationService()
    complete = service.value(
        state,
        valued_at=NOW + timedelta(days=1),
        source_timestamp=NOW + timedelta(days=1),
        acquired_at=NOW + timedelta(days=1, seconds=1),
        portfolio_snapshot_identity="snapshot-1",
        prices=(("AAPL", "120", NOW + timedelta(days=1), "price-1"),),
        maximum_price_age=timedelta(minutes=5),
    )
    assert complete.total_equity == "38"
    assert service.unrealized(state, complete)[0].unrealized_gain_loss == "38"
    missing = service.value(
        state,
        valued_at=NOW + timedelta(days=1),
        source_timestamp=NOW,
        acquired_at=NOW + timedelta(days=1),
        portfolio_snapshot_identity="snapshot-2",
        prices=(),
        maximum_price_age=timedelta(minutes=5),
    )
    assert missing.completeness_status is CompletenessStatus.PARTIAL
    assert missing.unpriced_positions == ("AAPL",)
    stale = service.value(
        state,
        valued_at=NOW + timedelta(days=1),
        source_timestamp=NOW,
        acquired_at=NOW + timedelta(days=1),
        portfolio_snapshot_identity="snapshot-3",
        prices=(("AAPL", "80", NOW, "price-2"),),
        maximum_price_age=timedelta(minutes=5),
    )
    assert stale.stale_price_symbols == ("AAPL",)
    with pytest.raises(PortfolioAccountingUnavailable):
        service.unrealized(state, stale)


def empty_state() -> object:
    return PortfolioAccountingEngine().replay((), POLICY)


def valuation(at: datetime, equity: str, identity: str = "snapshot"):
    from sigil.accounting import PortfolioValuation

    return PortfolioValuation(
        ACCOUNT,
        at,
        at,
        at,
        identity,
        None,
        equity,
        (),
        equity,
        (),
        (),
        CompletenessStatus.COMPLETE,
    )


def test_time_weighted_returns_and_invalid_denominator() -> None:
    service = PortfolioPerformanceService()
    begin = valuation(NOW, "100")
    end = valuation(NOW + timedelta(days=1), "110")
    assert service.time_weighted_return((begin, end), (), POLICY) == "0.1"
    assert (
        service.time_weighted_return(
            (begin, valuation(NOW + timedelta(days=1), "160")),
            ((NOW + timedelta(hours=12), "50"),),
            POLICY,
        )
        == "0.1"
    )
    assert (
        service.time_weighted_return(
            (begin, valuation(NOW + timedelta(days=1), "88")),
            ((NOW + timedelta(hours=12), "-20"),),
            POLICY,
        )
        == "0.08"
    )
    with pytest.raises(PortfolioAccountingUnavailable, match="denominator"):
        service.time_weighted_return((valuation(NOW, "0"), end), (), POLICY)


def test_money_weighted_return_valid_no_root_and_iteration_bound() -> None:
    service = PortfolioPerformanceService()
    begin = valuation(NOW, "100")
    end = valuation(NOW + timedelta(days=365), "110")
    result, reason = service.money_weighted_return(begin, end, (), POLICY)
    assert result == "0.1"
    assert reason is None
    no_root, no_root_reason = service.money_weighted_return(
        begin, valuation(NOW + timedelta(days=365), "-1"), (), POLICY
    )
    assert no_root is None
    assert no_root_reason == "no_root_in_bounded_domain"
    bounded = replace(POLICY, money_weighted_max_iterations=1, money_weighted_tolerance="0.0000000000000000000001")
    unavailable, bounded_reason = service.money_weighted_return(begin, end, (), bounded)
    assert unavailable is None
    assert bounded_reason == "convergence_iteration_limit"


def test_performance_benchmark_and_incomplete_not_lifetime() -> None:
    service = PortfolioPerformanceService()
    state = empty_state()
    state = replace(state, account_binding=ACCOUNT, state_digest="")
    report = service.report(
        state=state,
        beginning=valuation(NOW, "100"),
        ending=valuation(NOW + timedelta(days=1), "110"),
        external_cash_flows=(),
        policy=POLICY,
        realized_gain_loss="0",
        unrealized_gain_loss="10",
        benchmark=(
            "SPY-fixture",
            valuation(NOW, "100", "benchmark-start"),
            valuation(NOW + timedelta(days=1), "105", "benchmark-end"),
        ),
    )
    assert report.benchmark is not None
    assert report.benchmark.excess_return == "0.05"
    with pytest.raises(FinancialDataValidationError, match="lifetime"):
        replace(report, history_complete=False, lifetime_claim=True, report_digest="")


def test_period_close_success_and_material_discrepancy_block() -> None:
    from sigil.accounting import PortfolioLedgerDiscrepancy

    service = PortfolioPerformanceService()
    state = replace(empty_state(), account_binding=ACCOUNT, state_digest="")
    begin = valuation(NOW, "100")
    end = valuation(NOW + timedelta(days=1), "110")
    report = service.report(
        state=state,
        beginning=begin,
        ending=end,
        external_cash_flows=(),
        policy=POLICY,
        realized_gain_loss="0",
        unrealized_gain_loss="10",
    )
    state = replace(
        state,
        last_processed_sequence=1,
        ledger_chain_head="2" * 64,
        state_digest="",
    )
    closed = service.close_period(
        state=state,
        opening_state_digest="3" * 64,
        valuation=end,
        report=report,
        discrepancies=(),
        first_sequence=1,
        approval_identity="close-approval-1",
        closed_at=NOW + timedelta(days=1, seconds=1),
    )
    assert closed.close_identity
    with pytest.raises(PortfolioAccountingUnavailable, match="discrepancies"):
        service.close_period(
            state=state,
            opening_state_digest="3" * 64,
            valuation=end,
            report=report,
            discrepancies=(
                PortfolioLedgerDiscrepancy(
                    LedgerDiscrepancyCode.DERIVED_CASH_MISMATCH, "cash"
                ),
            ),
            first_sequence=1,
            approval_identity="close-approval-1",
            closed_at=NOW + timedelta(days=1, seconds=1),
        )


def brokerage_snapshot(
    *, cash: str = "798", quantity: str = "2", complete: bool = True
) -> BrokeragePortfolioSnapshot:
    account = BrokerageAccountState(
        PUBLIC_EXECUTION_PROVIDER_ID,
        ACCOUNT,
        "step12-account",
        "CASH",
        "ACTIVE",
        "USD",
        True,
        cash,
        cash,
        None,
        None,
        cash,
        "38",
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, seconds=1),
        DIGEST,
    )
    position = BrokeragePosition(
        "AAPL",
        "EQUITY",
        quantity,
        quantity,
        "101",
        "240",
        "120",
        "38",
        "USD",
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, seconds=1),
    )
    provenance = PortfolioStateProvenance(
        PUBLIC_EXECUTION_PROVIDER_ID,
        ACCOUNT,
        "portfolio",
        "/userapigateway/trading/account/portfolio",
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, seconds=1),
        DIGEST,
    )
    return BrokeragePortfolioSnapshot(
        ACCOUNT,
        account,
        (position,),
        (),
        (),
        (provenance,),
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, seconds=1),
        complete,
        complete,
        complete,
    )


def test_step11_reconciliation_match_mismatches_partial_stale_and_determinism(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    state = PortfolioAccountingEngine().replay(
        append_many(
            repo,
            (
                event(Type.CASH_DEPOSIT, {"amount": "1000"}, source_id="funding"),
                buy(),
            ),
        ),
        POLICY,  # type: ignore[arg-type]
    )
    service = PortfolioLedgerReconciliationService()
    report = service.reconcile(
        state,
        brokerage_snapshot(),
        created_at=NOW + timedelta(days=1, seconds=2),
        maximum_snapshot_age=timedelta(minutes=5),
    )
    assert report.discrepancies == ()
    mismatch = service.reconcile(
        state,
        brokerage_snapshot(cash="0", quantity="3", complete=False),
        created_at=NOW + timedelta(days=2),
        maximum_snapshot_age=timedelta(minutes=5),
    )
    codes = {item.code for item in mismatch.discrepancies}
    assert LedgerDiscrepancyCode.DERIVED_CASH_MISMATCH in codes
    assert LedgerDiscrepancyCode.POSITION_QUANTITY_MISMATCH in codes
    assert LedgerDiscrepancyCode.PARTIAL_SNAPSHOT in codes
    assert LedgerDiscrepancyCode.STALE_SNAPSHOT in codes
    assert mismatch.report_digest == service.reconcile(
        state,
        brokerage_snapshot(cash="0", quantity="3", complete=False),
        created_at=NOW + timedelta(days=2),
        maximum_snapshot_age=timedelta(minutes=5),
    ).report_digest
    assert repo.audit(ACCOUNT, LEDGER)[-1].entry_hash == state.ledger_chain_head


def test_reconciliation_detects_missing_ledger_and_missing_broker_positions(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    empty = replace(empty_state(), account_binding=ACCOUNT, state_digest="")
    service = PortfolioLedgerReconciliationService()
    missing_ledger = service.reconcile(
        empty,
        brokerage_snapshot(cash="0"),
        created_at=NOW + timedelta(days=1, seconds=2),
        maximum_snapshot_age=timedelta(minutes=5),
    )
    assert LedgerDiscrepancyCode.MISSING_LEDGER_POSITION in {
        item.code for item in missing_ledger.discrepancies
    }
    state = PortfolioAccountingEngine().replay(
        append_many(
            repo,
            (
                event(Type.CASH_DEPOSIT, {"amount": "1000"}, source_id="funding"),
                buy(),
            ),
        ),
        POLICY,  # type: ignore[arg-type]
    )
    broker_without_positions = replace(
        brokerage_snapshot(),
        positions=(),
        snapshot_id="",
    )
    missing_broker = service.reconcile(
        state,
        broker_without_positions,
        created_at=NOW + timedelta(days=1, seconds=2),
        maximum_snapshot_age=timedelta(minutes=5),
    )
    assert LedgerDiscrepancyCode.MISSING_BROKER_POSITION in {
        item.code for item in missing_broker.discrepancies
    }


def test_normalized_ingestion_exact_and_honest_completeness() -> None:
    execution = BrokerageExecution(
        "fill-1",
        "order-1",
        "client-1",
        "AAPL",
        "BUY",
        "2",
        "100",
        None,
        NOW,
        (("settlement_date", "2026-07-27"),),
    )
    ingestor = NormalizedBrokerActivityIngestor()
    normalized = ingestor.execution_event(
        execution,
        account_binding=ACCOUNT,
        ledger_identity=LEDGER,
        source_provider=PUBLIC_EXECUTION_PROVIDER_ID,
        source_response_digest=DIGEST,
        acquired_at=NOW + timedelta(seconds=1),
        accounting_policy_version=POLICY.version,
        source_complete=False,
        source_truncated=True,
    )
    assert normalized.payload["net_cash_impact"] == "200"
    assert normalized.source_complete is False
    assert normalized.source_truncated is True


def test_adjustment_exact_approval_single_use_and_terms_binding(tmp_path: Path) -> None:
    proposal = event(
        Type.RECONCILIATION_ADJUSTMENT_PROPOSED,
        {
            "amount": "5",
            "reason_code": "broker-correction",
            "affected_fields": ("cash",),
            "evidence_digest": DIGEST,
            "proposal_identity": "proposal-1",
        },
        source_id="proposal",
    )
    approval = AccountingAdjustmentApproval(
        "approval-1",
        ACCOUNT,
        proposal.event_identity,
        "broker-correction",
        "5",
        ("cash",),
        DIGEST,
        "operator-1",
        NOW + timedelta(seconds=2),
    )
    governance = AccountingAdjustmentGovernance()
    approval_event = governance.approval_event(
        proposal,
        approval,
        acquired_at=NOW + timedelta(seconds=3),
        source_response_digest=DIGEST,
        existing_entries=(),
    )
    adjustment = governance.adjustment_event(
        proposal,
        approval_event,
        acquired_at=NOW + timedelta(seconds=4),
        source_response_digest=DIGEST,
    )
    assert adjustment.payload["amount"] == "5"
    repo = repository(tmp_path)
    entries = append_many(repo, (proposal, approval_event, adjustment))
    with pytest.raises(PortfolioLedgerConflictError, match="already consumed"):
        governance.approval_event(
            proposal,
            approval,
            acquired_at=NOW + timedelta(seconds=5),
            source_response_digest=DIGEST,
            existing_entries=entries,  # type: ignore[arg-type]
        )
    with pytest.raises(PortfolioLedgerConflictError, match="exact proposal"):
        governance.approval_event(
            proposal,
            replace(approval, amount="6", approval_digest=""),
            acquired_at=NOW + timedelta(seconds=5),
            source_response_digest=DIGEST,
            existing_entries=(),
        )


def test_repository_rejects_unapproved_or_changed_adjustment(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    unapproved = event(
        Type.CASH_ADJUSTMENT,
        {
            "amount": "5",
            "reason_code": "broker-correction",
            "proposal_event_identity": "missing-proposal",
            "approval_id": "missing-approval",
            "approval_digest": DIGEST,
        },
        source_id="unapproved-adjustment",
    )
    with pytest.raises(PortfolioLedgerConflictError, match="exact committed"):
        repo.append(unapproved, created_at=NOW + timedelta(seconds=2))


def test_closed_period_rejects_activity_and_governed_reopen(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    close = event(
        Type.ACCOUNTING_PERIOD_CLOSED,
        {
            "period_start": NOW.isoformat(),
            "period_end": (NOW + timedelta(days=30)).isoformat(),
            "close_identity": "close-1",
        },
        source_id="close",
        effective_at=NOW + timedelta(days=30),
    )
    repo.append(close, created_at=NOW + timedelta(days=30, seconds=2))
    with pytest.raises(PortfolioLedgerConflictError, match="closed"):
        repo.append(
            event(
                Type.INTEREST,
                {"amount": "1"},
                source_id="late",
                effective_at=NOW + timedelta(days=2),
            ),
            created_at=NOW + timedelta(days=31),
        )
    reopen = event(
        Type.ACCOUNTING_PERIOD_REOPENED,
        {
            "period_start": NOW.isoformat(),
            "period_end": (NOW + timedelta(days=30)).isoformat(),
            "close_identity": "close-1",
            "reason_code": "late-broker-activity",
            "approval_id": "reopen-approval-1",
            "approval_digest": DIGEST,
        },
        source_id="reopen",
        effective_at=NOW + timedelta(days=31),
    )
    repo.append(reopen, created_at=NOW + timedelta(days=31, seconds=1))
    repo.append(
        event(
            Type.INTEREST,
            {"amount": "1"},
            source_id="late",
            effective_at=NOW + timedelta(days=2),
        ),
        created_at=NOW + timedelta(days=31, seconds=2),
    )


def test_incomplete_history_not_lifetime_and_audit_recovery_are_read_only(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    entry = repo.append(
        event(
            Type.CASH_DEPOSIT,
            {"amount": "1"},
            complete=False,
            source_id="partial",
        ),
        created_at=NOW + timedelta(seconds=2),
    )
    state = PortfolioAccountingEngine().replay((entry,), POLICY)
    assert state.history_complete is False
    before = tuple(repo.root.rglob("*.json"))
    assert repo.source_coverage_summary(ACCOUNT, LEDGER) == (
        ("fixture-provider", 1, False, False),
    )
    assert repo.get_event(ACCOUNT, LEDGER, entry.event.event_identity) == entry
    assert repo.find_source_record(ACCOUNT, LEDGER, "fixture-provider", "partial") == (entry,)
    repo.audit(ACCOUNT, LEDGER)
    assert tuple(repo.root.rglob("*.json")) == before
