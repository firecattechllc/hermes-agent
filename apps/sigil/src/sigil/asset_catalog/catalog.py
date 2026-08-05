"""Governed Alpaca Trading API asset discovery, cache, and research traversal."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PAPER_TRADING_API = "https://paper-api.alpaca.markets"
ASSET_PATH = "/v2/assets"
ACCOUNT_PATH = "/v2/account"
ASSET_FILTERS = {"status": "active", "asset_class": "us_equity"}
SCHEMA_VERSION = 1
ASSET_SCHEMA_VERSION = 1
DEFAULT_FRESHNESS_SECONDS = 86_400
DEFAULT_STALE_SECONDS = 604_800
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$", re.ASCII)
SUPPORTED_IEX_EXCHANGES = frozenset(
    {"AMEX", "ARCA", "BATS", "NASDAQ", "NYSE", "NYSEARCA"}
)
OTC_EXCHANGES = frozenset({"OTC", "OTCB", "OTCQB", "OTCQX", "PINK"})
FAILURE_CODES = frozenset(
    {
        "credentials_missing",
        "credentials_invalid",
        "wrong_environment_credentials",
        "trading_api_unauthorized",
        "asset_catalog_unauthorized",
        "endpoint_unreachable",
        "request_timeout",
        "rate_limited",
        "malformed_response",
        "empty_catalog",
        "cache_available_remote_failed",
        "unknown_failure",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))  # noqa: FURB162


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class AssetCatalogError(RuntimeError):
    """Sanitized catalog failure with a stable governed code."""

    def __init__(self, code: str) -> None:
        safe_code = code if code in FAILURE_CODES else "unknown_failure"
        super().__init__(safe_code)
        self.code = safe_code


@dataclass(frozen=True, slots=True)
class NormalizedAsset:
    asset_id: str
    asset_class: str
    exchange: str
    symbol: str
    name: str
    status: str
    tradable: bool
    marginable: bool
    maintenance_margin_requirement: str | None
    shortable: bool
    easy_to_borrow: bool
    fractionable: bool
    attributes: tuple[str, ...]
    discovered_at: str
    source: str
    source_environment: str
    schema_version: int
    min_order_size: str | None = None
    min_trade_increment: str | None = None
    price_increment: str | None = None
    proposal_eligible: bool = False
    exclusion_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    schema_version: int
    snapshot_id: str
    provider: str
    environment: str
    requested_filters: dict[str, str]
    discovered_at: str
    expires_at: str
    asset_count: int
    active_count: int
    inactive_count: int
    tradable_count: int
    non_tradable_count: int
    fractionable_count: int
    proposal_eligible_count: int
    excluded_count: int
    exchange_counts: dict[str, int]
    exclusion_reason_counts: dict[str, int]
    normalized_assets: tuple[NormalizedAsset, ...]
    source_response_digest: str
    canonical_sha256: str
    previous_snapshot_sha256: str | None
    discovery_evidence_id: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["normalized_assets"] = [
            item.to_dict() for item in self.normalized_assets
        ]
        return payload


Transport = Callable[
    [str, dict[str, str], float], tuple[int, object]
]


def _http_transport(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[int, object]:
    request = urllib.request.Request(
        url,
        headers={
            **headers,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "Sigil/1.9 governed-asset-discovery",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise AssetCatalogError("malformed_response")
            if response.headers.get("Content-Encoding", "").casefold() == "gzip":
                payload = gzip.decompress(payload)
            try:
                return int(response.status), json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise AssetCatalogError("malformed_response") from None
    except urllib.error.HTTPError as error:
        return int(error.code), {}
    except TimeoutError:
        raise AssetCatalogError("request_timeout") from None
    except (urllib.error.URLError, OSError):
        raise AssetCatalogError("endpoint_unreachable") from None


class AlpacaAssetCatalogClient:
    """Read-only client pinned to the Alpaca paper Trading API."""

    def __init__(
        self,
        key_id: str | None,
        secret_key: str | None,
        *,
        transport: Transport = _http_transport,
        timeout: float = 30.0,
    ) -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._transport = transport
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._key_id and self._secret_key)

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise AssetCatalogError("credentials_missing")
        return {
            "APCA-API-KEY-ID": self._key_id or "",
            "APCA-API-SECRET-KEY": self._secret_key or "",
        }

    def _get(
        self, path: str, query: dict[str, str] | None, *, auth_failure: str
    ) -> object:
        url = f"{PAPER_TRADING_API}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        status, payload = self._transport(url, self._headers(), self._timeout)
        if status == 200:
            return payload
        if status in {401, 403}:
            raise AssetCatalogError(auth_failure)
        if status == 429:
            raise AssetCatalogError("rate_limited")
        if status in {404, 405}:
            raise AssetCatalogError("wrong_environment_credentials")
        raise AssetCatalogError("endpoint_unreachable")

    def capability_probe(self) -> dict[str, Any]:
        checked_at = _timestamp(_now())
        result: dict[str, Any] = {
            "provider": "alpaca",
            "environment": "paper",
            "trading_api": {"reachable": False, "authenticated": False},
            "asset_catalog": {
                "reachable": False,
                "authorized": False,
                "response_valid": False,
                "asset_count": 0,
            },
            "market_data": {"feed": "iex", "reachable": None},
            "broker_submission": False,
            "checked_at": checked_at,
            "failure_code": None,
        }
        try:
            account = self._get(
                ACCOUNT_PATH, None, auth_failure="trading_api_unauthorized"
            )
            if not isinstance(account, dict):
                raise AssetCatalogError("malformed_response")
            result["trading_api"] = {
                "reachable": True,
                "authenticated": True,
            }
            assets = self._get(
                ASSET_PATH,
                ASSET_FILTERS,
                auth_failure="asset_catalog_unauthorized",
            )
            result["asset_catalog"]["reachable"] = True
            result["asset_catalog"]["authorized"] = True
            if not isinstance(assets, list):
                raise AssetCatalogError("malformed_response")
            if not assets:
                raise AssetCatalogError("empty_catalog")
            result["asset_catalog"].update(
                {"response_valid": True, "asset_count": len(assets)}
            )
            return {**result, "assets": assets}
        except AssetCatalogError as error:
            result["failure_code"] = error.code
            return result

    def assets(self) -> list[object]:
        payload = self._get(
            ASSET_PATH,
            ASSET_FILTERS,
            auth_failure="asset_catalog_unauthorized",
        )
        if not isinstance(payload, list):
            raise AssetCatalogError("malformed_response")
        if not payload:
            raise AssetCatalogError("empty_catalog")
        return payload


def _optional_decimal(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value)


def _eligibility_reasons(
    *,
    status: str,
    tradable: bool,
    exchange: str,
    symbol_valid: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if status != "active":
        reasons.append("inactive")
    if not tradable:
        reasons.append("not_tradable")
    if not symbol_valid:
        reasons.append("malformed_symbol")
    if exchange in OTC_EXCHANGES:
        reasons.append("otc_feed_unavailable")
    elif exchange not in SUPPORTED_IEX_EXCHANGES:
        reasons.append("unsupported_exchange")
    return tuple(sorted(set(reasons)))


def build_snapshot(
    records: Iterable[object],
    *,
    discovered_at: str,
    previous_snapshot_sha256: str | None = None,
    freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
) -> CatalogSnapshot:
    """Normalize a full response and fail closed on unresolved identity conflicts."""

    rows = tuple(records)
    if not rows:
        raise AssetCatalogError("empty_catalog")
    source_digest = _sha256(
        sorted(
            rows,
            key=lambda row: (
                str(row.get("symbol", "")) if isinstance(row, dict) else "",
                str(row.get("exchange", "")) if isinstance(row, dict) else "",
                str(row.get("id", "")) if isinstance(row, dict) else "",
                _sha256(row),
            ),
        )
    )
    candidates: list[NormalizedAsset] = []
    reason_counts: Counter[str] = Counter()
    by_id: dict[str, str] = {}
    by_symbol: dict[str, str] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            reason_counts["missing_required_fields"] += 1
            continue
        asset_id = raw.get("id")
        symbol_value = raw.get("symbol")
        asset_class = raw.get("class")
        status = raw.get("status")
        if not isinstance(asset_id, str) or not asset_id.strip():
            reason_counts["missing_required_fields"] += 1
            continue
        symbol = symbol_value.strip().upper() if isinstance(symbol_value, str) else ""
        if asset_class != "us_equity":
            reason_counts["unsupported_asset_class"] += 1
            continue
        if status not in {"active", "inactive"}:
            reason_counts["missing_required_fields"] += 1
            continue
        symbol_valid = bool(SYMBOL_PATTERN.fullmatch(symbol))
        exchange = str(raw.get("exchange") or "").strip().upper()
        content_digest = _sha256(raw)
        if asset_id in by_id:
            if by_id[asset_id] != content_digest:
                raise AssetCatalogError("malformed_response")
            continue
        if symbol in by_symbol and by_symbol[symbol] != asset_id:
            raise AssetCatalogError("malformed_response")
        by_id[asset_id] = content_digest
        by_symbol[symbol] = asset_id
        tradable = raw.get("tradable") is True
        reasons = _eligibility_reasons(
            status=status,
            tradable=tradable,
            exchange=exchange,
            symbol_valid=symbol_valid,
        )
        reason_counts.update(reasons)
        attributes_value = raw.get("attributes")
        attributes = (
            tuple(
                sorted(
                    {
                        str(value).strip()
                        for value in attributes_value
                        if isinstance(value, str) and value.strip()
                    }
                )
            )
            if isinstance(attributes_value, list)
            else ()
        )
        candidates.append(
            NormalizedAsset(
                asset_id=asset_id.strip(),
                asset_class="us_equity",
                exchange=exchange,
                symbol=symbol,
                name=" ".join(str(raw.get("name") or symbol).split())[:300],
                status=status,
                tradable=tradable,
                marginable=raw.get("marginable") is True,
                maintenance_margin_requirement=_optional_decimal(
                    raw, "maintenance_margin_requirement"
                ),
                shortable=raw.get("shortable") is True,
                easy_to_borrow=raw.get("easy_to_borrow") is True,
                fractionable=raw.get("fractionable") is True,
                attributes=attributes,
                discovered_at=discovered_at,
                source="alpaca_trading_assets_api",
                source_environment="paper",
                schema_version=ASSET_SCHEMA_VERSION,
                min_order_size=_optional_decimal(raw, "min_order_size"),
                min_trade_increment=_optional_decimal(
                    raw, "min_trade_increment"
                ),
                price_increment=_optional_decimal(raw, "price_increment"),
                proposal_eligible=not reasons,
                exclusion_reasons=reasons,
            )
        )
    assets = tuple(
        sorted(candidates, key=lambda item: (item.symbol, item.exchange, item.asset_id))
    )
    if not assets:
        raise AssetCatalogError("empty_catalog")
    active_count = sum(item.status == "active" for item in assets)
    tradable_count = sum(item.tradable for item in assets)
    eligible_count = sum(item.proposal_eligible for item in assets)
    exchange_counts = dict(sorted(Counter(item.exchange for item in assets).items()))
    expires_at = _timestamp(
        _parse_timestamp(discovered_at) + timedelta(seconds=freshness_seconds)
    )
    core = {
        "schema_version": SCHEMA_VERSION,
        "provider": "alpaca",
        "environment": "paper",
        "requested_filters": dict(sorted(ASSET_FILTERS.items())),
        "discovered_at": discovered_at,
        "expires_at": expires_at,
        "asset_count": len(assets),
        "active_count": active_count,
        "inactive_count": len(assets) - active_count,
        "tradable_count": tradable_count,
        "non_tradable_count": len(assets) - tradable_count,
        "fractionable_count": sum(item.fractionable for item in assets),
        "proposal_eligible_count": eligible_count,
        "excluded_count": len(assets) - eligible_count,
        "exchange_counts": exchange_counts,
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "normalized_assets": [item.to_dict() for item in assets],
        "source_response_digest": source_digest,
        "previous_snapshot_sha256": previous_snapshot_sha256,
    }
    canonical_sha256 = _sha256(core)
    snapshot_id = f"alpaca-paper-{canonical_sha256[:24]}"
    evidence_id = f"SIGIL-ASSET-CATALOG-{canonical_sha256[:20].upper()}"
    return CatalogSnapshot(
        **{
            **core,
            "normalized_assets": assets,
            "snapshot_id": snapshot_id,
            "canonical_sha256": canonical_sha256,
            "discovery_evidence_id": evidence_id,
        }
    )


class AssetCatalogStore:
    """Atomic checksummed paper-only cache."""

    def __init__(self, state_directory: Path) -> None:
        if not state_directory.is_absolute():
            raise ValueError("asset catalog state directory must be absolute")
        self.path = (
            state_directory / "asset-catalog" / "alpaca-us-equity-v1.json"
        )

    def write(
        self,
        snapshot: CatalogSnapshot,
        *,
        fetched_at: str,
        validated_at: str,
        freshness_seconds: int,
        stale_after_seconds: int,
    ) -> None:
        if snapshot.environment != "paper":
            raise AssetCatalogError("wrong_environment_credentials")
        payload = snapshot.to_dict()
        envelope_core = {
            "schema_version": SCHEMA_VERSION,
            "fetched_at": fetched_at,
            "validated_at": validated_at,
            "expires_at": snapshot.expires_at,
            "freshness_seconds": freshness_seconds,
            "stale_after_seconds": stale_after_seconds,
            "source_endpoint": "paper-api.alpaca.markets/v2/assets",
            "provider": "alpaca",
            "environment": "paper",
            "filters": dict(sorted(ASSET_FILTERS.items())),
            "asset_count": snapshot.asset_count,
            "canonical_hash": snapshot.canonical_sha256,
            "previous_hash": snapshot.previous_snapshot_sha256,
            "payload": payload,
            "validation_result": "verified",
        }
        envelope = {
            **envelope_core,
            "cache_sha256": _sha256(envelope_core),
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".alpaca-us-equity-v1.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(_canonical(envelope))
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(
        self, *, now: datetime | None = None
    ) -> tuple[str, CatalogSnapshot | None, dict[str, Any]]:
        current = now or _now()
        if not self.path.exists():
            return "missing", None, {}
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            cache_hash = envelope.pop("cache_sha256")
            if cache_hash != _sha256(envelope):
                return "corrupt", None, {}
            if envelope.get("schema_version") != SCHEMA_VERSION:
                return "schema_incompatible", None, {}
            if envelope.get("environment") != "paper":
                return "environment_mismatch", None, {}
            payload = envelope["payload"]
            assets = tuple(
                NormalizedAsset(
                    **{
                        **item,
                        "attributes": tuple(item["attributes"]),
                        "exclusion_reasons": tuple(item["exclusion_reasons"]),
                    }
                )
                for item in payload["normalized_assets"]
            )
            snapshot = CatalogSnapshot(
                **{**payload, "normalized_assets": assets}
            )
            if snapshot.canonical_sha256 != _sha256(
                {
                    key: value
                    for key, value in payload.items()
                    if key
                    not in {
                        "snapshot_id",
                        "canonical_sha256",
                        "discovery_evidence_id",
                    }
                }
            ):
                return "corrupt", None, {}
            age = max(
                0,
                int(
                    (
                        current - _parse_timestamp(envelope["fetched_at"])
                    ).total_seconds()
                ),
            )
            freshness = int(envelope["freshness_seconds"])
            stale_after = int(envelope["stale_after_seconds"])
            state = (
                "fresh"
                if age <= freshness
                else "stale_usable"
                if age <= stale_after
                else "expired"
            )
            return state, snapshot, {
                "fetched_at": envelope["fetched_at"],
                "validated_at": envelope["validated_at"],
                "age_seconds": age,
                "freshness_seconds": freshness,
                "stale_after_seconds": stale_after,
            }
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return "corrupt", None, {}


def _configured_credentials() -> tuple[str | None, str | None]:
    key = os.environ.get("APCA_API_KEY_ID") or os.environ.get(
        "SIGIL_ALPACA_API_KEY_ID"
    )
    secret = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get(
        "SIGIL_ALPACA_API_SECRET_KEY"
    )
    if key and secret:
        return key, secret
    try:
        from sigil.desktop_bridge.providers import load_credentials

        values = load_credentials()
        return (
            values.get("SIGIL_ALPACA_API_KEY_ID"),
            values.get("SIGIL_ALPACA_API_SECRET_KEY"),
        )
    except RuntimeError:
        return None, None


class AssetCatalogService:
    """Governed cache-first catalog orchestration for bridge commands."""

    def __init__(
        self,
        state_directory: Path,
        *,
        client: AlpacaAssetCatalogClient | None = None,
        freshness_seconds: int = DEFAULT_FRESHNESS_SECONDS,
        stale_after_seconds: int = DEFAULT_STALE_SECONDS,
    ) -> None:
        key, secret = _configured_credentials()
        self.client = client or AlpacaAssetCatalogClient(key, secret)
        self.store = AssetCatalogStore(state_directory)
        self.freshness_seconds = freshness_seconds
        self.stale_after_seconds = stale_after_seconds

    def refresh(self) -> dict[str, Any]:
        prior_state, prior, _ = self.store.load()
        probe = self.client.capability_probe()
        records = probe.pop("assets", None)
        if probe["failure_code"] is not None or not isinstance(records, list):
            if prior is not None and prior_state in {"fresh", "stale_usable"}:
                probe["failure_code"] = "cache_available_remote_failed"
                return self.status(
                    override_state="refresh_failed", failure=probe["failure_code"]
                )
            return self.status(
                override_state="refresh_failed", failure=probe["failure_code"]
            )
        discovered_at = probe["checked_at"]
        try:
            snapshot = build_snapshot(
                records,
                discovered_at=discovered_at,
                previous_snapshot_sha256=(
                    prior.canonical_sha256 if prior is not None else None
                ),
                freshness_seconds=self.freshness_seconds,
            )
        except AssetCatalogError as error:
            return self.status(
                override_state="refresh_failed", failure=error.code
            )
        self.store.write(
            snapshot,
            fetched_at=discovered_at,
            validated_at=discovered_at,
            freshness_seconds=self.freshness_seconds,
            stale_after_seconds=self.stale_after_seconds,
        )
        return self.status(capability=probe)

    def status(
        self,
        *,
        override_state: str | None = None,
        failure: str | None = None,
        capability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_state, snapshot, metadata = self.store.load()
        effective_state = override_state or cache_state
        statistics = _statistics(snapshot)
        return {
            "provider": "alpaca",
            "environment": "paper",
            "broker_submission": False,
            "revision": snapshot.snapshot_id if snapshot else "catalog-unavailable",
            "source": "Alpaca Paper Trading Assets API",
            "endpoint": "paper-api.alpaca.markets/v2/assets",
            "requested_filters": dict(sorted(ASSET_FILTERS.items())),
            "status": effective_state,
            "cache_state": cache_state,
            "failure_code": failure,
            "freshness": metadata,
            "integrity": "verified" if snapshot else cache_state,
            "statistics": statistics,
            "discovery_evidence_id": (
                snapshot.discovery_evidence_id if snapshot else None
            ),
            "capability": capability,
            "safety": {
                "environment": "paper",
                "broker_submission": False,
                "live_trading_enabled": False,
                "read_only_discovery": True,
            },
        }

    def snapshot(
        self, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        state, snapshot, metadata = self.store.load()
        if snapshot is None:
            return {
                **self.status(),
                "offset": 0,
                "limit": 0,
                "has_more": False,
                "assets": [],
            }
        bounded_limit = min(max(int(limit), 1), 100)
        bounded_offset = max(int(offset), 0)
        assets = snapshot.normalized_assets[
            bounded_offset : bounded_offset + bounded_limit
        ]
        return {
            **self.status(),
            "status": state,
            "freshness": metadata,
            "offset": bounded_offset,
            "limit": bounded_limit,
            "has_more": bounded_offset + bounded_limit < snapshot.asset_count,
            "assets": [item.to_dict() for item in assets],
        }

    def exclusions(self) -> dict[str, Any]:
        state, snapshot, _ = self.store.load()
        return {
            "environment": "paper",
            "broker_submission": False,
            "revision": snapshot.snapshot_id if snapshot else "catalog-unavailable",
            "status": state,
            "exclusion_reason_counts": (
                snapshot.exclusion_reason_counts if snapshot else {}
            ),
        }


def _statistics(snapshot: CatalogSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "total_assets_discovered": 0,
            "active_assets": 0,
            "tradable_assets": 0,
            "fractionable_assets": 0,
            "proposal_eligible_assets": 0,
            "excluded_assets": 0,
            "exchange_counts": {},
            "exclusion_reason_counts": {},
        }
    return {
        "total_assets_discovered": snapshot.asset_count,
        "active_assets": snapshot.active_count,
        "tradable_assets": snapshot.tradable_count,
        "fractionable_assets": snapshot.fractionable_count,
        "proposal_eligible_assets": snapshot.proposal_eligible_count,
        "excluded_assets": snapshot.excluded_count,
        "exchange_counts": snapshot.exchange_counts,
        "exclusion_reason_counts": snapshot.exclusion_reason_counts,
    }


class ResearchUniverseScheduler:
    """Restart-safe deterministic catalog traversal without execution capability."""

    def __init__(self, state_directory: Path, *, batch_size: int = 25) -> None:
        self.path = (
            state_directory / "asset-catalog" / "research-cursor-v1.json"
        )
        self.batch_size = min(max(batch_size, 1), 200)

    def _load(self, revision: str) -> dict[str, Any]:
        default = {
            "schema_version": 1,
            "revision": revision,
            "cursor": 0,
            "cycle_started_at": None,
            "cycle_completed_at": None,
            "completed": 0,
            "deferred": 0,
            "failed": 0,
            "last_completed_symbol": None,
        }
        if not self.path.exists():
            return default
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            if envelope["sha256"] != _sha256(payload):
                return default
            if payload.get("revision") != revision:
                return default
            return payload
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return default

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        envelope = {"payload": payload, "sha256": _sha256(payload)}
        descriptor, temporary = tempfile.mkstemp(
            prefix=".research-cursor.", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(_canonical(envelope))
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def next_batch(self, snapshot: CatalogSnapshot) -> dict[str, Any]:
        eligible = tuple(
            item for item in snapshot.normalized_assets if item.proposal_eligible
        )
        state = self._load(snapshot.snapshot_id)
        now = _timestamp(_now())
        if not eligible:
            return self.status(snapshot)
        cursor = min(int(state["cursor"]), len(eligible))
        if cursor >= len(eligible):
            cursor = 0
            state.update(
                {
                    "cursor": 0,
                    "completed": 0,
                    "deferred": 0,
                    "failed": 0,
                    "cycle_started_at": now,
                    "cycle_completed_at": None,
                }
            )
        if state["cycle_started_at"] is None:
            state["cycle_started_at"] = now
        batch = eligible[cursor : cursor + self.batch_size]
        next_cursor = cursor + len(batch)
        state["cursor"] = next_cursor
        state["completed"] = int(state["completed"]) + len(batch)
        state["last_completed_symbol"] = batch[-1].symbol if batch else None
        if next_cursor >= len(eligible):
            state["cycle_completed_at"] = now
        self._write(state)
        return {
            **self.status(snapshot, state=state),
            "symbols": [item.symbol for item in batch],
            "current_batch": cursor // self.batch_size + 1,
        }

    def status(
        self,
        snapshot: CatalogSnapshot,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        eligible = sum(item.proposal_eligible for item in snapshot.normalized_assets)
        current = state or self._load(snapshot.snapshot_id)
        completed = min(int(current["completed"]), eligible)
        return {
            "environment": "paper",
            "broker_submission": False,
            "revision": snapshot.snapshot_id,
            "catalog_total": snapshot.asset_count,
            "proposal_eligible": eligible,
            "research_queued": max(eligible - int(current["cursor"]), 0),
            "research_completed_current_cycle": completed,
            "research_deferred": int(current["deferred"]),
            "research_failed": int(current["failed"]),
            "research_coverage_percent": (
                round((completed / eligible) * 100, 2) if eligible else 0.0
            ),
            "current_batch": (
                int(current["cursor"]) // self.batch_size
                if current["cursor"]
                else 0
            ),
            "next_cursor": int(current["cursor"]),
            "last_completed_symbol": current["last_completed_symbol"],
            "cycle_started_at": current["cycle_started_at"],
            "cycle_completed_at": current["cycle_completed_at"],
            "batch_size": self.batch_size,
        }
