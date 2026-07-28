"""Data-only orchestration and runtime projection for Alpaca feeds."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sigil.market_universe.providers.alpaca import AlpacaAssetCatalogProvider

from .alpaca.client import AlpacaConfig, AlpacaHttpClient, AlpacaProviderError
from .audit import MarketDataAudit
from .policy import MarketDataPolicy


class AlpacaMarketDataRouter:
    def __init__(
        self, *, config: AlpacaConfig | None = None, client: AlpacaHttpClient | None = None,
        policy: MarketDataPolicy | None = None, audit: MarketDataAudit | None = None
    ) -> None:
        self.config = config or AlpacaConfig.from_environment()
        self.policy = policy or MarketDataPolicy()
        self.client = client or AlpacaHttpClient(
            self.config, timeout=self.policy.request_timeout_seconds,
            max_retries=self.policy.max_retries,
        )
        self.audit = audit or MarketDataAudit()
        self.catalog = None
        self.catalog_error: str | None = None

    def refresh_assets(self) -> dict[str, Any]:
        self.audit.record("alpaca_catalog_refresh_started")
        try:
            result = AlpacaAssetCatalogProvider(
                audit=lambda event, details: self.audit.record(event, details)
            ).ingest(self.client.assets())
        except AlpacaProviderError as error:
            self.catalog_error = error.code
            self.audit.record("alpaca_catalog_refresh_failed", {"error": error.code})
            raise
        self.catalog = result
        self.catalog_error = None
        self.audit.record("alpaca_catalog_refresh_completed", {
            "source_count": result.source_count, "accepted_count": len(result.accepted),
            "excluded_count": len(result.excluded), "conflict_count": result.conflict_count,
        })
        return asdict(result)

    def projection(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        catalog = self.catalog
        return {
            "configured": self.config.configured,
            "authenticated": bool(self.config.configured and catalog is not None),
            "provider_state": "ready" if catalog else "not_configured" if not self.config.configured else "degraded",
            "asset_catalog": {
                "refresh_state": "ready" if catalog else "unavailable",
                "source_count": catalog.source_count if catalog else 0,
                "accepted_count": len(catalog.accepted) if catalog else 0,
                "excluded_count": len(catalog.excluded) if catalog else 0,
                "conflict_count": catalog.conflict_count if catalog else 0,
                "generated_at": catalog.observed_at if catalog else None,
                "age_seconds": 0 if catalog else None, "stale": catalog is None,
                "last_error": self.catalog_error,
            },
            "delayed_sip": {
                "enabled": False, "classification": "15-minute delayed SIP",
                "expected_delay_minutes": 15, "latest_scan_started_at": None,
                "latest_scan_completed_at": None, "universe_total": len(catalog.accepted) if catalog else 0,
                "scanned_count": 0, "successful_count": 0, "missing_count": 0,
                "rejected_count": 0, "stale_count": 0, "current_batch": 0,
                "total_batches": 0, "next_scan_at": None, "provider_state": "idle",
            },
            "live_iex": {
                "enabled": False, "classification": "live partial-market IEX",
                "partial_market": True, "connected": False, "active_symbol_count": 0,
                "maximum_symbol_count": self.policy.iex_symbol_limit,
                "subscribed_symbols": [], "last_message_at": None,
                "reconnect_attempts": 0, "stale": True, "provider_state": "idle",
            },
            "safety": {
                "broker_submission_available": False, "execution_authorized": False,
                "live_trading_enabled": False, "data_only_mode": True,
            },
            "generated_at": now.isoformat().replace("+00:00", "Z"),
        }
