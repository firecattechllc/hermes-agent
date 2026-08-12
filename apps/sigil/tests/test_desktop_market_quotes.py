from datetime import UTC, datetime

import sigil.desktop_bridge.market_quotes as market_quotes
from sigil.desktop_bridge.market_quotes import market_universe_quotes
from sigil.market_data.alpaca.client import AlpacaConfig


class SnapshotClient:
    def stock_snapshots(self, symbols, *, feed):
        assert symbols == ("AAPL", "MSFT")
        assert feed == "iex"

        return {
            "AAPL": {
                "latestTrade": {
                    "p": 207.57,
                    "t": "2026-08-01T01:20:00Z",
                },
                "dailyBar": {"c": 207.57},
                "prevDailyBar": {"c": 205.03},
            },
            "MSFT": {
                "latestTrade": {
                    "p": 452.80,
                    "t": "2026-08-01T01:19:30Z",
                },
                "dailyBar": {"c": 452.80},
                "prevDailyBar": {"c": 450.00},
            },
        }


def test_visible_quotes_are_bounded_read_only_and_fresh():
    result = market_universe_quotes(
        {"symbols": ["aapl", "MSFT", "AAPL"]},
        client=SnapshotClient(),
        now=datetime(2026, 8, 1, 1, 20, 5, tzinfo=UTC),
    )

    assert result["feed"] == "iex"
    assert result["broker_submission_available"] is False
    assert result["execution_authorized"] is False
    assert result["data_only"] is True
    assert len(result["quotes"]) == 2

    apple = result["quotes"][0]

    assert apple["symbol"] == "AAPL"
    assert apple["price"] == 207.57
    assert round(apple["change"], 2) == 2.54
    assert round(apple["change_percent"], 2) == 1.24
    assert apple["source"] == "Live IEX"
    assert apple["age_seconds"] == 5


def test_missing_snapshot_remains_visible_without_inventing_price():
    class MissingClient:
        def stock_snapshots(self, symbols, *, feed):
            return {}

    result = market_universe_quotes(
        {"symbols": ["AAPL"]},
        client=MissingClient(),
    )

    quote = result["quotes"][0]

    assert quote["symbol"] == "AAPL"
    assert quote["price"] is None
    assert quote["source"] == "Price unavailable"
    assert quote["reason"] == "snapshot_missing"
    assert result["broker_submission_available"] is False


def test_quote_projection_never_exposes_credentials():
    class SafeClient:
        def stock_snapshots(self, symbols, *, feed):
            return {
                "AAPL": {
                    "latestTrade": {
                        "p": 207.57,
                        "t": "2026-08-01T01:20:00Z",
                    },
                    "prevDailyBar": {"c": 205.03},
                }
            }

    result = market_universe_quotes(
        {"symbols": ["AAPL"]},
        client=SafeClient(),
        now=datetime(2026, 8, 1, 1, 20, 5, tzinfo=UTC),
    )

    serialized = str(result)

    assert "API_KEY" not in serialized
    assert "SECRET" not in serialized
    assert result["broker_submission_available"] is False
    assert result["execution_authorized"] is False


def test_visible_quotes_use_shared_credentials_and_fixed_endpoints(monkeypatch):
    captured = {}

    class ResolverClient:
        def __init__(self, config):
            captured["config"] = config

        def stock_snapshots(self, symbols, *, feed):
            assert symbols == ("AAPL",)
            assert feed == "iex"
            return {
                "AAPL": {
                    "latestTrade": {"p": 207.57, "t": "2026-08-01T01:20:00Z"},
                    "prevDailyBar": {"c": 205.03},
                }
            }

    monkeypatch.setattr(
        market_quotes, "alpaca_credentials", lambda: ("resolver-key", "resolver-secret")
    )
    monkeypatch.setattr(market_quotes, "AlpacaHttpClient", ResolverClient)

    result = market_universe_quotes(
        {"symbols": ["AAPL"]},
        now=datetime(2026, 8, 1, 1, 20, 5, tzinfo=UTC),
    )

    config = captured["config"]
    assert config.key_id == "resolver-key"
    assert config.secret_key == "resolver-secret"
    assert config.api_base_url == "https://paper-api.alpaca.markets"
    assert config.data_base_url == "https://data.alpaca.markets"
    assert result["data_only"] is True
    assert result["execution_authorized"] is False
    assert result["broker_submission_available"] is False
    assert "resolver-key" not in str(result)
    assert "resolver-secret" not in str(result)


def test_missing_shared_credentials_fail_closed_without_secret_output(monkeypatch):
    monkeypatch.setattr(market_quotes, "alpaca_credentials", lambda: (None, None))

    result = market_universe_quotes({"symbols": ["AAPL"]})

    assert result["feed"] == "unavailable"
    assert result["quotes"][0]["reason"] == "not_configured"
    assert result["data_only"] is True
    assert result["execution_authorized"] is False
    assert result["broker_submission_available"] is False
    assert "API_KEY" not in str(result)
    assert "SECRET" not in str(result)


def test_alpaca_config_rejects_non_paper_or_non_data_endpoints(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("APCA_DATA_BASE_URL", "https://data.alpaca.markets")
    try:
        AlpacaConfig.from_environment()
    except Exception as error:
        assert str(error) == "unexpected_trading_environment"
    else:
        raise AssertionError("live Alpaca trading endpoint was not rejected")
