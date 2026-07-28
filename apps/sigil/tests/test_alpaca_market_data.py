from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from sigil.market_data import AlpacaMarketDataRouter, MarketDataPolicy, MarketDataPolicyError
from sigil.market_data.alpaca import (
    AlpacaConfig,
    AlpacaHttpClient,
    DelayedSipScanner,
    IexStreamManager,
    RankedCandidate,
)
from sigil.market_data.alpaca import client as alpaca_client
from sigil.market_data.audit import MarketDataAudit
from sigil.market_data.cache import MarketDataCache
from sigil.market_universe.providers.alpaca import AlpacaAssetCatalogProvider

NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def asset(index: int, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": f"asset-{index}", "symbol": f"T{index:05d}", "name": f"Asset {index}",
        "class": "us_equity", "exchange": "NASDAQ", "status": "active",
        "tradable": True, "fractionable": True, "marginable": True,
        "shortable": True, "easy_to_borrow": True, "borrow_status": "easy",
        "maintenance_margin_requirement": 30,
    }
    value.update(changes)
    return value


def test_configuration_secret_redaction_and_authentication_failures() -> None:
    assert not AlpacaConfig(None, None).configured
    assert AlpacaConfig("key", "secret").configured
    events: list[dict[str, object]] = []
    MarketDataAudit(events.append).record(
        "provider_degraded",
        {"api_key": "sensitive-id-value", "secret_key": "sensitive-secret-value", "reason": "401"},
    )
    assert "sensitive-id-value" not in str(events)
    assert "sensitive-secret-value" not in str(events)
    client = AlpacaHttpClient(
        AlpacaConfig("key", "secret"),
        transport=lambda _url, _headers, _timeout: (401, {}),
    )
    with pytest.raises(RuntimeError, match="authentication_failed"):
        client.assets()


def test_urllib_transport_requests_and_decodes_gzip(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status = 200
        headers = {"Content-Encoding": "gzip"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return gzip.compress(json.dumps([asset(1)]).encode())

    def urlopen(request: object, *, timeout: float) -> Response:
        captured["accept_encoding"] = request.get_header("Accept-encoding")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(alpaca_client.urllib.request, "urlopen", urlopen)
    status, payload = alpaca_client._urllib_transport(
        "https://provider.invalid/v2/assets",
        {"APCA-API-KEY-ID": "key", "APCA-API-SECRET-KEY": "secret"},
        7.5,
    )
    assert status == 200
    assert payload == [asset(1)]
    assert captured == {"accept_encoding": "gzip", "timeout": 7.5}


def test_assets_are_normalized_or_explicitly_excluded() -> None:
    rows = [
        asset(1), asset(2, status="inactive"), asset(3, **{"class": "crypto"}),
        asset(4, symbol="bad symbol"), asset(5, id=""), asset(6, exchange="OTC"),
        asset(7, symbol="TESTFAKE"), asset(8, symbol="ETF", attributes=["etf"]),
        asset(9, id="asset-1"), asset(10, symbol="T00001"), None,
    ]
    result = AlpacaAssetCatalogProvider().ingest(rows, observed_at="2026-07-27T12:00:00Z")
    assert result.source_count == 11
    assert [item.symbol for item in result.accepted] == ["T00001", "ETF"]
    assert result.accepted[1].asset_class == "etf"
    assert result.accepted[0].borrow_status == "easy"
    assert {item.reason for item in result.excluded} == {
        "inactive", "unsupported_asset_class", "malformed_symbol", "missing_asset_id",
        "unsupported_exchange", "test_asset", "duplicate_asset", "identity_conflict",
        "insufficient_provider_evidence",
    }
    assert result.conflict_count == 1


def test_delayed_sip_boundary_batching_missing_and_malformed() -> None:
    calls: list[tuple[str, ...]] = []

    def fetch(symbols: tuple[str, ...], **_query: str) -> object:
        calls.append(symbols)
        return {"bars": {
            symbol: [{"t": (NOW - timedelta(minutes=15)).isoformat(), "c": "10", "v": "5"}]
            for symbol in symbols if symbol != "T00003"
        }}

    scanner = DelayedSipScanner(MarketDataPolicy(batch_size=2), fetch)
    observations, checkpoint, counts = scanner.scan(
        [(f"ID-{index}", f"T{index:05d}") for index in range(1, 6)], now=NOW
    )
    assert [len(call) for call in calls] == [2, 2, 1]
    assert checkpoint.next_batch == checkpoint.total_batches == 3
    assert counts == {"successful": 4, "missing": 1, "rejected": 0, "stale": 0}
    assert all(
        item.classification == "delayed" and item.quality_flags == ("delayed",)
        for item in observations
    )
    with pytest.raises(MarketDataPolicyError, match="too_recent"):
        MarketDataPolicy().validate_delayed_timestamp(
            (NOW - timedelta(minutes=14, seconds=59)).isoformat(), NOW.isoformat()
        )


def test_iex_cap_rotation_ties_duplicates_dwell_and_disconnect() -> None:
    subscribed: list[tuple[str, ...]] = []
    unsubscribed: list[tuple[str, ...]] = []
    manager = IexStreamManager(
        MarketDataPolicy(iex_symbol_limit=2), subscribe=subscribed.append,
        unsubscribe=unsubscribed.append, minimum_dwell_seconds=0, cooldown_seconds=0,
    )
    candidates = [
        RankedCandidate("2", "B", 1, "rank"), RankedCandidate("1", "A", 1, "rank"),
        RankedCandidate("3", "A", 2, "duplicate"),
    ]
    assert manager.rotate(candidates, now=NOW) == ("A", "B")
    assert subscribed == [("A", "B")]
    assert manager.rotate(candidates, now=NOW) == ("A", "B")
    manager.disconnect()
    assert unsubscribed == [("A", "B")]
    with pytest.raises(MarketDataPolicyError, match="capacity"):
        IexStreamManager(
            MarketDataPolicy(), subscribe=lambda _s: None, unsubscribe=lambda _s: None
        ).rotate(
            [RankedCandidate(str(index), f"T{index}", index, "rank") for index in range(31)],
            now=NOW,
        )
    with pytest.raises(MarketDataPolicyError, match="maximum 30"):
        MarketDataPolicy(iex_symbol_limit=31)


def test_atomic_cache_corruption_and_data_only_projection(tmp_path) -> None:
    cache = MarketDataCache((tmp_path / "data.json").resolve(), retention=2)
    cache.write({"observations": [1, 2, 3]})
    assert cache.read()["observations"] == [2, 3]
    cache.path.write_text("{}")
    with pytest.raises(ValueError, match="unreadable"):
        cache.read()
    projection = AlpacaMarketDataRouter(config=AlpacaConfig(None, None)).projection()
    assert projection["delayed_sip"]["classification"] == "15-minute delayed SIP"
    assert projection["live_iex"]["classification"] == "live partial-market IEX"
    assert projection["live_iex"]["maximum_symbol_count"] == 30
    assert projection["safety"] == {
        "broker_submission_available": False, "execution_authorized": False,
        "live_trading_enabled": False, "data_only_mode": True,
    }


def test_synthetic_10_000_asset_ingestion_and_bounded_scan() -> None:
    result = AlpacaAssetCatalogProvider().ingest(
        (asset(index) for index in range(10_000)), observed_at="2026-07-27T12:00:00Z"
    )
    assert len(result.accepted) == 10_000 and not result.excluded
    sizes: list[int] = []

    def fetch(symbols: tuple[str, ...], **_query: str) -> object:
        sizes.append(len(symbols))
        return {"bars": {}}

    scanner = DelayedSipScanner(MarketDataPolicy(batch_size=200), fetch)
    _, checkpoint, counts = scanner.scan(
        ((f"ID-{index}", item.symbol) for index, item in enumerate(result.accepted)), now=NOW
    )
    assert sizes == [200] * 50
    assert checkpoint.total_batches == 50
    assert counts["missing"] == 10_000
