"""Governed configuration for Titan's local OmniRoute + FreeLLMAPI routing.

Hermes add-on: Titan-local governed model routing. Hermes owns policy
(provider allowlists, priority, budgets, offline-only mode); OmniRoute (a
Titan-local service built from this configuration, see
:mod:`hermes_cli.prime.omniroute_server`) owns transport between Titan
Ollama and FreeLLMAPI. This module is the single, fail-closed source of that
policy — every field is validated at construction, never at first use, so a
malformed or Mac-dependent configuration can never reach a running service.

Hard requirement: Titan must keep operating with the Mac powered off,
disconnected, or otherwise unavailable. :func:`validate_no_mac_dependency`
is the enforcement point for that requirement — it is called from
:meth:`TitanRoutingConfig.from_env` on every raw environment value *before*
any field is parsed, so a Mac Tailscale address, Mac hostname, ``/Users/...``
path, ``host.docker.internal``, or a provider named as a Mac fallback can
never become part of a constructed config, let alone reach the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple, cast

# Closed provider vocabulary for Titan-local governed routing. Adding a
# provider is a code change, not a request parameter — matching the
# KNOWN_FLEET_NODES / KNOWN_FLEET_CAPABILITIES convention used elsewhere in
# hermes_cli.prime.
KNOWN_TITAN_PROVIDERS: Tuple[str, ...] = ("titan_ollama", "freellmapi")

AUDIT_VERBOSITY_LEVELS: Tuple[str, ...] = ("minimal", "standard", "verbose")

# Discovered Mac Tailscale identity (see
# docs/architecture/hydra-ecosystem/evidence/TITAN_DISCOVERY.md and
# MACBOOK_DISCOVERY.md). Always forbidden; operators may only *extend* this
# set via HERMES_TITAN_FORBIDDEN_MAC_ADDRESSES, never shrink it.
DEFAULT_FORBIDDEN_MAC_ADDRESSES: Tuple[str, ...] = (
    "100.68.14.37",
    "matthews-macbook-air",
)

_MAC_HOSTNAME_MARKERS = (
    "macbook",
    "mac-mini",
    "mac-studio",
    "mac-pro",
    "mac-air",
    ".local",
)
_MAC_FALLBACK_MARKERS = ("mac_fallback", "mac-fallback", "macfallback", "mac fallback")
_USERS_PATH_PATTERN = re.compile(r"/Users/")
_HOST_DOCKER_INTERNAL = "host.docker.internal"

_PRIVATE_HOST_PATTERN = re.compile(
    r"^(127\.\d+\.\d+\.\d+|::1|10\.\d+\.\d+\.\d+|"
    r"172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+|localhost)$"
)


class TitanRoutingConfigError(ValueError):
    """A Titan governed-routing configuration is invalid, incomplete, or
    contains a forbidden Mac dependency."""


@dataclass(frozen=True, slots=True)
class AliasResolution:
    """The outcome of resolving one governed model alias against policy."""

    alias: str
    reason: str
    provider_id: Optional[str] = None
    model: Optional[str] = None
    permitted: bool = False


def validate_no_mac_dependency(
    values: Mapping[str, Optional[str]],
    *,
    forbidden_mac_addresses: Tuple[str, ...] = DEFAULT_FORBIDDEN_MAC_ADDRESSES,
) -> Tuple[str, ...]:
    """Scan configuration values for forbidden Mac dependencies. Fail closed.

    Returns every violation found (never just the first) so a single
    configuration error report can be shown in full. An empty tuple means no
    Mac dependency was found in any of ``values``. This function only reads
    ``values`` and forms an error message; it performs no I/O and never
    reflects a secret value back into its output.
    """
    violations: list[str] = []
    lowered_forbidden = tuple(marker.lower() for marker in forbidden_mac_addresses)

    for name, raw in values.items():
        if raw is None:
            continue
        value = str(raw)
        lowered = value.lower()

        if _USERS_PATH_PATTERN.search(value):
            violations.append(f"{name} contains a Mac filesystem path (/Users/...)")

        if _HOST_DOCKER_INTERNAL in lowered:
            violations.append(
                f"{name} references host.docker.internal; use a localhost or "
                "private-network address that resolves on Titan itself instead"
            )

        for marker in lowered_forbidden:
            if marker and marker in lowered:
                violations.append(
                    f"{name} references a known Mac Tailscale identity ({marker!r})"
                )

        for marker in _MAC_HOSTNAME_MARKERS:
            if marker in lowered:
                violations.append(
                    f"{name} looks like a Mac hostname ({marker!r} marker found)"
                )

        for marker in _MAC_FALLBACK_MARKERS:
            if marker in lowered:
                violations.append(f"{name} names a Mac fallback provider ({marker!r})")

    # De-duplicate while preserving first-seen order.
    seen: dict[str, None] = {}
    for item in violations:
        seen.setdefault(item, None)
    return tuple(seen.keys())


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise TitanRoutingConfigError(f"{name} is required and must not be blank")
    return value


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise TitanRoutingConfigError(f"{name} must be a boolean (true/false)")


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as error:
        raise TitanRoutingConfigError(f"{name} must be an integer") from error


def _optional_int_env(env: Mapping[str, str], name: str) -> Optional[int]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as error:
        raise TitanRoutingConfigError(f"{name} must be an integer") from error


def _csv_env(env: Mapping[str, str], name: str) -> Tuple[str, ...]:
    raw = env.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _alias_routes_env(
    env: Mapping[str, str], name: str
) -> Mapping[str, Tuple[str, str]]:
    """Parse ``alias=provider@model,alias2=provider2@model2`` pairs.

    ``=`` separates the alias from its route and ``@`` separates the
    provider from the model, deliberately avoiding ``:`` as a delimiter
    since Ollama model tags routinely contain one (e.g.
    ``embeddinggemma:latest``) — only the first ``=`` and first ``@`` in
    each entry are treated as delimiters, so a model tag may contain either
    character freely. Malformed entries fail closed at config-parse time
    rather than being silently skipped or guessed at.
    """
    raw = env.get(name, "")
    routes: dict[str, Tuple[str, str]] = {}
    for entry in (item.strip() for item in raw.split(",") if item.strip()):
        alias, equals, remainder = entry.partition("=")
        provider_id, at, model = remainder.partition("@")
        if (
            not equals
            or not at
            or not alias.strip()
            or not provider_id.strip()
            or not model.strip()
        ):
            raise TitanRoutingConfigError(
                f"{name} entry {entry!r} must have the form alias=provider@model"
            )
        alias, provider_id, model = alias.strip(), provider_id.strip(), model.strip()
        if alias in routes:
            raise TitanRoutingConfigError(f"{name} declares duplicate alias {alias!r}")
        routes[alias] = (provider_id, model)
    return routes


@dataclass(frozen=True, slots=True)
class TitanRoutingConfig:
    """Immutable, validated governance policy for Titan's OmniRoute service.

    Every provider-affecting field is validated here, once, at
    construction — nothing downstream (the OmniRoute HTTP service, the
    Hermes-side provider adapter, health aggregation) re-derives or
    re-validates this policy; they only consume it.
    """

    omniroute_enabled: bool
    freellmapi_enabled: bool
    titan_ollama_enabled: bool
    provider_priority: Tuple[str, ...]
    provider_timeout_ms: Mapping[str, int]
    provider_retry_limit: Mapping[str, int]
    allowed_model_aliases: Tuple[str, ...]
    alias_routes: Mapping[str, Tuple[str, str]]
    denied_models: Tuple[str, ...]
    denied_providers: Tuple[str, ...]
    offline_local_only: bool
    audit_verbosity: str
    health_check_interval_seconds: int
    titan_ollama_endpoint: str
    freellmapi_base_url: str
    bind_host: str
    bind_port: int
    max_context_tokens: Optional[int] = None
    max_request_bytes: Optional[int] = None
    daily_budget_micros: Optional[int] = None
    request_budget_micros: Optional[int] = None
    # repr=False: these must never appear in a log line, traceback, or
    # __repr__ dump of this config object.
    freellmapi_api_key: Optional[str] = field(default=None, repr=False)
    omniroute_auth_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.bind_host or not _PRIVATE_HOST_PATTERN.match(self.bind_host):
            raise TitanRoutingConfigError(
                "OmniRoute bind host must be localhost or a private-network address "
                "(127.0.0.1, ::1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16); "
                "refusing to bind a wildcard or public address"
            )
        if not 0 <= self.bind_port <= 65535:
            raise TitanRoutingConfigError(
                "OmniRoute bind port must be between 0 and 65535 (0 requests an "
                "OS-assigned ephemeral port, used in tests)"
            )
        if not self.omniroute_auth_token or len(self.omniroute_auth_token) < 16:
            raise TitanRoutingConfigError(
                "OmniRoute auth token must be set and at least 16 characters"
            )
        if self.audit_verbosity not in AUDIT_VERBOSITY_LEVELS:
            raise TitanRoutingConfigError(
                f"audit_verbosity must be one of {AUDIT_VERBOSITY_LEVELS}"
            )
        if not 5 <= self.health_check_interval_seconds <= 3600:
            raise TitanRoutingConfigError(
                "health_check_interval_seconds must be between 5 and 3600"
            )

        unknown_priority = tuple(
            sorted(set(self.provider_priority) - set(KNOWN_TITAN_PROVIDERS))
        )
        if unknown_priority:
            raise TitanRoutingConfigError(
                f"unknown provider(s) in priority list: {unknown_priority}"
            )
        if len(self.provider_priority) != len(set(self.provider_priority)):
            raise TitanRoutingConfigError(
                "provider_priority must not contain duplicates"
            )
        if not self.provider_priority:
            raise TitanRoutingConfigError(
                "provider_priority must declare at least one provider"
            )

        unknown_timeout = tuple(
            sorted(set(self.provider_timeout_ms) - set(KNOWN_TITAN_PROVIDERS))
        )
        if unknown_timeout:
            raise TitanRoutingConfigError(
                f"unknown provider(s) in timeout map: {unknown_timeout}"
            )
        for provider, timeout_ms in self.provider_timeout_ms.items():
            if not 100 <= timeout_ms <= 120_000:
                raise TitanRoutingConfigError(
                    f"provider_timeout_ms[{provider!r}] must be between 100 and 120000"
                )

        unknown_retry = tuple(
            sorted(set(self.provider_retry_limit) - set(KNOWN_TITAN_PROVIDERS))
        )
        if unknown_retry:
            raise TitanRoutingConfigError(
                f"unknown provider(s) in retry limit map: {unknown_retry}"
            )
        for provider, retry_limit in self.provider_retry_limit.items():
            # Bounded, small retry limits only -- this is the enforcement
            # point that prevents an infinite retry loop against a wedged
            # upstream from ever being configurable.
            if not 0 <= retry_limit <= 5:
                raise TitanRoutingConfigError(
                    f"provider_retry_limit[{provider!r}] must be between 0 and 5"
                )

        unknown_denied = tuple(
            sorted(set(self.denied_providers) - set(KNOWN_TITAN_PROVIDERS))
        )
        if unknown_denied:
            raise TitanRoutingConfigError(
                f"unknown provider(s) in denied_providers: {unknown_denied}"
            )

        # provider_priority and denied_providers are deliberately allowed to
        # overlap: denied_providers is a live kill-switch a caller may set
        # without also having to edit the priority list. is_provider_permitted()
        # / resolve_alias_detailed() always consult denied_providers directly
        # and take precedence over anything provider_priority says.

        if self.allowed_model_aliases:
            unknown_aliases = tuple(
                sorted(set(self.alias_routes) - set(self.allowed_model_aliases))
            )
            if unknown_aliases:
                raise TitanRoutingConfigError(
                    f"alias_routes declares alias(es) not present in allowed_model_aliases: "
                    f"{unknown_aliases}"
                )
        for alias, (provider_id, model) in self.alias_routes.items():
            if not alias.strip():
                raise TitanRoutingConfigError("alias_routes keys must not be blank")
            if provider_id not in KNOWN_TITAN_PROVIDERS:
                raise TitanRoutingConfigError(
                    f"alias_routes[{alias!r}] references unknown provider {provider_id!r}"
                )
            # Deliberately not an error for provider_id to be in
            # denied_providers here: denying a provider is meant to work as
            # a live kill-switch (one env var) without also requiring every
            # alias_routes entry that names it to be edited or removed.
            # resolve_alias_detailed() is the actual, always-consulted
            # enforcement point for a denied provider at dispatch time.
            if not model.strip():
                raise TitanRoutingConfigError(
                    f"alias_routes[{alias!r}] must name a non-empty model"
                )
            if model in self.denied_models:
                raise TitanRoutingConfigError(
                    f"alias_routes[{alias!r}] routes to a denied model {model!r}"
                )

        if self.offline_local_only and not self.titan_ollama_enabled:
            raise TitanRoutingConfigError(
                "offline_local_only requires titan_ollama_enabled=true, otherwise no "
                "route could ever be served"
            )
        if not self.omniroute_enabled and (
            self.freellmapi_enabled or self.titan_ollama_enabled
        ):
            # Not an error by itself (Hermes may keep provider toggles set
            # while the OmniRoute service itself is administratively
            # disabled) -- but a caller enabling providers with the router
            # off is very likely a misconfiguration, so fail closed.
            raise TitanRoutingConfigError(
                "omniroute_enabled=false but a provider is enabled; disable providers "
                "too, or enable omniroute_enabled"
            )
        if self.max_context_tokens is not None and self.max_context_tokens <= 0:
            raise TitanRoutingConfigError(
                "max_context_tokens must be positive when set"
            )
        if self.max_request_bytes is not None and self.max_request_bytes <= 0:
            raise TitanRoutingConfigError("max_request_bytes must be positive when set")
        if self.daily_budget_micros is not None and self.daily_budget_micros < 0:
            raise TitanRoutingConfigError(
                "daily_budget_micros must not be negative when set"
            )
        if self.request_budget_micros is not None and self.request_budget_micros < 0:
            raise TitanRoutingConfigError(
                "request_budget_micros must not be negative when set"
            )

        violations = validate_no_mac_dependency(self._mac_scan_values())
        if violations:
            raise TitanRoutingConfigError(
                "Titan routing configuration rejected — Mac dependency detected: "
                + "; ".join(violations)
            )

    def _mac_scan_values(self) -> Mapping[str, Optional[str]]:
        """Every string-valued field that could plausibly carry an address,
        hostname, path, or provider name -- deliberately excludes secret
        fields (the auth token / API key), which are opaque credential
        material, not addresses or paths, and must never be echoed into a
        validation error message."""
        return {
            "titan_ollama_endpoint": self.titan_ollama_endpoint,
            "freellmapi_base_url": self.freellmapi_base_url,
            "bind_host": self.bind_host,
            "provider_priority": ",".join(self.provider_priority),
            "denied_providers": ",".join(self.denied_providers),
            "allowed_model_aliases": ",".join(self.allowed_model_aliases),
            "denied_models": ",".join(self.denied_models),
            "alias_routes": ",".join(
                f"{alias}:{provider}:{model}"
                for alias, (provider, model) in self.alias_routes.items()
            ),
        }

    def resolve_alias_detailed(self, alias: Optional[str]) -> "AliasResolution":
        """Resolve a governed model alias with a specific, recordable reason.

        This is the evidence-grade counterpart to :meth:`resolve_alias`: it
        never collapses "no such alias" and "alias exists but its provider
        is disabled/denied/blocked by offline-only mode" into the same
        outcome, because the required route evidence
        (:mod:`hermes_cli.prime.omniroute_evidence`) must distinguish an
        unknown-provider rejection from a policy rejection.
        """
        if alias is None or not alias.strip():
            return AliasResolution(alias=alias or "", reason="blank_alias")
        route = self.alias_routes.get(alias)
        if route is None:
            return AliasResolution(alias=alias, reason="unknown_alias")
        provider_id, model = route
        if provider_id not in KNOWN_TITAN_PROVIDERS:
            return AliasResolution(alias=alias, reason="unknown_provider")
        if provider_id in self.denied_providers:
            return AliasResolution(alias=alias, reason="provider_denied")
        if self.offline_local_only and provider_id != "titan_ollama":
            return AliasResolution(
                alias=alias, reason="offline_local_only_blocks_remote"
            )
        enabled = (
            self.titan_ollama_enabled
            if provider_id == "titan_ollama"
            else self.freellmapi_enabled
        )
        if not enabled:
            return AliasResolution(alias=alias, reason="provider_disabled")
        return AliasResolution(
            alias=alias,
            reason="resolved",
            provider_id=provider_id,
            model=model,
            permitted=True,
        )

    def resolve_alias(self, alias: Optional[str]) -> Optional[Tuple[str, str]]:
        """Resolve a governed model alias to ``(provider_id, model)``.

        Returns ``None`` (never raises) for a blank, unknown, or
        not-permitted alias — an "unknown provider request" per the
        required route policy is a normal, recordable outcome for the
        caller (:mod:`hermes_cli.prime.omniroute_server`) to reject and
        log, not an exceptional condition.
        """
        resolution = self.resolve_alias_detailed(alias)
        if not resolution.permitted:
            return None
        return cast(str, resolution.provider_id), cast(str, resolution.model)

    def is_provider_permitted(self, provider_id: str) -> bool:
        """True only for a known, enabled, non-denied provider."""
        if provider_id not in KNOWN_TITAN_PROVIDERS:
            return False
        if provider_id in self.denied_providers:
            return False
        if self.offline_local_only and provider_id != "titan_ollama":
            return False
        if provider_id == "titan_ollama":
            return self.titan_ollama_enabled
        if provider_id == "freellmapi":
            return self.freellmapi_enabled
        return False

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "TitanRoutingConfig":
        import os

        env = env if env is not None else os.environ

        forbidden_mac_addresses = DEFAULT_FORBIDDEN_MAC_ADDRESSES + _csv_env(
            env, "HERMES_TITAN_FORBIDDEN_MAC_ADDRESSES"
        )
        # Validate the *raw* environment before any field parsing occurs --
        # this is the earliest possible fail-closed point, before a
        # malformed or Mac-dependent value can influence any other field
        # (e.g. a provider_priority parsed from a poisoned raw string).
        raw_scan = {
            "HERMES_TITAN_OLLAMA_ENDPOINT": env.get("HERMES_TITAN_OLLAMA_ENDPOINT"),
            "HERMES_FREELLMAPI_BASE_URL": env.get("HERMES_FREELLMAPI_BASE_URL"),
            "HERMES_OMNIROUTE_BIND_HOST": env.get("HERMES_OMNIROUTE_BIND_HOST"),
            "HERMES_OMNIROUTE_PROVIDER_PRIORITY": env.get(
                "HERMES_OMNIROUTE_PROVIDER_PRIORITY"
            ),
            "HERMES_OMNIROUTE_DENIED_PROVIDERS": env.get(
                "HERMES_OMNIROUTE_DENIED_PROVIDERS"
            ),
        }
        violations = validate_no_mac_dependency(
            raw_scan, forbidden_mac_addresses=forbidden_mac_addresses
        )
        if violations:
            raise TitanRoutingConfigError(
                "Titan routing configuration rejected — Mac dependency detected: "
                + "; ".join(violations)
            )

        priority = (
            _csv_env(env, "HERMES_OMNIROUTE_PROVIDER_PRIORITY") or KNOWN_TITAN_PROVIDERS
        )

        return cls(
            omniroute_enabled=_bool_env(env, "HERMES_OMNIROUTE_ENABLED", True),
            freellmapi_enabled=_bool_env(env, "HERMES_FREELLMAPI_ENABLED", True),
            titan_ollama_enabled=_bool_env(env, "HERMES_TITAN_OLLAMA_ENABLED", True),
            provider_priority=priority,
            provider_timeout_ms={
                "titan_ollama": _int_env(env, "HERMES_TITAN_OLLAMA_TIMEOUT_MS", 30_000),
                "freellmapi": _int_env(env, "HERMES_FREELLMAPI_TIMEOUT_MS", 20_000),
            },
            provider_retry_limit={
                "titan_ollama": _int_env(env, "HERMES_TITAN_OLLAMA_RETRY_LIMIT", 1),
                "freellmapi": _int_env(env, "HERMES_FREELLMAPI_RETRY_LIMIT", 2),
            },
            allowed_model_aliases=_csv_env(
                env, "HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES"
            ),
            alias_routes=_alias_routes_env(env, "HERMES_OMNIROUTE_ALIAS_ROUTES"),
            denied_models=_csv_env(env, "HERMES_OMNIROUTE_DENIED_MODELS"),
            denied_providers=_csv_env(env, "HERMES_OMNIROUTE_DENIED_PROVIDERS"),
            offline_local_only=_bool_env(
                env, "HERMES_OMNIROUTE_OFFLINE_LOCAL_ONLY", False
            ),
            audit_verbosity=env.get(
                "HERMES_OMNIROUTE_AUDIT_VERBOSITY", "standard"
            ).strip()
            or "standard",
            health_check_interval_seconds=_int_env(
                env, "HERMES_OMNIROUTE_HEALTH_CHECK_INTERVAL_SECONDS", 30
            ),
            titan_ollama_endpoint=env.get(
                "HERMES_TITAN_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"
            ).strip()
            or "http://127.0.0.1:11434",
            freellmapi_base_url=env.get(
                "HERMES_FREELLMAPI_BASE_URL", "http://127.0.0.1:3002"
            ).strip()
            or "http://127.0.0.1:3002",
            bind_host=env.get("HERMES_OMNIROUTE_BIND_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            bind_port=_int_env(env, "HERMES_OMNIROUTE_BIND_PORT", 8791),
            max_context_tokens=_optional_int_env(
                env, "HERMES_OMNIROUTE_MAX_CONTEXT_TOKENS"
            ),
            max_request_bytes=_optional_int_env(
                env, "HERMES_OMNIROUTE_MAX_REQUEST_BYTES"
            ),
            daily_budget_micros=_optional_int_env(
                env, "HERMES_OMNIROUTE_DAILY_BUDGET_MICROS"
            ),
            request_budget_micros=_optional_int_env(
                env, "HERMES_OMNIROUTE_REQUEST_BUDGET_MICROS"
            ),
            freellmapi_api_key=env.get("HERMES_FREELLMAPI_API_KEY", "").strip() or None,
            omniroute_auth_token=_require(env, "HERMES_OMNIROUTE_AUTH_TOKEN"),
        )
