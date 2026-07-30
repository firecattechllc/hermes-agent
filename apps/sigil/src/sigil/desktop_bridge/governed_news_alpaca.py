"""Alpaca Market Data News adapter for governed Sigil research."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .governed_news_providers import (
    DEFAULT_TIMEOUT_SECONDS,
    FetchJson,
    ProviderBatch,
    _header_int,
)

ALPACA_NEWS_ENDPOINT = "https://data.alpaca.markets/v1beta1/news"
ALPACA_KEY_ENV = "APCA_API_KEY_ID"
ALPACA_SECRET_ENV = "APCA_API_SECRET_KEY"
ALPACA_ENABLED_ENV = "SIGIL_ALPACA_NEWS_ENABLED"
MAX_ALPACA_SYMBOLS = 50
MAX_ALPACA_LIMIT = 50


def _default_fetch_json(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[object, Mapping[str, str]]:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint.
        payload = json.loads(response.read().decode("utf-8"))
        return payload, dict(response.headers.items())


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bounded_symbols(symbols: Iterable[str]) -> list[str]:
    normalized = sorted(
        {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    )
    if not normalized:
        raise ValueError("at least one Alpaca news symbol is required")
    if len(normalized) > MAX_ALPACA_SYMBOLS:
        raise ValueError("too many Alpaca news symbols requested")
    return normalized


class AlpacaNewsProvider:
    """Collect and map Alpaca News API articles into Sigil's governed schema."""

    name = "Alpaca News"

    def __init__(
        self,
        *,
        limit: int = 10,
        lookback_minutes: int = 60,
        include_content: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        fetch_json: FetchJson | None = None,
    ) -> None:
        if limit < 1 or limit > MAX_ALPACA_LIMIT:
            raise ValueError("Alpaca news limit must be between 1 and 50")
        if lookback_minutes < 1 or lookback_minutes > 24 * 60:
            raise ValueError("Alpaca news lookback must be between 1 and 1440 minutes")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("provider timeout must be between 0 and 30 seconds")
        self.limit = limit
        self.lookback_minutes = lookback_minutes
        self.include_content = include_content
        self.timeout_seconds = timeout_seconds
        self._fetch_json = fetch_json or _default_fetch_json

    @staticmethod
    def _credentials() -> tuple[str, str]:
        key = os.environ.get(ALPACA_KEY_ENV, "").strip()
        secret = os.environ.get(ALPACA_SECRET_ENV, "").strip()
        if not key or not secret:
            raise RuntimeError(
                f"{ALPACA_KEY_ENV} and {ALPACA_SECRET_ENV} must both be set"
            )
        return key, secret

    @staticmethod
    def _map_article(article: object) -> dict[str, Any]:
        if not isinstance(article, dict):
            raise ValueError("Alpaca news article must be an object")
        headline = str(article.get("headline", "")).strip()
        source = str(article.get("source", "Alpaca News")).strip() or "Alpaca News"
        source_url = str(article.get("url", "")).strip()
        published_at = str(
            article.get("created_at") or article.get("updated_at") or ""
        ).strip()
        raw_symbols = article.get("symbols", [])
        if not isinstance(raw_symbols, list):
            raise ValueError("Alpaca article symbols must be a list")
        summary = str(article.get("summary", "")).strip()
        if not summary:
            summary = str(article.get("content", "")).strip()[:2_000]

        return {
            "headline": headline,
            "summary": summary,
            "source": source,
            "source_url": source_url,
            "published_at": published_at,
            "symbols": raw_symbols,
            "sentiment": "unknown",
            "confidence": "0",
            "provider_metadata": {
                "provider": "alpaca",
                "article_id": article.get("id"),
                "author": article.get("author"),
                "updated_at": article.get("updated_at"),
            },
        }

    def collect(self, symbols: Iterable[str]) -> ProviderBatch:
        normalized_symbols = _bounded_symbols(symbols)
        key, secret = self._credentials()
        end = datetime.now(UTC)
        start = end - timedelta(minutes=self.lookback_minutes)
        query = urlencode(
            {
                "symbols": ",".join(normalized_symbols),
                "start": _utc_timestamp(start),
                "end": _utc_timestamp(end),
                "sort": "desc",
                "limit": str(self.limit),
                "include_content": str(self.include_content).lower(),
            }
        )
        request_url = f"{ALPACA_NEWS_ENDPOINT}?{query}"
        payload, response_headers = self._fetch_json(
            request_url,
            {
                "Accept": "application/json",
                "User-Agent": "Sigil-Governed-News/2.7",
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
            },
            self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            raise ValueError("Alpaca News response must be an object")
        raw_news = payload.get("news")
        if not isinstance(raw_news, list):
            raise ValueError("Alpaca News response field 'news' must be a list")
        if len(raw_news) > MAX_ALPACA_LIMIT:
            raise ValueError("Alpaca News returned too many articles")

        return ProviderBatch(
            provider=self.name,
            items=tuple(self._map_article(article) for article in raw_news),
            request_url=request_url,
            rate_limit={
                "limit": _header_int(
                    response_headers, "x-ratelimit-limit", "ratelimit-limit"
                ),
                "remaining": _header_int(
                    response_headers,
                    "x-ratelimit-remaining",
                    "ratelimit-remaining",
                ),
                "reset": _header_int(
                    response_headers, "x-ratelimit-reset", "ratelimit-reset"
                ),
            },
        )


def alpaca_news_enabled() -> bool:
    return os.environ.get(ALPACA_ENABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
