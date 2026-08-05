from __future__ import annotations

from dataclasses import replace

from sigil.asset_catalog.catalog import CatalogSnapshot, NormalizedAsset
from sigil.desktop_bridge import governed_news_universe as universe


def asset(symbol: str, *, eligible: bool = True) -> NormalizedAsset:
    return NormalizedAsset(
        asset_id=f"id-{symbol}",
        asset_class="us_equity",
        exchange="NASDAQ",
        symbol=symbol,
        name=symbol,
        status="active",
        tradable=True,
        marginable=True,
        maintenance_margin_requirement=None,
        shortable=True,
        easy_to_borrow=True,
        fractionable=True,
        attributes=(),
        discovered_at="2026-07-30T00:00:00Z",
        source="alpaca",
        source_environment="paper",
        schema_version=1,
        proposal_eligible=eligible,
    )


def snapshot(*symbols: str) -> CatalogSnapshot:
    assets = tuple(asset(symbol) for symbol in symbols)
    return CatalogSnapshot(
        schema_version=1,
        snapshot_id="snapshot",
        provider="alpaca",
        environment="paper",
        requested_filters={},
        discovered_at="2026-07-30T00:00:00Z",
        expires_at="2026-07-31T00:00:00Z",
        asset_count=len(assets),
        active_count=len(assets),
        inactive_count=0,
        tradable_count=len(assets),
        non_tradable_count=0,
        fractionable_count=len(assets),
        proposal_eligible_count=len(assets),
        excluded_count=0,
        exchange_counts={"NASDAQ": len(assets)},
        exclusion_reason_counts={},
        normalized_assets=assets,
        source_response_digest="digest",
        canonical_sha256="sha",
        previous_snapshot_sha256=None,
        discovery_evidence_id="evidence",
    )


def test_eligible_symbols_are_deterministic() -> None:
    class Store:
        def load(self):
            return "ready", snapshot("MSFT", "AAPL", "NVDA"), {}

    class Service:
        store = Store()

    assert universe._eligible_symbols(Service()) == [
        "AAPL",
        "MSFT",
        "NVDA",
    ]


def test_cursor_defaults_to_zero_for_missing_or_invalid_file(tmp_path) -> None:
    assert universe._load_cursor(tmp_path) == 0

    path = tmp_path / universe.CURSOR_FILENAME
    path.write_text("not-json", encoding="utf-8")

    assert universe._load_cursor(tmp_path) == 0


def test_cursor_round_trip_is_paper_only(tmp_path) -> None:
    universe._write_cursor(
        tmp_path,
        cursor=500,
        total_symbols=8000,
    )

    assert universe._load_cursor(tmp_path) == 500

    payload = (tmp_path / universe.CURSOR_FILENAME).read_text(encoding="utf-8")

    assert '"paper_only":true' in payload
    assert '"execution_authority":false' in payload
    assert '"broker_submission_attempted":false' in payload


def test_ineligible_assets_are_excluded() -> None:
    base = snapshot("AAPL", "MSFT")
    altered = replace(
        base,
        normalized_assets=(
            asset("AAPL"),
            asset("MSFT", eligible=False),
        ),
    )

    class Store:
        def load(self):
            return "ready", altered, {}

    class Service:
        store = Store()

    assert universe._eligible_symbols(Service()) == ["AAPL"]
