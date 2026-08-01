from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from sigil.desktop_bridge import production_research as desktop_production_research
from sigil.market_data.alpaca import AlpacaConfig
from sigil.production_research import (
    AlpacaProductionDataClient,
    EvidenceStatus,
    MarketBar,
    MarketEvidence,
    ProductionDataError,
    ProductionResearchService,
    ProductionResearchStore,
)
from sigil.production_research.engine import (
    MAXIMUM_PROVIDER_CLOCK_SKEW_SECONDS,
    _daily_bar_is_stale,
)

NOW = datetime(2026, 7, 29, 15, 30, tzinfo=UTC)


def test_daily_bar_remains_fresh_through_the_next_regular_session() -> None:
    completed_bar = "2026-07-28T04:00:00Z"
    assert _daily_bar_is_stale(completed_bar, datetime(2026, 7, 29, 19, tzinfo=UTC)) is False
    assert _daily_bar_is_stale(completed_bar, datetime(2026, 7, 30, 15, tzinfo=UTC)) is True


def test_friday_daily_bar_remains_fresh_before_monday_close() -> None:
    completed_bar = "2026-07-24T04:00:00Z"
    assert _daily_bar_is_stale(completed_bar, datetime(2026, 7, 27, 15, tzinfo=UTC)) is False


def test_small_provider_clock_skew_is_tolerated_but_large_future_skew_fails(tmp_path) -> None:
    research = service(tmp_path)
    accepted = evidence(
        observed_at=(NOW + timedelta(seconds=MAXIMUM_PROVIDER_CLOCK_SKEW_SECONDS)).isoformat()
    )
    rejected = evidence(
        observed_at=(NOW + timedelta(seconds=MAXIMUM_PROVIDER_CLOCK_SKEW_SECONDS + 1)).isoformat()
    )
    assert research.score(asset(), accepted, now=NOW).hard_rejection_reasons == ()
    assert "stale_quote" in research.score(asset(), rejected, now=NOW).hard_rejection_reasons


def test_effective_market_data_configuration_rejects_non_paper_urls(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    with pytest.raises(RuntimeError, match="unexpected_trading_environment"):
        AlpacaConfig.from_environment()


def test_desktop_production_client_uses_governed_credential_resolver(monkeypatch):
    monkeypatch.setattr(
        desktop_production_research,
        "alpaca_credentials",
        lambda: ("file-key", "file-secret"),
    )

    client = desktop_production_research._data_client()

    assert client.key_id == "file-key"
    assert client.secret_key == "file-secret"
    assert client.base_url == "https://data.alpaca.markets"


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("credentials_unavailable", EvidenceStatus.UNAVAILABLE),
        ("authentication_failed", EvidenceStatus.PROVIDER_ERROR),
        ("rate_limited", EvidenceStatus.RATE_LIMITED),
        ("provider_request_rejected", EvidenceStatus.UNSUPPORTED),
    ],
)
def test_provider_failure_reason_is_preserved(tmp_path, reason, status):
    item = service(tmp_path).unavailable_evidence("AAPL", NOW, reason)
    assert item.status is status
    assert item.missing_classifications == (reason,)


def asset(symbol: str = "AAPL", **changes: Any) -> dict[str, Any]:
    value = {
        "asset_id": f"id-{symbol}",
        "asset_class": "us_equity",
        "exchange": "NASDAQ",
        "symbol": symbol,
        "name": f"{symbol} Corporation",
        "status": "active",
        "tradable": True,
        "fractionable": True,
        "proposal_eligible": True,
    }
    value.update(changes)
    return value


def bars(
    *,
    count: int = 60,
    end: datetime = NOW,
    start_price: Decimal = Decimal(80),
    daily_gain: Decimal = Decimal("0.002"),
    volume: Decimal = Decimal(1000000),
) -> tuple[MarketBar, ...]:
    result = []
    price = start_price
    for index in range(count):
        current = end - timedelta(days=count - index - 1)
        open_price = price
        close = price * (Decimal(1) + daily_gain)
        result.append(
            MarketBar(
                timestamp=current.isoformat().replace("+00:00", "Z"),
                open=open_price,
                high=max(open_price, close) * Decimal("1.002"),
                low=min(open_price, close) * Decimal("0.998"),
                close=close,
                volume=volume,
            )
        )
        price = close
    return tuple(result)


def evidence(symbol: str = "AAPL", **changes: Any) -> MarketEvidence:
    value = MarketEvidence(
        symbol=symbol,
        observed_at=NOW.isoformat().replace("+00:00", "Z"),
        received_at=NOW.isoformat().replace("+00:00", "Z"),
        source="alpaca_market_data",
        feed="iex",
        adjustment="all",
        status=EvidenceStatus.COMPLETE,
        bid=Decimal("99.98"),
        ask=Decimal("100.02"),
        bid_size=Decimal(100),
        ask_size=Decimal(120),
        last_trade=Decimal(100),
        last_trade_at=NOW.isoformat().replace("+00:00", "Z"),
        daily_bars=bars(),
    )
    return replace(value, **changes)


def service(tmp_path: Path) -> ProductionResearchService:
    return ProductionResearchService(ProductionResearchStore(tmp_path.resolve()))


def score(
    service: ProductionResearchService,
    *,
    asset_value: dict[str, Any] | None = None,
    evidence_value: MarketEvidence | None = None,
    **gates: Any,
):
    return service.score(
        asset_value or asset(),
        evidence_value or evidence(),
        now=NOW,
        **gates,
    )


def test_valid_production_evidence_produces_complete_score(tmp_path):
    result = score(service(tmp_path))
    assert result.strategy_id == "sigil-liquid-trend"
    assert result.strategy_version == "3.5.0"
    assert result.eligible is True
    assert result.normalized_score >= Decimal("0.68")
    assert result.confidence >= Decimal("0.80")
    assert result.evidence_checksum == evidence().evidence_checksum
    assert len(result.component_scores) == 9
    assert dict(result.component_evidence)["spread_bps"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"bid": None, "ask": None}, "missing_quote"),
        ({"daily_bars": ()}, "insufficient_history"),
        (
            {"observed_at": (NOW - timedelta(seconds=31)).isoformat().replace("+00:00", "Z")},
            "stale_quote",
        ),
        (
            {"daily_bars": bars(end=NOW - timedelta(days=3))},
            "stale_bars",
        ),
        ({"status": EvidenceStatus.CONTRADICTORY}, "evidence_contradictory"),
        ({"status": EvidenceStatus.MALFORMED}, "evidence_malformed"),
        ({"daily_bars": bars(count=49)}, "insufficient_history"),
        ({"bid": Decimal(99), "ask": Decimal(101)}, "excessive_spread"),
        (
            {"daily_bars": bars(volume=Decimal(100))},
            "insufficient_liquidity",
        ),
    ],
)
def test_market_evidence_hard_failures_are_explicit(tmp_path, change, reason):
    result = score(service(tmp_path), evidence_value=evidence(**change))
    assert result.eligible is False
    assert reason in result.hard_rejection_reasons
    assert reason not in result.soft_penalties


@pytest.mark.parametrize(
    ("asset_change", "reason"),
    [
        ({"asset_class": "crypto"}, "unsupported_asset"),
        ({"asset_class": "option"}, "unsupported_asset"),
        ({"status": "inactive"}, "inactive_asset"),
        ({"tradable": False}, "not_tradable"),
        ({"fractionable": False}, "not_fractionable"),
        ({"exchange": "OTC"}, "otc_forbidden"),
        ({"name": "Daily 3X Bull ETF"}, "leveraged_or_inverse_forbidden"),
        ({"name": "Inverse Bear ETF"}, "leveraged_or_inverse_forbidden"),
    ],
)
def test_unsupported_assets_fail_closed(tmp_path, asset_change, reason):
    result = score(service(tmp_path), asset_value=asset(**asset_change))
    assert result.eligible is False
    assert reason in result.hard_rejection_reasons


@pytest.mark.parametrize(
    ("gates", "reason"),
    [
        ({"catalog_fresh": False}, "stale_catalog"),
        ({"portfolio_fresh": False}, "stale_portfolio"),
        ({"market_state_known": False}, "unknown_market_state"),
        ({"paused": True}, "execution_paused"),
        ({"kill_switch": True}, "kill_switch_active"),
        ({"audit_available": False}, "audit_unavailable"),
        ({"reconciliation_complete": False}, "reconciliation_required"),
        ({"duplicate_position": True}, "duplicate_position"),
        ({"duplicate_order": True}, "duplicate_open_order"),
    ],
)
def test_governance_hard_gates_are_not_score_penalties(tmp_path, gates, reason):
    result = score(service(tmp_path), **gates)
    assert reason in result.hard_rejection_reasons
    assert reason not in result.soft_penalties


def test_demonstration_data_cannot_become_production_complete_or_scored(tmp_path):
    with pytest.raises(ValueError, match="demonstration evidence"):
        evidence(demonstration=True)
    demo = replace(
        evidence(),
        status=EvidenceStatus.UNSUPPORTED,
        demonstration=True,
    )
    result = score(service(tmp_path), evidence_value=demo)
    assert "demonstration_evidence_forbidden" in result.hard_rejection_reasons


def test_identical_evidence_is_reproducible_and_ranking_is_deterministic(tmp_path):
    research = service(tmp_path)
    first = score(research)
    second = score(research)
    assert first == second
    assert first.to_dict() == second.to_dict()
    a = replace(first, symbol="AAA")
    z = replace(first, symbol="ZZZ")
    liquid = replace(first, symbol="LIQ", average_dollar_volume=Decimal(999999999))
    assert min([z, a, liquid], key=lambda item: item.ranking_key()).symbol == "LIQ"
    assert min([z, a], key=lambda item: item.ranking_key()).symbol == "AAA"


def test_soft_penalties_are_distinct_and_reproducible(tmp_path):
    extended = evidence(daily_bars=bars(daily_gain=Decimal("0.012")))
    result = score(service(tmp_path), evidence_value=extended)
    assert "momentum_extended" in result.soft_penalties
    assert "momentum_extended" not in result.hard_rejection_reasons


def process(
    research: ProductionResearchService,
    assets: list[dict[str, Any]],
    items: tuple[MarketEvidence, ...],
):
    return research.process_batch(
        assets,
        items,
        cursor=25,
        batch_number=1,
        total_eligible=12984,
        now=NOW,
        next_cycle_at=(NOW + timedelta(seconds=5)).isoformat(),
        catalog_fresh=True,
        portfolio_fresh=True,
        market_state_known=True,
        paused=False,
        kill_switch=False,
        audit_available=True,
        reconciliation_complete=True,
    )


def test_completed_batch_generates_shadow_proposal_before_catalog_traversal(tmp_path):
    research = service(tmp_path)
    status = process(research, [asset("AAPL")], (evidence("AAPL"),))
    assert status["progress"]["state"] == "proposal_generated"
    assert status["progress"]["current_cursor"] == 25
    assert status["progress"]["total_eligible"] == 12984
    assert status["shadow_mode"] is True
    assert status["active_shadow_positions"] == 1
    proposal = research.recent("proposals")["items"][0]
    assert proposal["status"] == "admitted_in_shadow"
    assert proposal["strategy_version"] == "3.5.0"
    assert proposal["evidence_identity"]
    assert proposal["proposed_notional"] == "25.00"
    assert proposal["exit_plan"]["maximum_holding_days"] == 10


def test_no_candidate_batch_advances_and_records_exact_reasons(tmp_path):
    research = service(tmp_path)
    status = process(
        research,
        [asset("BAD", tradable=False)],
        (evidence("BAD"),),
    )
    assert status["progress"]["state"] == "no_eligible_candidate"
    assert status["progress"]["current_cursor"] == 25
    assert status["progress"]["leading_rejection_reasons"]["not_tradable"] == 1
    assert status["proposal_count"] == 0


def test_batch_is_bounded_and_records_all_research(tmp_path):
    research = service(tmp_path)
    with pytest.raises(ValueError, match="bounded to 25"):
        process(
            research,
            [asset(f"S{index}") for index in range(26)],
            tuple(evidence(f"S{index}") for index in range(26)),
        )
    values = [asset(f"S{index:02d}") for index in range(25)]
    result = process(
        research,
        values,
        tuple(evidence(item["symbol"]) for item in values),
    )
    assert result["research_result_count"] == 25
    assert result["proposal_count"] == 1


def test_proposal_identity_and_shadow_fill_are_deterministic(tmp_path):
    first = service(tmp_path / "one")
    second = service(tmp_path / "two")
    process(first, [asset()], (evidence(),))
    process(second, [asset()], (evidence(),))
    proposal_one = first.recent("proposals")["items"][0]
    proposal_two = second.recent("proposals")["items"][0]
    shadow_one = first.recent("shadow_positions")["items"][0]
    shadow_two = second.recent("shadow_positions")["items"][0]
    assert proposal_one["proposal_id"] == proposal_two["proposal_id"]
    assert shadow_one["hypothetical_fill"] == shadow_two["hypothetical_fill"]
    assert Decimal(shadow_one["hypothetical_fill"]) > Decimal(proposal_one["reference_price"])


@pytest.mark.parametrize(
    ("price", "trigger"),
    [
        (Decimal(90), "protective_stop"),
        (Decimal(111), "profit_taking"),
    ],
)
def test_shadow_price_exits_are_deterministic_and_decimal(tmp_path, price, trigger):
    research = service(tmp_path)
    process(research, [asset()], (evidence(),))
    followup = evidence(bid=price, ask=price + Decimal("0.01"))
    status = research.monitor_shadow((followup,), now=NOW + timedelta(hours=1))
    assert status["active_shadow_positions"] == 0
    outcome = research.recent("shadow_outcomes")["items"][0]
    assert outcome["exit_trigger"] == trigger
    Decimal(outcome["gross_return"])
    Decimal(outcome["net_simulated_return"])


def test_shadow_maximum_hold_and_missing_followup_are_classified(tmp_path):
    research = service(tmp_path)
    process(research, [asset()], (evidence(),))
    status = research.monitor_shadow((), now=NOW + timedelta(hours=1))
    assert status["active_shadow_positions"] == 1
    assert research.recent("shadow_positions")["items"][0]["status"] == (
        "insufficient_followup_data"
    )
    followup = evidence(bid=Decimal(100), ask=Decimal("100.01"))
    status = research.monitor_shadow((followup,), now=NOW + timedelta(days=11))
    assert status["active_shadow_positions"] == 0
    assert research.recent("shadow_outcomes")["items"][0]["exit_trigger"] == (
        "maximum_holding_period"
    )


def test_shadow_state_and_kill_switch_defaults_survive_restart(tmp_path):
    research = service(tmp_path)
    process(research, [asset()], (evidence(),))
    restarted = ProductionResearchService(research.store)
    assert restarted.status()["shadow_mode"] is True
    assert restarted.status()["active_shadow_positions"] == 1
    assert restarted.status()["broker_submission"] is False
    assert restarted.status()["live_execution"] is False


def test_promotion_readiness_fails_closed_and_reports_every_condition(tmp_path):
    research = service(tmp_path)
    readiness = research.promotion_readiness()
    assert readiness["ready"] is False
    assert readiness["status"] == "promotion_not_ready"
    assert "minimum_completed_shadow_proposals" in readiness["failed_conditions"]
    assert "minimum_distinct_symbols" in readiness["failed_conditions"]
    assert readiness["profit_guarantee"] is False
    with pytest.raises(ValueError, match="not ready"):
        research.request_promotion()
    with pytest.raises(ValueError, match="cannot be disabled"):
        research.set_shadow_mode(False)


def test_safety_defect_blocks_promotion(tmp_path):
    research = service(tmp_path)
    with research.store.locked() as state:
        state["safety_defects"] = ["restart_recovery_defect"]
        research.store.save(state)
    readiness = research.promotion_readiness()
    assert "no_unresolved_safety_defects" in readiness["failed_conditions"]


def test_store_checksum_corruption_fails_closed(tmp_path):
    research = service(tmp_path)
    research.set_shadow_mode(True)
    envelope = json.loads(research.store.path.read_text())
    envelope["payload"]["shadow_mode"] = False
    research.store.path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="integrity"):
        research.status()


class FakeMarketData:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.statuses: list[int] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, object]:
        del timeout
        assert url.startswith("https://data.alpaca.markets/v2/stocks/")
        assert headers["APCA-API-KEY-ID"] == "paper-key"
        assert headers["APCA-API-SECRET-KEY"] == "paper-secret"
        self.calls.append(url)
        if self.statuses:
            status = self.statuses.pop(0)
            if status != 200:
                return status, {}
        parsed = urlparse(url)
        symbols = parse_qs(parsed.query)["symbols"][0].split(",")
        if parsed.path.endswith("/quotes/latest"):
            return 200, {
                "quotes": {
                    symbol: {
                        "bp": "99.98",
                        "ap": "100.02",
                        "bs": "10",
                        "as": "12",
                        "t": NOW.isoformat(),
                    }
                    for symbol in symbols
                }
            }
        if parsed.path.endswith("/trades/latest"):
            return 200, {
                "trades": {symbol: {"p": "100", "t": NOW.isoformat()} for symbol in symbols}
            }
        return 200, {
            "bars": {
                symbol: [
                    {
                        "t": bar.timestamp,
                        "o": str(bar.open),
                        "h": str(bar.high),
                        "l": str(bar.low),
                        "c": str(bar.close),
                        "v": str(bar.volume),
                    }
                    for bar in bars()
                ]
                for symbol in symbols
            }
        }


def test_bounded_market_data_client_produces_valid_evidence():
    fake = FakeMarketData()
    client = AlpacaProductionDataClient("paper-key", "paper-secret", transport=fake)
    result = client.collect_batch(("MSFT", "AAPL"), now=NOW)
    assert [item.symbol for item in result] == ["AAPL", "MSFT"]
    assert all(item.status is EvidenceStatus.COMPLETE for item in result)
    assert all(len(item.daily_bars) == 60 for item in result)
    assert len(fake.calls) == 3
    assert all("symbols=AAPL%2CMSFT" in call for call in fake.calls)
    bars_call = next(call for call in fake.calls if "/bars?" in call)
    assert "start=2026-03-31T00%3A00%3A00Z" in bars_call
    assert "end=2026-07-29T00%3A00%3A00Z" in bars_call


def test_market_data_missing_symbol_does_not_block_batch():
    fake = FakeMarketData()

    def partial(url, headers, timeout):
        status, payload = fake(url, headers, timeout)
        key = next(iter(payload))
        payload[key].pop("MSFT", None)
        return status, payload

    client = AlpacaProductionDataClient("paper-key", "paper-secret", transport=partial)
    result = client.collect_batch(("AAPL", "MSFT"), now=NOW)
    assert result[0].status is EvidenceStatus.COMPLETE
    assert result[1].status is EvidenceStatus.INCOMPLETE


def test_zero_quote_rejects_only_affected_symbol():
    fake = FakeMarketData()

    def zero_quote(url, headers, timeout):
        status, payload = fake(url, headers, timeout)
        if urlparse(url).path.endswith("/quotes/latest"):
            payload["quotes"]["MSFT"]["bp"] = "0"
            payload["quotes"]["MSFT"]["ap"] = "0"
        return status, payload

    client = AlpacaProductionDataClient("paper-key", "paper-secret", transport=zero_quote)
    result = client.collect_batch(("AAPL", "MSFT"), now=NOW)
    by_symbol = {item.symbol: item for item in result}
    assert by_symbol["AAPL"].status is EvidenceStatus.COMPLETE
    assert by_symbol["MSFT"].status is EvidenceStatus.INCOMPLETE
    assert by_symbol["MSFT"].bid is None
    assert by_symbol["MSFT"].ask is None
    assert set(by_symbol["MSFT"].missing_classifications) == {
        "invalid_ask",
        "invalid_bid",
        "invalid_quote",
    }


def test_market_data_retries_only_reads_with_deterministic_backoff():
    fake = FakeMarketData()
    fake.statuses = [429, 500]
    waits: list[float] = []
    client = AlpacaProductionDataClient(
        "paper-key",
        "paper-secret",
        transport=fake,
        retry_wait=waits.append,
    )
    result = client.collect_batch(("AAPL",), now=NOW)
    assert result[0].status is EvidenceStatus.COMPLETE
    assert waits == [0.25, 0.5]


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "authentication_failed"), (422, "provider_request_rejected")],
)
def test_market_data_provider_failures_are_sanitized(status, code):
    fake = FakeMarketData()
    fake.statuses = [status]
    client = AlpacaProductionDataClient("paper-key", "paper-secret", transport=fake)
    with pytest.raises(ProductionDataError, match=code):
        client.collect_batch(("AAPL",), now=NOW)


def test_market_data_batch_limit_is_enforced():
    client = AlpacaProductionDataClient("paper-key", "paper-secret")
    with pytest.raises(ValueError, match="1 to 25"):
        client.collect_batch(tuple(f"S{index}" for index in range(26)), now=NOW)


def test_historical_validation_is_chronological_reproducible_and_costed(tmp_path):
    research = service(tmp_path)
    dataset = {"AAPL": bars(count=90)}
    first = research.validation_report(
        dataset, dataset_identity="fixture-v1", slippage_bps=Decimal(5)
    )
    second = research.validation_report(
        dataset, dataset_identity="fixture-v1", slippage_bps=Decimal(5)
    )
    assert first["report_checksum"] == second["report_checksum"]
    assert first["walk_forward"] is True
    assert first["chronological"] is True
    assert first["future_data_leakage"] is False
    assert all(item["decision_index"] < item["exit_index"] for item in first["signals"])
    assert first["slippage_bps"] == "5"
    assert first["survivorship_bias_caveat"] is True


def test_higher_slippage_never_improves_validation_return(tmp_path):
    research = service(tmp_path)
    dataset = {"AAPL": bars(count=90)}
    low = research.validation_report(
        dataset, dataset_identity="fixture-v1", slippage_bps=Decimal(1)
    )
    high = research.validation_report(
        dataset, dataset_identity="fixture-v1", slippage_bps=Decimal(20)
    )
    assert Decimal(high["estimated_net_return"]) <= Decimal(low["estimated_net_return"])


def test_recent_payloads_are_paginated_and_bounded(tmp_path):
    research = service(tmp_path)
    process(
        research,
        [asset(f"S{index:02d}") for index in range(25)],
        tuple(evidence(f"S{index:02d}") for index in range(25)),
    )
    page = research.recent("research", offset=-1, limit=10000)
    assert page["offset"] == 0
    assert page["limit"] == 100
    assert len(page["items"]) == 25
    assert page["environment"] == "paper"
    assert page["shadow_mode"] is True
    assert page["live_execution"] is False


def test_secrets_and_live_trading_url_never_enter_state(tmp_path):
    research = service(tmp_path)
    process(research, [asset()], (evidence(),))
    serialized = research.store.path.read_text()
    assert "paper-key" not in serialized
    assert "paper-secret" not in serialized
    assert "https://api.alpaca.markets" not in serialized


def test_default_shadow_certification_performs_no_broker_calls(tmp_path):
    research = service(tmp_path)
    status = process(research, [asset()], (evidence(),))
    assert status["broker_submission"] is False
    assert status["live_execution"] is False
    assert status["shadow_mode"] is True
    assert research.recent("audit")["items"][0]["details"]["broker_submission_attempted"] is False


def test_promotion_metrics_only_include_current_strategy_outcomes(tmp_path) -> None:
    research = service(tmp_path)

    state = {
        "strategy": None,
        "paper_promotion_approved": False,
        "shadow_mode": True,
        "progress": {},
        "research_results": [],
        "candidates": [],
        "proposals": [],
        "shadow_positions": [],
        "safety_defects": [],
        "audit": [],
        "revision": 1,
        "shadow_outcomes": [
            {
                "strategy_version": "2.2.0",
                "symbol": "OLD1",
                "entry_at": "2026-07-01T12:00:00+00:00",
                "net_simulated_return": "-0.500000",
            },
            {
                "strategy_version": "2.4.0",
                "symbol": "OLD2",
                "entry_at": "2026-07-02T12:00:00+00:00",
                "net_simulated_return": "-0.250000",
            },
            {
                "strategy_version": "3.5.0",
                "symbol": "NEW1",
                "entry_at": "2026-07-10T12:00:00+00:00",
                "net_simulated_return": "0.020000",
            },
            {
                "strategy_version": "3.5.0",
                "symbol": "NEW2",
                "entry_at": "2026-07-12T12:00:00+00:00",
                "net_simulated_return": "-0.005000",
            },
        ],
    }

    projection = research._projection(state)
    promotion = projection["promotion"]

    assert len(state["shadow_outcomes"]) == 4
    assert projection["completed_shadow_outcomes"] == 2
    assert projection["shadow_simulated_return"] == "0.015000"
    assert projection["shadow_win_rate"] == "0.500000"

    assert promotion["completed_shadow_proposals"] == 2
    assert promotion["distinct_symbols"] == 2
    assert promotion["observation_days"] == 2
    assert promotion["net_simulated_return"] == "0.015000"
    assert promotion["maximum_drawdown"] == "0.005000"
