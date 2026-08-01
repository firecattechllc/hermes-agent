"""Minimal injectable Alpaca HTTP transport; credentials are never rendered."""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass


class AlpacaProviderError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AlpacaConfig:
    key_id: str | None
    secret_key: str | None
    api_base_url: str = "https://paper-api.alpaca.markets"
    data_base_url: str = "https://data.alpaca.markets"

    @classmethod
    def from_environment(cls) -> AlpacaConfig:
        config = cls(
            os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY"),
            os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY"),
            os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/"),
            os.environ.get("APCA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/"),
        )
        if config.api_base_url != "https://paper-api.alpaca.markets":
            raise AlpacaProviderError("unexpected_trading_environment")
        if config.data_base_url != "https://data.alpaca.markets":
            raise AlpacaProviderError("unexpected_market_data_environment")
        return config

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret_key)


Transport = Callable[[str, dict[str, str], float], tuple[int, object]]


def _urllib_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, object]:
    request = urllib.request.Request(url, headers={**headers, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                payload = gzip.decompress(payload)
            return response.status, json.loads(payload)
    except urllib.error.HTTPError as error:
        return error.code, {}
    except (OSError, TimeoutError, ValueError):
        raise AlpacaProviderError("provider_unavailable") from None


class AlpacaHttpClient:
    def __init__(
        self, config: AlpacaConfig, *, transport: Transport = _urllib_transport,
        timeout: float = 10, max_retries: int = 3
    ) -> None:
        self.config, self.transport = config, transport
        self.timeout, self.max_retries = timeout, max_retries

    def _get(self, base: str, path: str, query: dict[str, str] | None = None) -> object:
        if not self.config.configured:
            raise AlpacaProviderError("not_configured")
        url = f"{base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {
            "APCA-API-KEY-ID": self.config.key_id or "",
            "APCA-API-SECRET-KEY": self.config.secret_key or "",
        }
        for attempt in range(self.max_retries):
            status, payload = self.transport(url, headers, self.timeout)
            if status == 200:
                return payload
            if status in {401, 403}:
                raise AlpacaProviderError("authentication_failed")
            if status != 429 and status < 500:
                raise AlpacaProviderError("provider_request_rejected")
            if attempt + 1 < self.max_retries:
                time.sleep(min(0.25 * (2 ** attempt), 1.0))
        raise AlpacaProviderError("retry_exhausted")

    def assets(self) -> list[object]:
        payload = self._get(self.config.api_base_url, "/v2/assets", {"status": "all", "asset_class": "us_equity"})
        if not isinstance(payload, list):
            raise AlpacaProviderError("malformed_asset_response")
        return payload

    def stock_snapshots(
        self,
        symbols: tuple[str, ...],
        *,
        feed: str = "iex",
    ) -> object:
        """Return read-only snapshots for a bounded symbol set."""

        if not symbols:
            return {}

        if len(symbols) > 20:
            raise AlpacaProviderError("snapshot_capacity_rejected")

        if feed not in {"iex", "delayed_sip"}:
            raise AlpacaProviderError("unsupported_snapshot_feed")

        return self._get(
            self.config.data_base_url,
            "/v2/stocks/snapshots",
            {
                "symbols": ",".join(symbols),
                "feed": feed,
                "currency": "USD",
            },
        )

    def delayed_bars(self, symbols: tuple[str, ...], *, start: str, end: str) -> object:
        return self._get(
            self.config.data_base_url, "/v2/stocks/bars",
            {"symbols": ",".join(symbols), "feed": "sip", "timeframe": "1Min",
             "start": start, "end": end, "limit": str(max(len(symbols), 1))},
        )
