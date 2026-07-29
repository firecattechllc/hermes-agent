"""Paper-pinned Alpaca Trading API adapter with sanitized failures."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .models import ALPACA_PAPER_BASE_URL

ALLOWED_METHODS = frozenset({"GET", "POST", "DELETE"})
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
Transport = Callable[
    [str, str, dict[str, str], object | None, float], tuple[int, object]
]


class AlpacaPaperError(RuntimeError):
    """Sanitized stable paper-adapter failure."""


class AlpacaPaperTransportError(AlpacaPaperError):
    """Transport failed with known transmission ambiguity."""

    def __init__(self, code: str, *, ambiguous: bool) -> None:
        super().__init__(code)
        self.code = code
        self.ambiguous = ambiguous


def _transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: object | None,
    timeout: float,
) -> tuple[int, object]:
    payload = (
        json.dumps(body, separators=(",", ":")).encode()
        if body is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            **headers,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Sigil/2.0 governed-autonomous-paper",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AlpacaPaperError("response_too_large")
            return int(response.status), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        try:
            raw = error.read(MAX_RESPONSE_BYTES)
            value = json.loads(raw) if raw else {}
        except (ValueError, OSError):
            value = {}
        return int(error.code), value
    except TimeoutError:
        raise AlpacaPaperTransportError("request_timeout", ambiguous=method != "GET")
    except (urllib.error.URLError, OSError):
        raise AlpacaPaperTransportError(
            "transport_unavailable", ambiguous=method != "GET"
        )


class AlpacaPaperClient:
    """Closed Alpaca paper Trading API surface; no live URL is selectable."""

    base_url = ALPACA_PAPER_BASE_URL

    def __init__(
        self,
        key_id: str | None,
        secret_key: str | None,
        *,
        transport: Transport = _transport,
        timeout: float = 10.0,
    ) -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._transport = transport
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self._key_id or not self._secret_key:
            raise AlpacaPaperError("credentials_missing")
        return {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: object | None = None,
        expected: frozenset[int] = frozenset({200}),
    ) -> object:
        if method not in ALLOWED_METHODS or not path.startswith("/v2/"):
            raise AlpacaPaperError("operation_not_allowed")
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        if not url.startswith(f"{ALPACA_PAPER_BASE_URL}/v2/"):
            raise AlpacaPaperError("paper_endpoint_invariant_failed")
        status, payload = self._transport(
            method, url, self._headers(), body, self._timeout
        )
        if status not in expected:
            if status in {401, 403}:
                raise AlpacaPaperError("paper_authentication_failed")
            if status == 404:
                raise AlpacaPaperError("not_found")
            if status == 422:
                raise AlpacaPaperError("paper_order_rejected")
            if status == 429:
                raise AlpacaPaperError("rate_limited")
            raise AlpacaPaperError("paper_api_failure")
        return payload

    def account(self) -> dict[str, Any]:
        payload = self._request("GET", "/v2/account")
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "ACTIVE"
            or not isinstance(payload.get("id"), str)
        ):
            raise AlpacaPaperError("unexpected_paper_account")
        return payload

    def clock(self) -> dict[str, Any]:
        payload = self._request("GET", "/v2/clock")
        if not isinstance(payload, dict) or not isinstance(payload.get("is_open"), bool):
            raise AlpacaPaperError("malformed_clock")
        return payload

    def positions(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v2/positions")
        if not isinstance(payload, list):
            raise AlpacaPaperError("malformed_positions")
        return [item for item in payload if isinstance(item, dict)]

    def open_orders(self) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "/v2/orders", query={"status": "open", "direction": "asc"}
        )
        if not isinstance(payload, list):
            raise AlpacaPaperError("malformed_orders")
        return [item for item in payload if isinstance(item, dict)]

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        try:
            payload = self._request(
                "GET",
                "/v2/orders:by_client_order_id",
                query={"client_order_id": client_order_id},
            )
        except AlpacaPaperError as error:
            if str(error) == "not_found":
                return None
            raise
        if not isinstance(payload, dict):
            raise AlpacaPaperError("malformed_order")
        return payload

    def submit_order(self, request: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "symbol",
            "notional",
            "qty",
            "side",
            "type",
            "time_in_force",
            "extended_hours",
            "client_order_id",
            "limit_price",
        }
        if set(request) - allowed:
            raise AlpacaPaperError("unallowlisted_order_field")
        if (
            request.get("side") != "buy"
            or request.get("time_in_force") != "day"
            or request.get("extended_hours") is not False
        ):
            raise AlpacaPaperError("unsafe_order_terms")
        payload = self._request(
            "POST", "/v2/orders", body=request, expected=frozenset({200, 201})
        )
        if not isinstance(payload, dict):
            raise AlpacaPaperError("malformed_order_acknowledgement")
        return payload

    def cancel_order(self, provider_order_id: str) -> None:
        self._request(
            "DELETE",
            f"/v2/orders/{urllib.parse.quote(provider_order_id, safe='')}",
            expected=frozenset({200, 204}),
        )

    def close_position(self, symbol: str, *, quantity: str) -> dict[str, Any]:
        payload = self._request(
            "DELETE",
            f"/v2/positions/{urllib.parse.quote(symbol, safe='')}",
            query={"qty": quantity},
            expected=frozenset({200}),
        )
        if not isinstance(payload, dict):
            raise AlpacaPaperError("malformed_close_acknowledgement")
        return payload
