"""Governed configuration for the Titan documentation worker.

Mirrors the fail-closed pattern used by
:class:`hermes_cli.prime.omniroute_config.TitanRoutingConfig`: every field
is validated once, in ``__post_init__``/``from_env``, before any collector,
generator, or git operation ever runs. A malformed configuration raises
:class:`DocsWorkerConfigError` and the process exits before touching the
filesystem, the documentation repository, or the network.

The Hermes source checkout is deliberately *not* a hardcoded path -- the
requirement is "discover from configured deployment rather than assuming a
path" -- so :data:`HERMES_SOURCE_DIR` is a required environment variable
(set by the systemd unit's EnvironmentFile, which is itself produced by the
Titan install runbook from the actual deployment) rather than a guess.

The Mac-dependency guard below is intentionally a local copy of
``hermes_cli.prime.omniroute_config.validate_no_mac_dependency`` rather than
an import of it: that module lives on a separate, not-yet-merged branch, and
this worker must not take a hard dependency on unmerged code. Keep the two
copies in sync if the shared policy changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Tuple

# Discovered Mac Tailscale identity (see
# docs/architecture/hydra-ecosystem/evidence/TITAN_DISCOVERY.md and
# MACBOOK_DISCOVERY.md). Always forbidden; operators may only *extend* this
# set via HERMES_DOCS_WORKER_FORBIDDEN_MAC_ADDRESSES, never shrink it.
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

# Fixed, non-configurable naming conventions -- these are part of the
# governance contract itself (exact branch/commit format), not a knob an
# operator should be able to drift.
AUTOMATION_BRANCH_PREFIX = "automation/titan-docs-"
COMMIT_MESSAGE_PREFIX = "Update Titan fleet evidence "

DEFAULT_SYSTEMD_ALLOWLIST: Tuple[str, ...] = (
    "hermes-docs-evidence.service",
    "hermes-docs-daily.service",
    "hermes-docs-weekly.service",
    "ollama.service",
)

DEFAULT_FLEET_NODE_KEYS: Tuple[str, ...] = ("prime", "mac", "hydra_live")
DEFAULT_PR_LABELS: Tuple[str, ...] = ("automation", "titan-docs")

_UNIT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9@:_.\\-]+\.service$")
_REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


class DocsWorkerConfigError(ValueError):
    """A Titan documentation worker configuration is invalid or
    unsafe. Fail closed: raised before any I/O against the vault, the
    Hermes source tree, or the network."""


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise DocsWorkerConfigError(f"{name} is required and must not be blank")
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
        raise DocsWorkerConfigError(f"{name} must be an integer") from error


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise DocsWorkerConfigError(f"{name} must be a boolean (true/false)")


def _csv_env(env: Mapping[str, str], name: str, default: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class DocsWorkerConfig:
    """Immutable, validated configuration for one worker run."""

    hermes_source_dir: Path
    docs_repo_path: Path
    state_dir: Path
    github_repo: str
    git_remote_name: str
    main_branch: str
    git_user_name: str
    git_user_email: str
    ollama_endpoint: str
    ollama_model: str
    ollama_timeout_seconds: int
    systemd_allowlist: Tuple[str, ...]
    extra_filesystem_allowlist: Tuple[Path, ...]
    max_diff_bytes: int
    max_files_changed: int
    min_pr_interval_seconds: int
    max_run_seconds: int
    max_subprocess_seconds: int
    evidence_retention_days: int
    evidence_max_files: int
    pr_labels: Tuple[str, ...]
    fleet_node_keys: Tuple[str, ...]
    hermes_test_evidence_path: Optional[Path] = None
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        for name, value in (
            ("hermes_source_dir", self.hermes_source_dir),
            ("docs_repo_path", self.docs_repo_path),
            ("state_dir", self.state_dir),
        ):
            if not value.is_absolute():
                raise DocsWorkerConfigError(f"{name} must be an absolute path")

        distinct = {self.hermes_source_dir, self.docs_repo_path, self.state_dir}
        if len(distinct) != 3:
            raise DocsWorkerConfigError(
                "hermes_source_dir, docs_repo_path, and state_dir must all be distinct paths"
            )

        if not _REPO_SLUG_PATTERN.match(self.github_repo):
            raise DocsWorkerConfigError(
                "github_repo must be an 'owner/repo' slug (e.g. firecattechllc/hydra-docs)"
            )

        if not self.git_user_name.strip():
            raise DocsWorkerConfigError("git_user_name must not be blank")
        if "@" not in self.git_user_email:
            raise DocsWorkerConfigError("git_user_email must look like an email address")

        if not 1 <= self.ollama_timeout_seconds <= 120:
            raise DocsWorkerConfigError("ollama_timeout_seconds must be between 1 and 120")

        if not self.systemd_allowlist:
            raise DocsWorkerConfigError("systemd_allowlist must declare at least one unit")
        bad_units = tuple(
            unit for unit in self.systemd_allowlist if not _UNIT_NAME_PATTERN.match(unit)
        )
        if bad_units:
            raise DocsWorkerConfigError(
                f"systemd_allowlist contains invalid unit name(s): {bad_units}"
            )

        for path in self.extra_filesystem_allowlist:
            if not path.is_absolute():
                raise DocsWorkerConfigError(
                    f"extra_filesystem_allowlist entry {path} must be absolute"
                )

        if not 1_024 <= self.max_diff_bytes <= 50_000_000:
            raise DocsWorkerConfigError(
                "max_diff_bytes must be between 1024 and 50000000 (Pi5-appropriate ceiling)"
            )
        if not 1 <= self.max_files_changed <= 500:
            raise DocsWorkerConfigError("max_files_changed must be between 1 and 500")
        if not 0 <= self.min_pr_interval_seconds <= 30 * 24 * 3600:
            raise DocsWorkerConfigError(
                "min_pr_interval_seconds must be between 0 and 30 days"
            )
        if not 30 <= self.max_run_seconds <= 3600:
            raise DocsWorkerConfigError("max_run_seconds must be between 30 and 3600")
        if not 1 <= self.max_subprocess_seconds <= 300:
            raise DocsWorkerConfigError(
                "max_subprocess_seconds must be between 1 and 300"
            )
        if self.max_subprocess_seconds > self.max_run_seconds:
            raise DocsWorkerConfigError(
                "max_subprocess_seconds must not exceed max_run_seconds"
            )
        if not 1 <= self.evidence_retention_days <= 365:
            raise DocsWorkerConfigError(
                "evidence_retention_days must be between 1 and 365"
            )
        if not 10 <= self.evidence_max_files <= 100_000:
            raise DocsWorkerConfigError(
                "evidence_max_files must be between 10 and 100000"
            )
        if not self.fleet_node_keys:
            raise DocsWorkerConfigError(
                "fleet_node_keys must declare at least one natural key to observe"
            )

        violations = validate_no_mac_dependency(self._mac_scan_values())
        if violations:
            raise DocsWorkerConfigError(
                "Titan documentation worker configuration rejected — Mac dependency "
                "detected: " + "; ".join(violations)
            )

    def _mac_scan_values(self) -> Mapping[str, Optional[str]]:
        return {
            "hermes_source_dir": str(self.hermes_source_dir),
            "docs_repo_path": str(self.docs_repo_path),
            "state_dir": str(self.state_dir),
            "ollama_endpoint": self.ollama_endpoint,
            "extra_filesystem_allowlist": ",".join(
                str(p) for p in self.extra_filesystem_allowlist
            ),
        }

    def filesystem_allowlist(self) -> Tuple[Path, ...]:
        """Every path a collector or generator may read or write this run."""
        return (
            self.hermes_source_dir,
            self.docs_repo_path,
            self.state_dir,
        ) + self.extra_filesystem_allowlist

    def is_within_allowlist(self, path: Path) -> bool:
        """True only if ``path`` is inside one of :meth:`filesystem_allowlist`'s
        roots. Enforced at every write boundary (see
        :mod:`hermes_docs_worker.orchestrator`) so a future bug in a path
        computation fails closed rather than writing outside the governed
        checkout/state directories."""
        resolved = path.resolve()
        for root in self.filesystem_allowlist():
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "DocsWorkerConfig":
        import os

        env = env if env is not None else os.environ

        raw_scan = {
            "HERMES_DOCS_WORKER_HERMES_SOURCE_DIR": env.get(
                "HERMES_DOCS_WORKER_HERMES_SOURCE_DIR"
            ),
            "HERMES_DOCS_WORKER_OLLAMA_ENDPOINT": env.get(
                "HERMES_DOCS_WORKER_OLLAMA_ENDPOINT"
            ),
        }
        forbidden = DEFAULT_FORBIDDEN_MAC_ADDRESSES + _csv_env(
            env, "HERMES_DOCS_WORKER_FORBIDDEN_MAC_ADDRESSES", ()
        )
        violations = validate_no_mac_dependency(raw_scan, forbidden_mac_addresses=forbidden)
        if violations:
            raise DocsWorkerConfigError(
                "Titan documentation worker configuration rejected — Mac dependency "
                "detected: " + "; ".join(violations)
            )

        test_evidence_raw = env.get(
            "HERMES_DOCS_WORKER_HERMES_TEST_EVIDENCE_PATH", ""
        ).strip()

        return cls(
            hermes_source_dir=Path(
                _require(env, "HERMES_DOCS_WORKER_HERMES_SOURCE_DIR")
            ),
            docs_repo_path=_path_env(
                env, "HERMES_DOCS_WORKER_DOCS_REPO_PATH", "/opt/hermes-docs/hydra-docs"
            ),
            state_dir=_path_env(
                env, "HERMES_DOCS_WORKER_STATE_DIR", "/var/lib/hermes-docs-worker"
            ),
            github_repo=_require(env, "HERMES_DOCS_WORKER_GITHUB_REPO"),
            git_remote_name=env.get("HERMES_DOCS_WORKER_GIT_REMOTE", "origin").strip()
            or "origin",
            main_branch=env.get("HERMES_DOCS_WORKER_MAIN_BRANCH", "main").strip()
            or "main",
            git_user_name=env.get(
                "HERMES_DOCS_WORKER_GIT_USER_NAME", "Titan Docs Worker"
            ).strip()
            or "Titan Docs Worker",
            git_user_email=env.get(
                "HERMES_DOCS_WORKER_GIT_USER_EMAIL", "titan-docs-worker@hermes.local"
            ).strip()
            or "titan-docs-worker@hermes.local",
            ollama_endpoint=env.get(
                "HERMES_DOCS_WORKER_OLLAMA_ENDPOINT", "http://127.0.0.1:11434"
            ).strip()
            or "http://127.0.0.1:11434",
            ollama_model=env.get("HERMES_DOCS_WORKER_OLLAMA_MODEL", "gemma3:4b").strip()
            or "gemma3:4b",
            ollama_timeout_seconds=_int_env(
                env, "HERMES_DOCS_WORKER_OLLAMA_TIMEOUT_SECONDS", 30
            ),
            systemd_allowlist=_csv_env(
                env, "HERMES_DOCS_WORKER_SYSTEMD_ALLOWLIST", DEFAULT_SYSTEMD_ALLOWLIST
            ),
            extra_filesystem_allowlist=tuple(
                Path(p)
                for p in _csv_env(
                    env, "HERMES_DOCS_WORKER_EXTRA_FILESYSTEM_ALLOWLIST", ()
                )
            ),
            max_diff_bytes=_int_env(env, "HERMES_DOCS_WORKER_MAX_DIFF_BYTES", 200_000),
            max_files_changed=_int_env(
                env, "HERMES_DOCS_WORKER_MAX_FILES_CHANGED", 25
            ),
            min_pr_interval_seconds=_int_env(
                env, "HERMES_DOCS_WORKER_MIN_PR_INTERVAL_SECONDS", 21_600
            ),
            max_run_seconds=_int_env(env, "HERMES_DOCS_WORKER_MAX_RUN_SECONDS", 600),
            max_subprocess_seconds=_int_env(
                env, "HERMES_DOCS_WORKER_MAX_SUBPROCESS_SECONDS", 30
            ),
            evidence_retention_days=_int_env(
                env, "HERMES_DOCS_WORKER_EVIDENCE_RETENTION_DAYS", 30
            ),
            evidence_max_files=_int_env(
                env, "HERMES_DOCS_WORKER_EVIDENCE_MAX_FILES", 500
            ),
            pr_labels=_csv_env(env, "HERMES_DOCS_WORKER_PR_LABELS", DEFAULT_PR_LABELS),
            fleet_node_keys=_csv_env(
                env, "HERMES_DOCS_WORKER_FLEET_NODE_KEYS", DEFAULT_FLEET_NODE_KEYS
            ),
            hermes_test_evidence_path=(
                Path(test_evidence_raw) if test_evidence_raw else None
            ),
            log_level=env.get("HERMES_DOCS_WORKER_LOG_LEVEL", "INFO").strip() or "INFO",
        )
