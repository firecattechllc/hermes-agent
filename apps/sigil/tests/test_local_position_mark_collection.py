from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace


from sigil.desktop_bridge import production_research


NOW = datetime(2026, 7, 29, 21, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, evidence):
        self.evidence = evidence

    def collect_batch(self, symbols, *, now):
        assert symbols == ("AAPL",)
        assert now == NOW
        return self.evidence


def evidence(
    *,
    status: str = "incomplete",
    bid: Decimal | None = Decimal("105.25"),
    observed_at: datetime = NOW - timedelta(hours=5),
    received_at: datetime = NOW,
):
    return SimpleNamespace(
        symbol="AAPL",
        status=SimpleNamespace(value=status),
        bid=bid,
        observed_at=observed_at.isoformat(),
        received_at=received_at.isoformat(),
        source="alpaca_market_data",
        feed="iex",
        evidence_checksum="evidence-checksum",
    )


def install(monkeypatch, item):
    monkeypatch.setattr(
        production_research.AlpacaProductionDataClient,
        "from_environment",
        classmethod(lambda cls: FakeClient((item,))),
    )


def test_latest_available_after_hours_quote_produces_pnl_mark(monkeypatch):
    install(monkeypatch, evidence())

    marks = production_research.collect_local_position_marks(
        ("AAPL",),
        now=NOW,
    )

    assert marks["AAPL"]["status"] == "fresh"
    assert marks["AAPL"]["price"] == "105.25"
    assert marks["AAPL"]["reason"] is None
    assert marks["AAPL"]["source"].endswith("latest_available_bid")


def test_contradictory_quote_remains_fail_closed(monkeypatch):
    install(monkeypatch, evidence(status="contradictory"))

    marks = production_research.collect_local_position_marks(
        ("AAPL",),
        now=NOW,
    )

    assert marks["AAPL"]["status"] == "unavailable"
    assert marks["AAPL"]["price"] is None
    assert marks["AAPL"]["reason"] == "position_mark_contradictory"


def test_extremely_old_quote_remains_stale(monkeypatch):
    install(
        monkeypatch,
        evidence(observed_at=NOW - timedelta(days=8)),
    )

    marks = production_research.collect_local_position_marks(
        ("AAPL",),
        now=NOW,
    )

    assert marks["AAPL"]["status"] == "stale"
    assert marks["AAPL"]["price"] is None
    assert marks["AAPL"]["reason"] == "position_mark_stale"


def test_stale_provider_response_remains_unavailable(monkeypatch):
    install(
        monkeypatch,
        evidence(received_at=NOW - timedelta(minutes=2)),
    )

    marks = production_research.collect_local_position_marks(
        ("AAPL",),
        now=NOW,
    )

    assert marks["AAPL"]["status"] == "unavailable"
    assert marks["AAPL"]["price"] is None
    assert marks["AAPL"]["reason"] == "position_mark_provider_response_stale"
