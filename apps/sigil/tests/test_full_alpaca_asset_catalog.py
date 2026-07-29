from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from sigil.asset_catalog import (
    AlpacaAssetCatalogClient,
    AssetCatalogError,
    AssetCatalogService,
    AssetCatalogStore,
    ResearchUniverseScheduler,
    build_snapshot,
)
from sigil.desktop_bridge import runtime
from sigil.desktop_bridge.runner import handle_request

NOW = datetime(2026, 7, 28, 18, tzinfo=timezone.utc)  # noqa: UP017
STAMP = "2026-07-28T18:00:00Z"


def asset(
    symbol: str = "AAPL",
    *,
    asset_id: str | None = None,
    exchange: str = "NASDAQ",
    tradable: bool = True,
    fractionable: bool = True,
    status: str = "active",
    **changes: object,
) -> dict[str, object]:
    return {
        "id": asset_id or f"id-{symbol}",
        "class": "us_equity",
        "exchange": exchange,
        "symbol": symbol,
        "name": f"{symbol} Incorporated",
        "status": status,
        "tradable": tradable,
        "marginable": True,
        "maintenance_margin_requirement": 30,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": fractionable,
        "attributes": ["fractional_eh_enabled"],
        **changes,
    }


def client_transport(
    requests: list[tuple[str, dict[str, str]]],
    *,
    assets: list[object] | None = None,
):
    def transport(
        url: str, headers: dict[str, str], _timeout: float
    ) -> tuple[int, object]:
        requests.append((url, headers))
        if url.endswith("/v2/account"):
            return 200, {"id": "paper-account"}
        return 200, assets if assets is not None else [asset()]

    return transport


def test_probe_uses_paper_trading_api_and_exact_asset_filters() -> None:
    requests: list[tuple[str, dict[str, str]]] = []
    client = AlpacaAssetCatalogClient(
        "key",
        "secret",
        transport=client_transport(requests),
    )

    result = client.capability_probe()

    assert result["failure_code"] is None
    assert result["environment"] == "paper"
    assert result["broker_submission"] is False
    assert requests[0][0] == "https://paper-api.alpaca.markets/v2/account"
    assert requests[1][0] == (
        "https://paper-api.alpaca.markets/v2/assets"
        "?status=active&asset_class=us_equity"
    )
    assert all("data.alpaca.markets" not in url for url, _headers in requests)


def test_credentials_are_sent_only_in_approved_headers() -> None:
    requests: list[tuple[str, dict[str, str]]] = []
    client = AlpacaAssetCatalogClient(
        "key-value",
        "secret-value",
        transport=client_transport(requests),
    )

    client.assets()

    url, headers = requests[0]
    assert "key-value" not in url
    assert "secret-value" not in url
    assert headers == {
        "APCA-API-KEY-ID": "key-value",
        "APCA-API-SECRET-KEY": "secret-value",
    }


def test_probe_distinguishes_account_and_catalog_authorization() -> None:
    def denied_account(
        _url: str, _headers: dict[str, str], _timeout: float
    ) -> tuple[int, object]:
        return 401, {}

    account = AlpacaAssetCatalogClient(
        "key", "secret", transport=denied_account
    ).capability_probe()
    assert account["failure_code"] == "trading_api_unauthorized"
    assert account["asset_catalog"]["authorized"] is False

    def denied_assets(
        url: str, _headers: dict[str, str], _timeout: float
    ) -> tuple[int, object]:
        return (200, {"id": "paper"}) if url.endswith("/account") else (403, {})

    catalog = AlpacaAssetCatalogClient(
        "key", "secret", transport=denied_assets
    ).capability_probe()
    assert catalog["trading_api"]["authenticated"] is True
    assert catalog["failure_code"] == "asset_catalog_unauthorized"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, "wrong_environment_credentials"), (429, "rate_limited")],
)
def test_probe_classifies_provider_failures(
    status: int, expected: str
) -> None:
    def response(
        _url: str, _headers: dict[str, str], _timeout: float
    ) -> tuple[int, object]:
        return status, {}

    result = AlpacaAssetCatalogClient(
        "key", "secret", transport=response
    ).capability_probe()
    assert result["failure_code"] == expected
    assert "key" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_missing_credentials_are_sanitized() -> None:
    result = AlpacaAssetCatalogClient(None, None).capability_probe()
    assert result["failure_code"] == "credentials_missing"
    assert result["broker_submission"] is False


def test_normalization_sorting_and_hashing_are_deterministic() -> None:
    rows = [asset("MSFT"), asset("AAPL")]
    first = build_snapshot(rows, discovered_at=STAMP)
    second = build_snapshot(reversed(rows), discovered_at=STAMP)

    assert [item.symbol for item in first.normalized_assets] == [
        "AAPL",
        "MSFT",
    ]
    assert first.snapshot_id == second.snapshot_id
    assert first.canonical_sha256 == second.canonical_sha256
    assert first.asset_count == 2
    assert first.active_count == 2
    assert first.tradable_count == 2
    assert first.fractionable_count == 2


def test_optional_asset_fields_are_preserved_deterministically() -> None:
    snapshot = build_snapshot(
        [
            asset(
                min_order_size="0.01",
                min_trade_increment="0.001",
                price_increment="0.0001",
            )
        ],
        discovered_at=STAMP,
    )
    normalized = snapshot.normalized_assets[0]
    assert normalized.min_order_size == "0.01"
    assert normalized.min_trade_increment == "0.001"
    assert normalized.price_increment == "0.0001"


def test_proposal_eligibility_is_separate_from_discovery() -> None:
    snapshot = build_snapshot(
        [
            asset("AAPL"),
            asset("HALT", tradable=False),
            asset("OTCX", exchange="OTC"),
            asset("ODD", exchange="UNKNOWN"),
        ],
        discovered_at=STAMP,
    )
    assert snapshot.asset_count == 4
    assert snapshot.proposal_eligible_count == 1
    assert snapshot.excluded_count == 3
    reasons = snapshot.exclusion_reason_counts
    assert reasons["not_tradable"] == 1
    assert reasons["otc_feed_unavailable"] == 1
    assert reasons["unsupported_exchange"] == 1


def test_invalid_records_are_excluded_with_schema_diagnostics() -> None:
    snapshot = build_snapshot(
        [
            asset(),
            {"class": "us_equity", "symbol": "NOID", "status": "active"},
            asset("CRYPTO", **{"class": "crypto"}),
        ],
        discovered_at=STAMP,
    )
    assert snapshot.asset_count == 1
    assert snapshot.exclusion_reason_counts["missing_required_fields"] == 1
    assert snapshot.exclusion_reason_counts["unsupported_asset_class"] == 1


def test_identical_duplicates_deduplicate_and_conflicts_fail_closed() -> None:
    row = asset()
    deduplicated = build_snapshot([row, dict(row)], discovered_at=STAMP)
    assert deduplicated.asset_count == 1

    with pytest.raises(AssetCatalogError, match="malformed_response"):
        build_snapshot(
            [row, {**row, "name": "Conflicting name"}],
            discovered_at=STAMP,
        )
    with pytest.raises(AssetCatalogError, match="malformed_response"):
        build_snapshot(
            [asset("AAPL", asset_id="one"), asset("AAPL", asset_id="two")],
            discovered_at=STAMP,
        )


def write_cache(
    store: AssetCatalogStore,
    *,
    fetched_at: str = STAMP,
    freshness: int = 3600,
    stale_after: int = 7200,
) -> None:
    snapshot = build_snapshot([asset()], discovered_at=STAMP)
    store.write(
        snapshot,
        fetched_at=fetched_at,
        validated_at=fetched_at,
        freshness_seconds=freshness,
        stale_after_seconds=stale_after,
    )


def test_fresh_stale_and_expired_cache_states(tmp_path) -> None:
    store = AssetCatalogStore(tmp_path)
    write_cache(store)

    assert store.load(now=NOW + timedelta(minutes=30))[0] == "fresh"
    assert store.load(now=NOW + timedelta(minutes=90))[0] == "stale_usable"
    assert store.load(now=NOW + timedelta(hours=3))[0] == "expired"


def test_cache_write_is_atomic_and_integrity_checked(tmp_path) -> None:
    store = AssetCatalogStore(tmp_path)
    write_cache(store)

    state, snapshot, metadata = store.load(now=NOW)
    assert state == "fresh"
    assert snapshot is not None
    assert metadata["age_seconds"] == 0
    assert store.path.name == "alpaca-us-equity-v1.json"
    assert not list(store.path.parent.glob(".*.tmp"))

    content = store.path.read_text(encoding="utf-8")
    store.path.write_text(content.replace("AAPL", "EVIL"), encoding="utf-8")
    assert store.load(now=NOW)[0] == "corrupt"


def test_environment_mismatch_is_rejected(tmp_path) -> None:
    store = AssetCatalogStore(tmp_path)
    write_cache(store)
    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    core = {key: value for key, value in envelope.items() if key != "cache_sha256"}
    core["environment"] = "live"
    import hashlib

    core["cache_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in core.items() if key != "cache_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    store.path.write_text(json.dumps(core), encoding="utf-8")
    assert store.load(now=NOW)[0] == "environment_mismatch"


def test_empty_or_failed_refresh_preserves_valid_cache(tmp_path) -> None:
    store = AssetCatalogStore(tmp_path)
    write_cache(store, freshness=604_800, stale_after=1_209_600)
    previous = store.path.read_bytes()

    empty_requests: list[tuple[str, dict[str, str]]] = []
    client = AlpacaAssetCatalogClient(
        "key",
        "secret",
        transport=client_transport(empty_requests, assets=[]),
    )
    service = AssetCatalogService(tmp_path, client=client)
    result = service.refresh()

    assert result["failure_code"] == "cache_available_remote_failed"
    assert store.path.read_bytes() == previous
    assert result["broker_submission"] is False


def test_large_catalog_and_bounded_snapshot(tmp_path) -> None:
    rows = [asset(f"S{index:05d}") for index in range(4_000)]
    requests: list[tuple[str, dict[str, str]]] = []
    service = AssetCatalogService(
        tmp_path,
        client=AlpacaAssetCatalogClient(
            "key",
            "secret",
            transport=client_transport(requests, assets=rows),
        ),
    )

    refreshed = service.refresh()
    bounded = service.snapshot(offset=100, limit=500)

    assert refreshed["statistics"]["total_assets_discovered"] == 4_000
    assert len(bounded["assets"]) == 100
    assert bounded["has_more"] is True
    assert bounded["broker_submission"] is False


def test_research_scheduler_is_deterministic_and_restart_safe(tmp_path) -> None:
    snapshot = build_snapshot(
        [asset("MSFT"), asset("AAPL"), asset("NVDA")],
        discovered_at=STAMP,
    )
    first = ResearchUniverseScheduler(tmp_path, batch_size=2)
    batch_one = first.next_batch(snapshot)
    second = ResearchUniverseScheduler(tmp_path, batch_size=2)
    batch_two = second.next_batch(snapshot)

    assert batch_one["symbols"] == ["AAPL", "MSFT"]
    assert batch_two["symbols"] == ["NVDA"]
    assert batch_two["next_cursor"] == 3
    assert batch_two["research_coverage_percent"] == 100.0
    assert batch_two["broker_submission"] is False


def test_live_endpoint_and_submission_capability_do_not_exist() -> None:
    source = (
        __import__("inspect")
        .getsource(AlpacaAssetCatalogClient)
    )
    assert "api.alpaca.markets" not in source.replace(
        "paper-api.alpaca.markets", ""
    )
    assert "submit" not in source.casefold()


def test_bridge_commands_are_bounded_and_paper_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_directory = tmp_path / "bridge-state"
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(state_directory))
    store = AssetCatalogStore(state_directory)
    write_cache(store, freshness=604_800, stale_after=1_209_600)

    for command in (
        "asset_catalog_status",
        "asset_catalog_statistics",
        "asset_catalog_sample",
        "asset_catalog_exclusions",
        "research_universe_status",
    ):
        response = handle_request({"command": command})
        assert response["ok"] is True
        assert response["result"]["environment"] == "paper"
        assert response["result"]["broker_submission"] is False
    sample = handle_request({"command": "asset_catalog_sample"})
    assert len(sample["result"]["assets"]) <= 12


def test_production_paper_cycle_traverses_catalog_without_order_submission(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_directory = tmp_path / "runtime-state"
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(state_directory))
    monkeypatch.delenv("SIGIL_ASSET_CATALOG_MODE", raising=False)
    snapshot = build_snapshot(
        [asset("MSFT"), asset("AAPL"), asset("NVDA")],
        discovered_at=STAMP,
        freshness_seconds=604_800,
    )
    AssetCatalogStore(state_directory).write(
        snapshot,
        fetched_at=STAMP,
        validated_at=STAMP,
        freshness_seconds=604_800,
        stale_after_seconds=1_209_600,
    )

    runtime.control_paper_cycle("start", now=NOW)
    started = runtime.runtime_snapshot(now=NOW)

    assert started["automation"]["state"] == "running"
    assert started["automation"]["cycle_count"] == 1
    assert started["proposals"] == []
    assert started["executions"] == []
    event = next(
        item
        for item in started["audit"]
        if item["status"] == "catalog_research_batch_completed"
    )
    assert event["details"]["symbols_examined"] == ["AAPL", "MSFT", "NVDA"]
    assert event["details"]["broker_submission_attempted"] is False
