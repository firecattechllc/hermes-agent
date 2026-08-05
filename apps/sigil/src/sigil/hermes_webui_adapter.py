"""Disabled-by-default Hermes WebUI discovery, health, and deep-link adapter.

Stage 3 models approved private Hermes operator surfaces for Titan and Mac.
It performs no HTTP requests, authentication, service startup, job dispatch,
credential resolution, filesystem access, shell execution, or financial action.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sigil.ai.registry import canonical_digest
from sigil.integration_registry import AuthorityDenials
from sigil.worker_contract import WORKER_CONTRACT_SCHEMA_VERSION

HERMES_WEBUI_ADAPTER_SCHEMA_VERSION = 1

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|"
    r"client[_-]?secret|cookie|session[_-]?id|password)\s*[:=]|"
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_ROUTES = frozenset(
    {
        "/",
        "/chat",
        "/sessions",
        "/tasks",
        "/cron",
        "/skills",
        "/memory",
        "/profiles",
        "/files",
        "/models",
        "/approvals",
        "/health",
    }
)
_ALLOWED_QUERY_KEYS = frozenset({"profile", "session", "tab"})


class HermesWebUIValidationError(ValueError):
    """Hermes WebUI adapter input failed closed."""


class HermesNodeRole(str, Enum):
    PRIMARY = "primary"
    SENIOR = "senior"


class HermesWebUIHealth(str, Enum):
    DISABLED = "disabled"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class HermesWebUITarget:
    node_id: str
    display_name: str
    role: HermesNodeRole
    base_url: str
    approved_routes: tuple[str, ...]
    expected_worker_contract_schema: int = WORKER_CONTRACT_SCHEMA_VERSION
    enabled: bool = False
    schema_version: int = HERMES_WEBUI_ADAPTER_SCHEMA_VERSION
    content_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()

        if self.content_digest and self.content_digest != expected:
            raise HermesWebUIValidationError("Hermes WebUI target digest mismatch")

        if not self.content_digest:
            object.__setattr__(self, "content_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["role"] = self.role.value
        payload.pop("content_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.schema_version != HERMES_WEBUI_ADAPTER_SCHEMA_VERSION:
            raise HermesWebUIValidationError(
                "unsupported Hermes WebUI adapter schema version"
            )
        if _IDENTIFIER.fullmatch(self.node_id) is None:
            raise HermesWebUIValidationError("malformed Hermes node identity")
        if not self.display_name.strip():
            raise HermesWebUIValidationError("Hermes node display name is required")
        if not isinstance(self.role, HermesNodeRole):
            raise HermesWebUIValidationError("unknown Hermes node role")
        if self.expected_worker_contract_schema != WORKER_CONTRACT_SCHEMA_VERSION:
            raise HermesWebUIValidationError(
                "incompatible worker contract schema"
            )

        _validate_private_base_url(self.base_url)

        if not self.approved_routes:
            raise HermesWebUIValidationError(
                "at least one approved Hermes WebUI route is required"
            )

        for route in self.approved_routes:
            if route not in _ALLOWED_ROUTES:
                raise HermesWebUIValidationError(
                    f"unapproved Hermes WebUI route: {route}"
                )

        self.authority.validate()
        _validate_sanitized(self.digest_payload(), "Hermes WebUI target")

    @property
    def can_authenticate(self) -> bool:
        return False

    @property
    def can_dispatch(self) -> bool:
        return False

    @property
    def can_start_service(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class HermesWebUIProbe:
    node_id: str
    observed_at: str
    responding: bool
    dashboard_version: str | None
    worker_contract_schema: int | None
    component_health: str
    sanitized_message: str

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.node_id) is None:
            raise HermesWebUIValidationError("malformed Hermes node identity")
        if _UTC_TIMESTAMP.fullmatch(self.observed_at) is None:
            raise HermesWebUIValidationError(
                "probe time must be a canonical UTC timestamp"
            )
        if not self.component_health.strip():
            raise HermesWebUIValidationError("component health is required")
        _validate_sanitized(asdict(self), "Hermes WebUI probe")


@dataclass(frozen=True, slots=True)
class HermesWebUIStatus:
    node_id: str
    display_name: str
    role: HermesNodeRole
    state: HermesWebUIHealth
    enabled: bool
    observed_at: str | None
    dashboard_version: str | None
    worker_contract_compatible: bool
    deep_link_available: bool
    reason: str
    target_digest: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.node_id) is None:
            raise HermesWebUIValidationError("malformed Hermes node identity")
        if self.observed_at is not None and _UTC_TIMESTAMP.fullmatch(
            self.observed_at
        ) is None:
            raise HermesWebUIValidationError(
                "status observation time must be canonical UTC"
            )
        if not self.reason.strip():
            raise HermesWebUIValidationError("status reason is required")
        self.authority.validate()
        _validate_sanitized(asdict(self), "Hermes WebUI status")


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)
    if _SECRET.search(serialized):
        raise HermesWebUIValidationError(
            f"credential material is prohibited in {context}"
        )


def _validate_private_base_url(value: str) -> None:
    parsed = urlsplit(value)

    if (
        parsed.scheme not in _ALLOWED_SCHEMES
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HermesWebUIValidationError(
            "Hermes WebUI base URL must be a private origin without credentials"
        )

    hostname = parsed.hostname.lower()

    if hostname in {"localhost", "0.0.0.0"}:
        raise HermesWebUIValidationError(
            "wildcard and localhost Hermes WebUI targets are denied"
        )

    private = False

    try:
        address = ipaddress.ip_address(hostname)
        private = (
            address.is_private
            or address in ipaddress.ip_network("100.64.0.0/10")
        )
    except ValueError:
        private = (
            hostname.endswith(".ts.net")
            or hostname.endswith(".tailnet")
            or hostname.endswith(".local")
        )

    if not private:
        raise HermesWebUIValidationError(
            "Hermes WebUI target must be a private or tailnet address"
        )


def build_deep_link(
    target: HermesWebUITarget,
    route: str,
    *,
    query: dict[str, str] | None = None,
) -> str:
    """Build one allowlisted private deep link without authenticating or probing."""

    if not target.enabled:
        raise HermesWebUIValidationError(
            "Hermes WebUI target is disabled"
        )
    if route not in target.approved_routes or route not in _ALLOWED_ROUTES:
        raise HermesWebUIValidationError(
            "Hermes WebUI route is not approved"
        )

    values = {} if query is None else dict(query)

    if any(key not in _ALLOWED_QUERY_KEYS for key in values):
        raise HermesWebUIValidationError(
            "Hermes WebUI deep-link query key is not approved"
        )

    _validate_sanitized(values, "Hermes WebUI deep-link query")

    base = urlsplit(target.base_url)
    encoded = urlencode(sorted(parse_qsl(urlencode(values))))
    return urlunsplit(
        (
            base.scheme,
            base.netloc,
            route,
            encoded,
            "",
        )
    )


def evaluate_webui_status(
    target: HermesWebUITarget,
    probe: HermesWebUIProbe | None,
    *,
    now: str,
    stale_after_seconds: int = 120,
) -> HermesWebUIStatus:
    """Evaluate injected health evidence without performing a network request."""

    if _UTC_TIMESTAMP.fullmatch(now) is None:
        raise HermesWebUIValidationError(
            "status evaluation time must be canonical UTC"
        )
    if not 1 <= stale_after_seconds <= 86400:
        raise HermesWebUIValidationError(
            "health staleness threshold is outside bounds"
        )

    if not target.enabled:
        return HermesWebUIStatus(
            node_id=target.node_id,
            display_name=target.display_name,
            role=target.role,
            state=HermesWebUIHealth.DISABLED,
            enabled=False,
            observed_at=None if probe is None else probe.observed_at,
            dashboard_version=None if probe is None else probe.dashboard_version,
            worker_contract_compatible=False,
            deep_link_available=False,
            reason="Hermes WebUI target is disabled by policy.",
            target_digest=target.content_digest,
        )

    if probe is None:
        return HermesWebUIStatus(
            node_id=target.node_id,
            display_name=target.display_name,
            role=target.role,
            state=HermesWebUIHealth.UNAVAILABLE,
            enabled=True,
            observed_at=None,
            dashboard_version=None,
            worker_contract_compatible=False,
            deep_link_available=False,
            reason="No governed health evidence is available.",
            target_digest=target.content_digest,
        )

    if probe.node_id != target.node_id:
        raise HermesWebUIValidationError(
            "health probe does not match Hermes WebUI target"
        )

    observed = _parse_utc(probe.observed_at)
    evaluated = _parse_utc(now)
    age_seconds = int((evaluated - observed).total_seconds())

    if age_seconds < 0:
        raise HermesWebUIValidationError(
            "health probe observation is in the future"
        )

    compatible = (
        probe.worker_contract_schema
        == target.expected_worker_contract_schema
    )

    if age_seconds > stale_after_seconds:
        state = HermesWebUIHealth.STALE
        reason = f"Health evidence is stale by {age_seconds} seconds."
    elif not probe.responding:
        state = HermesWebUIHealth.UNAVAILABLE
        reason = "Hermes WebUI did not respond to the governed health probe."
    elif not compatible:
        state = HermesWebUIHealth.INCOMPATIBLE
        reason = "Hermes WebUI worker contract schema is incompatible."
    elif probe.component_health == "healthy":
        state = HermesWebUIHealth.HEALTHY
        reason = "Hermes WebUI health evidence is current and compatible."
    else:
        state = HermesWebUIHealth.DEGRADED
        reason = "Hermes WebUI reported degraded component health."

    return HermesWebUIStatus(
        node_id=target.node_id,
        display_name=target.display_name,
        role=target.role,
        state=state,
        enabled=True,
        observed_at=probe.observed_at,
        dashboard_version=probe.dashboard_version,
        worker_contract_compatible=compatible,
        deep_link_available=state
        in {HermesWebUIHealth.HEALTHY, HermesWebUIHealth.DEGRADED},
        reason=reason,
        target_digest=target.content_digest,
    )


def default_hermes_webui_targets() -> tuple[HermesWebUITarget, ...]:
    """Return disabled-by-default Titan and Mac operator-surface definitions."""

    routes = (
        "/",
        "/chat",
        "/sessions",
        "/tasks",
        "/cron",
        "/skills",
        "/memory",
        "/profiles",
        "/files",
        "/models",
        "/approvals",
        "/health",
    )

    return (
        HermesWebUITarget(
            node_id="hermes-titan",
            display_name="Hermes Titan",
            role=HermesNodeRole.PRIMARY,
            base_url="http://100.103.4.38",
            approved_routes=routes,
        ),
        HermesWebUITarget(
            node_id="hermes-mac",
            display_name="Hermes Mac",
            role=HermesNodeRole.SENIOR,
            base_url="http://100.68.14.37",
            approved_routes=routes,
        ),
    )


_PROBE_MAX_RESPONSE_BYTES = 65_536


def probe_webui_target(
    target: HermesWebUITarget,
    *,
    timeout_seconds: float = 5.0,
    now: str | None = None,
) -> HermesWebUIProbe:
    """Perform one real, bounded, read-only GET against ``target``'s ``/health`` route.

    Only called for an already-``enabled`` target (real deployments opt in
    explicitly); the private/tailnet-only host allowlist enforced by
    :func:`_validate_private_base_url` at target construction time still
    applies, so this can never reach a public address. No authentication,
    no request body, no state mutation, no dispatch. A failure of any kind
    degrades to a non-``responding`` probe rather than raising, so a
    Mission Control panel can always render a status.
    """

    if not target.enabled:
        raise HermesWebUIValidationError(
            "cannot probe a disabled Hermes WebUI target"
        )
    if not 0 < timeout_seconds <= 30:
        raise HermesWebUIValidationError("probe timeout is outside bounds")

    observed_at = now or datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    base = urlsplit(target.base_url)
    health_url = urlunsplit((base.scheme, base.netloc, "/health", "", ""))

    responding = False
    dashboard_version: str | None = None
    worker_contract_schema: int | None = None
    component_health = "unavailable"
    sanitized_message = "no response"

    try:
        request = urllib.request.Request(
            health_url,
            method="GET",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(  # noqa: S310 - private/tailnet allowlist enforced above
            request, timeout=timeout_seconds
        ) as response:
            body = response.read(_PROBE_MAX_RESPONSE_BYTES)
            responding = 200 <= response.status < 300
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            if isinstance(payload, dict):
                dashboard_version = payload.get("version")
                schema = payload.get("worker_contract_schema")
                worker_contract_schema = schema if isinstance(schema, int) else None
                component_health = str(payload.get("status", "healthy" if responding else "unavailable"))
            sanitized_message = f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        sanitized_message = f"HTTP {error.code}"
    except (urllib.error.URLError, TimeoutError, OSError):
        sanitized_message = "connection failed"

    return HermesWebUIProbe(
        node_id=target.node_id,
        observed_at=observed_at,
        responding=responding,
        dashboard_version=dashboard_version,
        worker_contract_schema=worker_contract_schema,
        component_health=component_health,
        sanitized_message=sanitized_message,
    )


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00").astimezone(
        timezone.utc
    )
