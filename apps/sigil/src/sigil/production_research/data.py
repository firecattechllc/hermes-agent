"""Bounded GET-only Alpaca production evidence acquisition."""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .models import EvidenceStatus, MarketBar, MarketEvidence, decimal

ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"
MAX_BATCH_SIZE = 25
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
Transport = Callable[[str, dict[str, str], float], tuple[int, object]]


class ProductionDataError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, object]:
    request = urllib.request.Request(
        url,
        headers={**headers, "Accept": "application/json", "Accept-Encoding": "gzip"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProductionDataError("response_too_large")
            if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                raw = gzip.decompress(raw)
            return int(response.status), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        return int(error.code), {}
    except (OSError, TimeoutError, ValueError):
        raise ProductionDataError("provider_error") from None


class AlpacaProductionDataClient:
    """Three bounded reads supply quotes, trades, and adjusted daily bars."""

    base_url = ALPACA_DATA_BASE_URL

    def __init__(
        self,
        key_id: str | None,
        secret_key: str | None,
        *,
        transport: Transport = _transport,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self.key_id = key_id
        self.secret_key = secret_key
        self.transport = transport
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_wait = retry_wait

    @classmethod
    def from_environment(cls) -> AlpacaProductionDataClient:
        return cls(
            os.environ.get("APCA_API_KEY_ID")
            or os.environ.get("SIGIL_ALPACA_API_KEY_ID")
            or os.environ.get("ALPACA_API_KEY"),
            os.environ.get("APCA_API_SECRET_KEY")
            or os.environ.get("SIGIL_ALPACA_API_SECRET_KEY")
            or os.environ.get("ALPACA_SECRET_KEY"),
        )

    def _get(self, path: str, query: dict[str, str]) -> object:
        if not self.key_id or not self.secret_key:
            raise ProductionDataError("credentials_unavailable")
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        if not url.startswith(f"{ALPACA_DATA_BASE_URL}/v2/stocks/"):
            raise ProductionDataError("unsupported_endpoint")
        headers = {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }
        for attempt in range(self.max_retries + 1):
            status, payload = self.transport(url, headers, self.timeout)
            if status == 200:
                return payload
            if status in {401, 403}:
                raise ProductionDataError("authentication_failed")
            if status == 429:
                code = "rate_limited"
            elif status >= 500:
                code = "provider_error"
            else:
                raise ProductionDataError("provider_request_rejected")
            if attempt < self.max_retries:
                self.retry_wait(DecimalBackoff.seconds(attempt))
                continue
            raise ProductionDataError(code)
        raise ProductionDataError("provider_error")

    def collect_batch(
        self, symbols: tuple[str, ...], *, now: datetime | None = None
    ) -> tuple[MarketEvidence, ...]:
        ordered = tuple(sorted(set(symbols)))
        if not ordered or len(ordered) > MAX_BATCH_SIZE:
            raise ValueError("production research batch must contain 1 to 25 symbols")
        observed_now = now or datetime.now(UTC)
        completed_bar_cutoff = observed_now.replace(hour=0, minute=0, second=0, microsecond=0)
        joined = ",".join(ordered)
        quotes_payload = self._get("/v2/stocks/quotes/latest", {"symbols": joined, "feed": "iex"})
        trades_payload = self._get("/v2/stocks/trades/latest", {"symbols": joined, "feed": "iex"})
        bars_payload = self._get(
            "/v2/stocks/bars",
            {
                "symbols": joined,
                "timeframe": "1Day",
                "limit": str(60 * len(ordered)),
                "adjustment": "all",
                "feed": "iex",
                "sort": "asc",
                "start": (completed_bar_cutoff - timedelta(days=120))
                .isoformat()
                .replace("+00:00", "Z"),
                "end": completed_bar_cutoff.isoformat().replace("+00:00", "Z"),
            },
        )
        if not all(
            isinstance(payload, dict) for payload in (quotes_payload, trades_payload, bars_payload)
        ):
            raise ProductionDataError("malformed")
        quotes = quotes_payload.get("quotes", {})
        trades = trades_payload.get("trades", {})
        bars = bars_payload.get("bars", {})
        received = observed_now.isoformat().replace("+00:00", "Z")
        return tuple(
            self._normalize_symbol(
                symbol,
                quotes.get(symbol) if isinstance(quotes, dict) else None,
                trades.get(symbol) if isinstance(trades, dict) else None,
                bars.get(symbol) if isinstance(bars, dict) else None,
                received,
            )
            for symbol in ordered
        )

    @staticmethod
    def _normalize_symbol(
        symbol: str,
        quote: object,
        trade: object,
        bars: object,
        received: str,
    ) -> MarketEvidence:
        missing: list[str] = []
        status = EvidenceStatus.COMPLETE
        if not isinstance(quote, dict):
            quote = {}
            missing.append("missing_quote")
        if not isinstance(trade, dict):
            trade = {}
            missing.append("missing_trade")
        if not isinstance(bars, list):
            bars = []
            missing.append("missing_bars")
        try:
            normalized_bars = tuple(
                MarketBar(
                    timestamp=item["t"],
                    open=decimal(item["o"], "open"),
                    high=decimal(item["h"], "high"),
                    low=decimal(item["l"], "low"),
                    close=decimal(item["c"], "close"),
                    volume=decimal(item["v"], "volume", nonnegative=True),
                )
                for item in bars
                if isinstance(item, dict)
            )
            bid = decimal(quote["bp"], "bid") if quote.get("bp") is not None else None
            ask = decimal(quote["ap"], "ask") if quote.get("ap") is not None else None
            bid_size = (
                decimal(quote["bs"], "bid size", nonnegative=True)
                if quote.get("bs") is not None
                else None
            )
            ask_size = (
                decimal(quote["as"], "ask size", nonnegative=True)
                if quote.get("as") is not None
                else None
            )
            last_trade = decimal(trade["p"], "last trade") if trade.get("p") is not None else None
        except (KeyError, ValueError):
            status = EvidenceStatus.MALFORMED
            normalized_bars = ()
            bid = ask = bid_size = ask_size = last_trade = None
            missing.append("malformed_provider_payload")
        if bid is not None and bid <= 0:
            bid = None
            missing.append("invalid_bid")
        if ask is not None and ask <= 0:
            ask = None
            missing.append("invalid_ask")
        if last_trade is not None and last_trade <= 0:
            last_trade = None
            missing.append("invalid_last_trade")
        if bid is None or ask is None:
            missing.append("invalid_quote")
        elif ask < bid:
            status = EvidenceStatus.CONTRADICTORY
            missing.append("crossed_quote")
        if not normalized_bars:
            missing.append("invalid_bars")
        if missing and status is EvidenceStatus.COMPLETE:
            status = EvidenceStatus.INCOMPLETE
        observed = quote.get("t") if isinstance(quote.get("t"), str) else received
        return MarketEvidence(
            symbol=symbol,
            observed_at=observed,
            received_at=received,
            source="alpaca_market_data",
            feed="iex",
            adjustment="all",
            status=status,
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
            last_trade=last_trade,
            last_trade_at=trade.get("t") if isinstance(trade.get("t"), str) else None,
            daily_bars=normalized_bars,
            missing_classifications=tuple(sorted(set(missing))),
        )


class DecimalBackoff:
    """Integer-scaled deterministic retry schedule."""

    @staticmethod
    def seconds(attempt: int) -> float:
        return (25 * (2**attempt)) / 100
