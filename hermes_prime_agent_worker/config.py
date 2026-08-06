"""Governed configuration for the Prime Agent worker adapter.

Fail-closed, same convention as :mod:`hermes_docs_worker.config`: every
field is validated once in ``__post_init__``/``from_env`` before any
subprocess, filesystem write, or network call happens. A malformed
configuration raises :class:`PrimeAgentWorkerConfigError` before touching
anything.

The Mac-dependency guard below is intentionally a local copy of the same
check used by :mod:`hermes_docs_worker.config` (itself a local copy of
``hermes_cli.prime.omniroute_config.validate_no_mac_dependency``, which
lives on a separate, not-yet-merged branch). Each governed worker package
keeps its own copy rather than importing across package boundaries -- see
the docs-worker's own docstring for why. Keep the copies in sync if the
shared policy changes.

Env var prefix is deliberately ``HERMES_PRIME_AGENT_WORKER_*``, not
``HERMES_PRIME_*`` -- ``hermes_cli.prime`` already owns that namespace for
the unrelated "Hydra Prime" fleet control-plane node. Confusing the two
would be a real operational hazard on Titan, which runs both.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

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
)
_MAC_FALLBACK_MARKERS = ("mac_fallback", "mac-fallback", "macfallback", "mac fallback")
_USERS_PATH_PATTERN = re.compile(r"/Users/")
_HOST_DOCKER_INTERNAL = "host.docker.internal"
# An mDNS-style Mac hostname (e.g. "matthews-macbook.local"): a hostname
# label immediately followed by ".local". Deliberately requires at least
# one hostname character before the literal ".local" and none of
# "/", ".", or another word character right before it, so this does not
# false-positive on the ordinary Linux XDG path convention
# "~/.local/share/..." (Prime Agent's own private Node.js install
# location) -- that ".local" is a dotfile directory name, not a hostname
# suffix.
_MAC_LOCAL_HOSTNAME_PATTERN = re.compile(r"(?<![/.\w])[a-z0-9-]+\.local\b")


def validate_no_mac_dependency(
    values: Mapping[str, Optional[str]],
    *,
    forbidden_mac_addresses: Tuple[str, ...] = DEFAULT_FORBIDDEN_MAC_ADDRESSES,
) -> Tuple[str, ...]:
    """Scan configuration values for forbidden Mac dependencies. Fail closed.

    Returns every violation found (never just the first). An empty tuple
    means no Mac dependency was found. Performs no I/O and never reflects a
    secret value back into its output.
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

        if _MAC_LOCAL_HOSTNAME_PATTERN.search(lowered):
            violations.append(f"{name} looks like an mDNS Mac hostname (*.local)")

        for marker in _MAC_FALLBACK_MARKERS:
            if marker in lowered:
                violations.append(f"{name} names a Mac fallback provider ({marker!r})")

    seen: dict[str, None] = {}
    for item in violations:
        seen.setdefault(item, None)
    return tuple(seen.keys())


class PrimeAgentWorkerConfigError(ValueError):
    """A Prime Agent worker configuration is invalid or unsafe."""


# The one systemd unit this worker is deployed under. Fixed, not
# operator-configurable -- part of the "no direct enabling or editing of
# its own systemd unit" governance boundary (see policy.py).
OWN_SYSTEMD_UNIT_NAME = "hermes-prime-agent-worker.service"

ALLOWED_EXECUTABLE_BASENAME = "prime-agent"


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise PrimeAgentWorkerConfigError(f"{name} is required and must not be blank")
    return value


def _path_env(env: Mapping[str, str], name: str, default: str) -> Path:
    raw = env.get(name, "").strip() or default
    return Path(raw)


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as error:
        raise PrimeAgentWorkerConfigError(f"{name} must be an integer") from error


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise PrimeAgentWorkerConfigError(f"{name} must be a boolean (true/false)")


def _csv_env(
    env: Mapping[str, str], name: str, default: Tuple[str, ...]
) -> Tuple[str, ...]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _csv_path_env(env: Mapping[str, str], name: str) -> Tuple[Path, ...]:
    return tuple(Path(p) for p in _csv_env(env, name, ()))


@dataclass(frozen=True, slots=True)
class PrimeAgentWorkerConfig:
    """Immutable, validated configuration for the Prime Agent worker adapter."""

    executable: Path
    node_bin_dir: Path
    home_dir: Path
    workspace_allowlist: Tuple[Path, ...]
    state_dir: Path
    cache_dir: Path
    log_dir: Path
    xdg_data_home: Path
    provider: str
    model: str
    provider_active: bool
    max_turns: int
    max_tokens: int
    timeout_seconds: int
    gate_retries: int
    gate_timeout_seconds: int
    max_output_bytes: int
    max_concurrent_sessions: int
    allowed_tools: Tuple[str, ...]
    mutation_tools: Tuple[str, ...]
    allowed_network_endpoints: Tuple[str, ...]
    evidence_retention_days: int
    evidence_max_files: int
    cooldown_seconds_after_failure: int
    max_consecutive_failures_before_cooldown: int
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.executable.name != ALLOWED_EXECUTABLE_BASENAME:
            raise PrimeAgentWorkerConfigError(
                f"executable must be named {ALLOWED_EXECUTABLE_BASENAME!r}, "
                f"got {self.executable.name!r}"
            )

        absolute_paths = {
            "executable": self.executable,
            "node_bin_dir": self.node_bin_dir,
            "home_dir": self.home_dir,
            "state_dir": self.state_dir,
            "cache_dir": self.cache_dir,
            "log_dir": self.log_dir,
            "xdg_data_home": self.xdg_data_home,
        }
        for name, value in absolute_paths.items():
            if not value.is_absolute():
                raise PrimeAgentWorkerConfigError(f"{name} must be an absolute path")

        if not self.workspace_allowlist:
            raise PrimeAgentWorkerConfigError(
                "workspace_allowlist must declare at least one absolute path"
            )
        for path in self.workspace_allowlist:
            if not path.is_absolute():
                raise PrimeAgentWorkerConfigError(
                    f"workspace_allowlist entry {path} must be absolute"
                )

        if not 1 <= self.max_turns <= 12:
            raise PrimeAgentWorkerConfigError(
                "max_turns must be between 1 and 12 (Prime Agent's own default ceiling)"
            )
        if not 1 <= self.max_tokens <= 80_000:
            raise PrimeAgentWorkerConfigError(
                "max_tokens must be between 1 and 80000 (Prime Agent's own default ceiling)"
            )
        if not 10 <= self.timeout_seconds <= 1800:
            raise PrimeAgentWorkerConfigError(
                "timeout_seconds must be between 10 and 1800 (30 minutes)"
            )
        if not 0 <= self.gate_retries <= 3:
            raise PrimeAgentWorkerConfigError("gate_retries must be between 0 and 3")
        if not 1 <= self.gate_timeout_seconds <= 300:
            raise PrimeAgentWorkerConfigError(
                "gate_timeout_seconds must be between 1 and 300"
            )
        if not 1_000 <= self.max_output_bytes <= 2_000_000:
            raise PrimeAgentWorkerConfigError(
                "max_output_bytes must be between 1000 and 2000000"
            )
        if not 1 <= self.max_concurrent_sessions <= 3:
            raise PrimeAgentWorkerConfigError(
                "max_concurrent_sessions must be between 1 and 3 (Pi5-appropriate ceiling)"
            )
        if not 1 <= self.evidence_retention_days <= 365:
            raise PrimeAgentWorkerConfigError(
                "evidence_retention_days must be between 1 and 365"
            )
        if not 10 <= self.evidence_max_files <= 100_000:
            raise PrimeAgentWorkerConfigError(
                "evidence_max_files must be between 10 and 100000"
            )
        if not 0 <= self.cooldown_seconds_after_failure <= 3600:
            raise PrimeAgentWorkerConfigError(
                "cooldown_seconds_after_failure must be between 0 and 3600"
            )
        if not 1 <= self.max_consecutive_failures_before_cooldown <= 20:
            raise PrimeAgentWorkerConfigError(
                "max_consecutive_failures_before_cooldown must be between 1 and 20"
            )

        overlap = set(self.allowed_tools) & set(self.mutation_tools)
        if overlap:
            raise PrimeAgentWorkerConfigError(
                "allowed_tools and mutation_tools must be disjoint "
                f"(mutation-only tools found in both: {sorted(overlap)})"
            )

        violations = validate_no_mac_dependency(self._mac_scan_values())
        if violations:
            raise PrimeAgentWorkerConfigError(
                "Prime Agent worker configuration rejected — Mac dependency "
                "detected: " + "; ".join(violations)
            )

    def _mac_scan_values(self) -> Mapping[str, Optional[str]]:
        return {
            "executable": str(self.executable),
            "node_bin_dir": str(self.node_bin_dir),
            "home_dir": str(self.home_dir),
            "state_dir": str(self.state_dir),
            "workspace_allowlist": ",".join(str(p) for p in self.workspace_allowlist),
            "allowed_network_endpoints": ",".join(self.allowed_network_endpoints),
        }

    def filesystem_allowlist(self) -> Tuple[Path, ...]:
        """Every path Prime Agent may be pointed at as a session ``--cwd``."""
        return self.workspace_allowlist

    def is_within_allowlist(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.filesystem_allowlist():
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    @classmethod
    def from_env(
        cls, env: Optional[Mapping[str, str]] = None
    ) -> "PrimeAgentWorkerConfig":
        env = env if env is not None else os.environ

        home_dir = Path(_require(env, "HERMES_PRIME_AGENT_WORKER_HOME_DIR"))

        raw_scan = {
            "HERMES_PRIME_AGENT_WORKER_HOME_DIR": env.get(
                "HERMES_PRIME_AGENT_WORKER_HOME_DIR"
            ),
            "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST": env.get(
                "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST"
            ),
        }
        forbidden = DEFAULT_FORBIDDEN_MAC_ADDRESSES + _csv_env(
            env, "HERMES_PRIME_AGENT_WORKER_FORBIDDEN_MAC_ADDRESSES", ()
        )
        violations = validate_no_mac_dependency(
            raw_scan, forbidden_mac_addresses=forbidden
        )
        if violations:
            raise PrimeAgentWorkerConfigError(
                "Prime Agent worker configuration rejected — Mac dependency "
                "detected: " + "; ".join(violations)
            )

        workspace_allowlist = _csv_path_env(
            env, "HERMES_PRIME_AGENT_WORKER_WORKSPACE_ALLOWLIST"
        )

        return cls(
            executable=Path(_require(env, "HERMES_PRIME_AGENT_WORKER_EXECUTABLE")),
            node_bin_dir=Path(_require(env, "HERMES_PRIME_AGENT_WORKER_NODE_BIN_DIR")),
            home_dir=home_dir,
            workspace_allowlist=workspace_allowlist,
            state_dir=_path_env(
                env, "HERMES_PRIME_AGENT_WORKER_STATE_DIR", str(home_dir / "state")
            ),
            cache_dir=_path_env(
                env, "HERMES_PRIME_AGENT_WORKER_CACHE_DIR", str(home_dir / "cache")
            ),
            log_dir=_path_env(
                env, "HERMES_PRIME_AGENT_WORKER_LOG_DIR", str(home_dir / "logs")
            ),
            xdg_data_home=_path_env(
                env,
                "HERMES_PRIME_AGENT_WORKER_XDG_DATA_HOME",
                str(home_dir / ".local" / "share"),
            ),
            provider=env.get(
                "HERMES_PRIME_AGENT_WORKER_PROVIDER", "titan-omniroute"
            ).strip()
            or "titan-omniroute",
            model=env.get("HERMES_PRIME_AGENT_WORKER_MODEL", "lightweight").strip()
            or "lightweight",
            provider_active=_bool_env(
                env, "HERMES_PRIME_AGENT_WORKER_PROVIDER_ACTIVE", False
            ),
            max_turns=_int_env(env, "HERMES_PRIME_AGENT_WORKER_MAX_TURNS", 6),
            max_tokens=_int_env(env, "HERMES_PRIME_AGENT_WORKER_MAX_TOKENS", 20_000),
            timeout_seconds=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_TIMEOUT_SECONDS", 300
            ),
            gate_retries=_int_env(env, "HERMES_PRIME_AGENT_WORKER_GATE_RETRIES", 1),
            gate_timeout_seconds=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_GATE_TIMEOUT_SECONDS", 60
            ),
            max_output_bytes=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_MAX_OUTPUT_BYTES", 200_000
            ),
            max_concurrent_sessions=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_MAX_CONCURRENT_SESSIONS", 1
            ),
            allowed_tools=_csv_env(env, "HERMES_PRIME_AGENT_WORKER_ALLOWED_TOOLS", ()),
            mutation_tools=_csv_env(
                env, "HERMES_PRIME_AGENT_WORKER_MUTATION_TOOLS", ()
            ),
            allowed_network_endpoints=_csv_env(
                env,
                "HERMES_PRIME_AGENT_WORKER_ALLOWED_NETWORK_ENDPOINTS",
                ("http://127.0.0.1:8791",),
            ),
            evidence_retention_days=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_EVIDENCE_RETENTION_DAYS", 30
            ),
            evidence_max_files=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_EVIDENCE_MAX_FILES", 500
            ),
            cooldown_seconds_after_failure=_int_env(
                env, "HERMES_PRIME_AGENT_WORKER_COOLDOWN_SECONDS_AFTER_FAILURE", 30
            ),
            max_consecutive_failures_before_cooldown=_int_env(
                env,
                "HERMES_PRIME_AGENT_WORKER_MAX_CONSECUTIVE_FAILURES_BEFORE_COOLDOWN",
                3,
            ),
            log_level=env.get("HERMES_PRIME_AGENT_WORKER_LOG_LEVEL", "INFO").strip()
            or "INFO",
        )
