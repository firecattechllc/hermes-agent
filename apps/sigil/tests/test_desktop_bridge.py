import json

import pytest

from sigil.asset_catalog import AssetCatalogStore, build_snapshot
from sigil.desktop_bridge.providers import load_credentials, provider_snapshot
from sigil.desktop_bridge.runner import backend_status, handle_request


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path / "paper-state"))


def proposal_request() -> dict[str, object]:
    return {
        "command": "explain_proposal",
        "payload": {
            "proposal_id": "PRP-20260725-0042",
            "symbol": "MSFT",
            "side": "BUY",
            "estimated_notional": 25.0,
            "strategy": "Quality momentum v2",
            "evidence_references": [
                {
                    "id": "EV-0042",
                    "label": "Proposal evidence",
                    "source": "sigil",
                }
            ],
        },
    }


def test_backend_status_is_read_only_and_paper_only() -> None:
    status = backend_status()

    assert status["status"] == "ok"
    assert status["mode"] == "local-read-only"
    assert status["environment"] == "paper"
    assert status["simulation"] is True
    assert status["execution_authorized"] is False
    assert status["broker_submission_available"] is False
    assert status["supported_commands"] == [
        "health",
        "explain_proposal",
        "runtime_snapshot",
        "control_paper_cycle",
        "control_paper_authorization",
        "reset_paper_runtime",
        "provider_snapshot",
        "market_universe_status",
        "market_universe_search",
        "alpaca_market_data_status",
        "control_alpaca_market_data",
        "asset_catalog_status",
        "asset_catalog_refresh",
        "asset_catalog_snapshot",
        "asset_catalog_statistics",
        "asset_catalog_sample",
        "asset_catalog_exclusions",
        "research_universe_status",
        "research_universe_advance",
    ]


def test_health_request_returns_status() -> None:
    response = handle_request({"command": "health"})

    assert response["ok"] is True
    assert response["result"]["mode"] == "local-read-only"


def test_alpaca_market_data_projection_is_data_only(monkeypatch) -> None:
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    response = handle_request({"command": "alpaca_market_data_status"})
    assert response["ok"] is True
    result = response["result"]
    assert result["delayed_sip"]["classification"] == "15-minute delayed SIP"
    assert result["live_iex"]["classification"] == "live partial-market IEX"
    assert result["live_iex"]["maximum_symbol_count"] == 30
    assert result["safety"]["data_only_mode"] is True
    assert result["safety"]["execution_authorized"] is False
    assert result["safety"]["live_trading_enabled"] is False


def test_explain_proposal_returns_governed_result() -> None:
    response = handle_request(proposal_request())

    assert response["ok"] is True

    result = response["result"]
    assert result["kind"] == "proposal-explanation"
    assert result["model_route"] == "python-bridge-v1"
    assert result["source"] == "local"
    assert result["execution_authorized"] is False
    assert result["broker_submission_available"] is False
    assert result["evidence_references"][0]["id"] == "EV-0042"
    assert "$25.00" in result["explanation"]


def test_explain_proposal_rejects_invalid_side() -> None:
    request = proposal_request()
    payload = request["payload"]
    assert isinstance(payload, dict)
    payload["side"] = "SHORT"

    response = handle_request(request)

    assert response == {
        "ok": False,
        "error": "invalid_payload",
        "message": "side must be BUY or SELL.",
    }


def test_explain_proposal_requires_evidence_list() -> None:
    request = proposal_request()
    payload = request["payload"]
    assert isinstance(payload, dict)
    payload["evidence_references"] = "EV-0042"

    response = handle_request(request)

    assert response["ok"] is False
    assert response["error"] == "invalid_payload"


def test_unknown_command_fails_closed() -> None:
    response = handle_request({"command": "execute"})

    assert response == {
        "ok": False,
        "error": "unsupported_command",
        "message": "Only allow-listed local paper commands are available.",
    }


def test_market_universe_projection_and_bounded_search_are_read_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_directory = tmp_path / "universe-state"
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(state_directory))
    snapshot = build_snapshot(
        [
            {
                "id": "asset-aapl",
                "class": "us_equity",
                "exchange": "NASDAQ",
                "symbol": "AAPL",
                "name": "Apple",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            }
        ],
        discovered_at="2026-07-28T18:00:00Z",
    )
    AssetCatalogStore(state_directory).write(
        snapshot,
        fetched_at="2026-07-28T18:00:00Z",
        validated_at="2026-07-28T18:00:00Z",
        freshness_seconds=604_800,
        stale_after_seconds=1_209_600,
    )
    status = handle_request({"command": "market_universe_status"})
    assert status["ok"] is True
    assert status["result"]["master_count"] == 1
    assert status["result"]["catalog_source"] == "Alpaca Paper Trading Assets API"
    assert status["result"]["target_capacity_validated"] is True
    assert status["result"]["broker_submission_available"] is False

    search = handle_request(
        {
            "command": "market_universe_search",
            "payload": {"query": "apple", "universe": "proposal_eligible", "limit": 10},
        }
    )
    assert search["ok"] is True
    assert search["result"]["total"] == 1
    assert search["result"]["results"][0]["symbol"] == "AAPL"
    denied = handle_request(
        {"command": "market_universe_search", "payload": {"limit": 101}}
    )
    assert denied["error"] == "invalid_universe_query"


def test_non_object_request_fails_closed() -> None:
    response = handle_request(["health"])

    assert response["ok"] is False
    assert response["error"] == "invalid_request"


class ProviderResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.headers = {"Content-Type": "application/json"}

    def getcode(self) -> int:
        return 200

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.payload).encode()


def test_provider_snapshot_is_read_only_masked_and_secret_free(tmp_path) -> None:
    credential_path = tmp_path / "providers.txt"
    credential_path.write_text(
        "\n".join(
            (
                "SIGIL_ALPACA_API_KEY_ID=alpaca-key",
                "SIGIL_ALPACA_API_SECRET_KEY=alpaca-secret",
                "SIGIL_PUBLIC_API_SECRET=public-secret",
                "SIGIL_BROKER_SUBMISSION_ENABLED=false",
            )
        )
    )
    credential_path.chmod(0o600)
    requests = []

    def opener(request, _timeout):  # type: ignore[no-untyped-def]
        requests.append(request)
        if request.full_url.endswith("/personal/access-tokens"):
            return ProviderResponse({"accessToken": "runtime-token"})
        if request.full_url.endswith("/trading/account"):
            return ProviderResponse({"accounts": [{"accountId": "paper-account-1234"}]})
        if "/portfolio/v2" in request.full_url:
            return ProviderResponse(
                {
                    "buyingPower": {"cashOnlyBuyingPower": "1250.00"},
                    "equity": "1500.00",
                    "positions": [
                        {"instrument": {"symbol": "AAPL"}, "quantity": "2"}
                    ],
                }
            )
        if "/stocks/snapshots" in request.full_url:
            return ProviderResponse(
                {
                    "snapshots": {
                        symbol: {
                            "latestTrade": {
                                "p": 201.25,
                                "t": "2026-07-26T14:30:00Z",
                            },
                            "dailyBar": {"c": 202.0},
                            "prevDailyBar": {"c": 200.0},
                        }
                        for symbol in (
                            "AAPL",
                            "MSFT",
                            "NVDA",
                            "AMZN",
                            "GOOGL",
                            "META",
                            "JPM",
                            "XOM",
                            "UNH",
                            "COST",
                            "CAT",
                            "NEE",
                        )
                    }
                }
            )
        raise AssertionError(f"unexpected provider request: {request.full_url}")

    result = provider_snapshot(opener=opener, path=credential_path)
    serialized = json.dumps(result)

    assert result["alpaca"]["status"] == "degraded"
    universe = result["alpaca"]["universe"]
    assert universe["scope"] == "Catalog unavailable"
    assert universe["total"] == 0
    assert universe["available"] == 0
    assert universe["unavailable"] == 0
    assert universe["whole_market_coverage"] is False
    assert universe["catalog_access"] == "missing"
    assert result["alpaca"]["symbols"] == []
    assert result["public"]["status"] == "connected"
    assert result["public"]["accounts"][0]["masked_account_id"] == "•••• 1234"
    assert result["broker_submission_available"] is False
    assert result["credentials_exposed"] is False
    assert "alpaca-secret" not in serialized
    assert "public-secret" not in serialized
    assert all(request.method in {"GET", "POST"} for request in requests)
    assert not any("data.alpaca.markets" in request.full_url for request in requests)
    assert not any("/order" in request.full_url for request in requests)
    assert not any("transfer" in request.full_url for request in requests)


def test_provider_credential_loader_rejects_unsafe_permissions(tmp_path) -> None:
    credential_path = tmp_path / "providers.txt"
    credential_path.write_text("SIGIL_PUBLIC_API_SECRET=secret")
    credential_path.chmod(0o644)

    try:
        load_credentials(credential_path)
    except RuntimeError as error:
        assert "permissions are unsafe" in str(error)
    else:
        raise AssertionError("unsafe credential permissions must fail closed")
