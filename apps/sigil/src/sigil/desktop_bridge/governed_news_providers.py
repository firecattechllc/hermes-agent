"""Provider contracts for governed external news collection."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

MAX_PROVIDER_ITEMS = 250
DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProviderBatch:
    provider: str
    items: tuple[dict[str, Any], ...]
    request_url: str
    rate_limit: dict[str, int | None]


class NewsProvider(Protocol):
    name: str

    def collect(self, symbols: Iterable[str]) -> ProviderBatch:
        """Collect raw provider items without granting execution authority."""


FetchJson = Callable[[str, Mapping[str, str], float], tuple[object, Mapping[str, str]]]


def _default_fetch_json(
    url: str,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[object, Mapping[str, str]]:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is HTTPS-validated.
        body = response.read()
        payload = json.loads(body.decode("utf-8"))
        return payload, dict(response.headers.items())


def _header_int(headers: Mapping[str, str], *names: str) -> int | None:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            return None
    return None


class JsonNewsProvider:
    """Configurable HTTPS JSON provider with an injectable network boundary."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        api_key_env: str | None = None,
        api_key_header: str = "Authorization",
        items_field: str = "items",
        query_symbol_field: str = "symbols",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        fetch_json: FetchJson | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("provider name is required")
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("provider endpoint must be an HTTPS URL")
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("provider timeout must be between 0 and 30 seconds")
        self.name = normalized_name
        self.endpoint = endpoint
        self.api_key_env = api_key_env
        self.api_key_header = api_key_header
        self.items_field = items_field
        self.query_symbol_field = query_symbol_field
        self.timeout_seconds = timeout_seconds
        self._fetch_json = fetch_json or _default_fetch_json

    def collect(self, symbols: Iterable[str]) -> ProviderBatch:
        normalized_symbols = sorted(
            {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
        )
        query = urlencode({self.query_symbol_field: ",".join(normalized_symbols)})
        separator = "&" if "?" in self.endpoint else "?"
        request_url = f"{self.endpoint}{separator}{query}" if query else self.endpoint

        headers = {
            "Accept": "application/json",
            "User-Agent": "Sigil-Governed-News/2.6",
        }
        if self.api_key_env:
            secret = os.environ.get(self.api_key_env, "").strip()
            if not secret:
                raise RuntimeError(
                    f"provider credential environment variable {self.api_key_env} is not set"
                )
            headers[self.api_key_header] = secret

        payload, response_headers = self._fetch_json(
            request_url,
            headers,
            self.timeout_seconds,
        )
        if not isinstance(payload, dict):
            raise ValueError("provider response must be an object")
        raw_items = payload.get(self.items_field)
        if not isinstance(raw_items, list):
            raise ValueError(f"provider response field {self.items_field!r} must be a list")
        if len(raw_items) > MAX_PROVIDER_ITEMS:
            raise ValueError("provider response contains too many items")

        items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("provider items must be objects")
            items.append(dict(item))

        rate_limit = {
            "limit": _header_int(response_headers, "x-ratelimit-limit", "ratelimit-limit"),
            "remaining": _header_int(
                response_headers,
                "x-ratelimit-remaining",
                "ratelimit-remaining",
            ),
            "reset": _header_int(response_headers, "x-ratelimit-reset", "ratelimit-reset"),
        }
        return ProviderBatch(
            provider=self.name,
            items=tuple(items),
            request_url=request_url,
            rate_limit=rate_limit,
        )
