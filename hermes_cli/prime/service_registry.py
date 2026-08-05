"""Ecosystem service registry.

Fleet Unification ecosystem-services work. Repository-wide discovery (see
``docs/beta/post-phase9/`` and this branch's own discovery pass) found that
none of the ecosystem services this module tracks — Paperclip, Buzz Relay,
Buzznode, the Hermes WebUI operator-dashboard adapter, Hermes Wiki, Agent
Reach, Self-Evolution, and the ecosystem discovery catalog — exist as real
external repositories. Every one of them is instead a complete, tested,
disabled-by-default local *data model* already committed to ``main`` under
``apps/sigil/src/sigil/*.py``, built during a provisional planning exercise
whose own README states: *"These external systems have no independent
execution authority and remain disabled by default"* and that
post-Phase-9 runtime integration "may [not] begin until the live-node gates
are completed and recorded."

This module never enables, activates, or grants execution authority to any
of them. Its job is narrower and entirely safe: make each one's *real,
introspected* presence and disabled state visible to Prime and Mission
Control, so an operator can see "Paperclip is present, its code imports
cleanly, and its default configuration is confirmed disabled" instead of
either (a) having no visibility into these modules at all, or (b) a
dashboard falsely implying they are live services.

:func:`discover_service` never trusts documentation or a hardcoded belief
that a module is disabled — it actually imports the module, constructs its
config class with defaults, and reads the real ``enabled`` field and every
``can_*`` capability property. A module that has drifted to
``enabled=True`` (or any ``can_*`` property returning ``True``) is
classified :class:`ServiceInstallationStatus.UNSAFE`, not silently trusted.

:data:`KNOWN_ECOSYSTEM_SERVICES` is a closed catalog — :func:`register_known_service`
only accepts a ``service_key`` already in that table. This is a deliberate
choice mirroring :data:`hermes_cli.prime.fleet_registry.KNOWN_FLEET_NODES`:
there is no code path by which an arbitrary, unverified service name or
module path can be registered as if it were a known, reviewed component.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only lock, matches evidence.py
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.prime.identity import (
    FleetIdentity,
    IdentityKind,
    IdentitySource,
)

SERVICE_REGISTRY_SCHEMA_VERSION = 1
SUPPORTED_SERVICE_REGISTRY_SCHEMA_VERSIONS = frozenset({1})


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_SERVICE_REGISTRY_SCHEMA_VERSIONS:
        raise ValueError(
            f"service registry schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_SERVICE_REGISTRY_SCHEMA_VERSIONS)})"
        )
    return version


class ServiceRegistryError(RuntimeError):
    """Durable service registry state failed closed."""


class EcosystemServiceCategory(str, Enum):
    BUILDER_WORKER = "builder_worker"
    MESSAGING_RELAY = "messaging_relay"
    WORKER_HOST = "worker_host"
    OPERATOR_DASHBOARD = "operator_dashboard"
    KNOWLEDGE = "knowledge"
    EXTERNAL_COMMS = "external_comms"
    SELF_IMPROVEMENT = "self_improvement"
    DISCOVERY_CATALOG = "discovery_catalog"


class ServiceInstallationStatus(str, Enum):
    PRESENT_DISABLED = "present_disabled"
    AVAILABLE_VERIFIED = "available_verified"
    NOT_FOUND = "not_found"
    REFERENCED_UNAVAILABLE = "referenced_unavailable"
    AMBIGUOUS = "ambiguous"
    OBSOLETE = "obsolete"
    UNSAFE = "unsafe"


class ServiceDiscoveryOutcome(str, Enum):
    VERIFIED_PRESENT_DISABLED = "verified_present_disabled"
    IMPORT_FAILED = "import_failed"
    SHAPE_MISMATCH = "shape_mismatch"
    UNEXPECTEDLY_ENABLED = "unexpectedly_enabled"


class EcosystemServiceDescriptor(BaseModel):
    """A hand-curated description of one known ecosystem service — what to
    look for, never a claim about what was found. Discovery independently
    verifies every field this table implies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_key: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    category: EcosystemServiceCategory
    module_path: Optional[str] = Field(default=None, max_length=256)
    config_class_name: Optional[str] = Field(default=None, max_length=128)
    enabled_field: str = Field(default="enabled", max_length=64)
    certification_gate: str = Field(
        default="phase9_live_node_certification", max_length=128
    )
    notes: str = Field(default="", max_length=2048)


KNOWN_ECOSYSTEM_SERVICES: Tuple[EcosystemServiceDescriptor, ...] = (
    EcosystemServiceDescriptor(
        service_key="paperclip",
        display_name="Paperclip",
        category=EcosystemServiceCategory.BUILDER_WORKER,
        module_path="sigil.paperclip_adapter",
        config_class_name="PaperclipAdapterConfig",
        notes=(
            "Organizational/assignment adapter modeling companies, agents, "
            "issues, and heartbeats. No verified external Paperclip "
            "repository exists anywhere in this repo or the firecattechllc "
            "GitHub org; this is a local, disabled data model only."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="buzz_relay",
        display_name="Buzz Relay",
        category=EcosystemServiceCategory.MESSAGING_RELAY,
        module_path="sigil.buzz_relay_adapter",
        config_class_name="BuzzRelayConfig",
        notes=(
            "Signed-event relay/messaging adapter with hash-chained replay-"
            "window modeling. No verified external Buzz repository exists."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="buzznode",
        display_name="Buzznode",
        category=EcosystemServiceCategory.WORKER_HOST,
        module_path="sigil.buzznode_adapter",
        config_class_name="BuzznodeAdapterConfig",
        notes=(
            "Persistent isolated worker-host adapter. This is the closest "
            "local model to a generic 'Hermes Node' worker-runtime concept "
            "— no distinct 'Hermes Node' project exists anywhere in this "
            "repository or the firecattechllc GitHub org."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="hermes_webui_adapter",
        display_name="Hermes WebUI (operator dashboard adapter)",
        category=EcosystemServiceCategory.OPERATOR_DASHBOARD,
        module_path="sigil.hermes_webui_adapter",
        config_class_name=None,
        enabled_field="enabled",
        notes=(
            "Distinct from the already-shipping browser chat UI persona in "
            "agent/prompt_builder.py. Models Titan/Mac operator-dashboard "
            "targets; both hardcoded default targets are disabled."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="hermes_wiki",
        display_name="Hermes Wiki",
        category=EcosystemServiceCategory.KNOWLEDGE,
        module_path="sigil.hermes_wiki_adapter",
        config_class_name="HermesWikiConfig",
        notes=(
            "Knowledge/document adapter with provenance, citation, and "
            "index modeling. No verified external Wiki repository exists."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="agent_reach",
        display_name="Agent Reach",
        category=EcosystemServiceCategory.EXTERNAL_COMMS,
        module_path="sigil.agent_reach_adapter",
        config_class_name="AgentReachConfig",
        notes=(
            "External agent-outreach adapter. No verified external Agent "
            "Reach repository exists; repo-local docs describe upstream "
            "Agent Reach only in prose (selector/installer/router over "
            "tools like Jina Reader, yt-dlp, gh), with no URL."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="self_evolution",
        display_name="Hermes Self-Evolution",
        category=EcosystemServiceCategory.SELF_IMPROVEMENT,
        module_path="sigil.self_evolution",
        config_class_name="EvolutionFrameworkConfig",
        notes=(
            "Non-executing improvement-proposal framework with a working "
            "independent-review self-approval guard "
            "(assess_promotion_readiness excludes the proposer's own "
            "identity from the reviewer set)."
        ),
    ),
    EcosystemServiceDescriptor(
        service_key="ecosystem_catalog",
        display_name="Ecosystem Discovery Catalog",
        category=EcosystemServiceCategory.DISCOVERY_CATALOG,
        module_path="sigil.ecosystem_catalog",
        config_class_name="EcosystemCatalogConfig",
        notes=(
            "Models externally-supplied discovery evidence. No verified "
            "'Awesome Hermes Agent' external list/repository exists "
            "anywhere in this repo; this catalog is the closest local "
            "analog and is explicitly documented elsewhere in this repo "
            "as 'discovery input, never an install allowlist.'"
        ),
    ),
)

_KNOWN_SERVICE_BY_KEY: Dict[str, EcosystemServiceDescriptor] = {
    d.service_key: d for d in KNOWN_ECOSYSTEM_SERVICES
}


def import_ecosystem_module(module_path: str):
    """Import a ``sigil.*`` ecosystem-adapter module, in-process.

    The ``sigil`` package lives in ``apps/sigil/src`` — a separate
    installable package within this monorepo, not on the repo-root
    interpreter's default path. This mirrors exactly the fallback
    ``hermes_cli.prime.certification.run_stage1_regression`` already uses
    for the same reason (there for a subprocess's ``PYTHONPATH``; here for
    this process's ``sys.path``, since real in-process introspection of the
    module's classes — not just running a script — is the whole point of
    discovery). Only ever adds the path, only once, and only as a fallback
    after a direct import genuinely failed.
    """
    try:
        return importlib.import_module(module_path)
    except ImportError:
        if not module_path.startswith("sigil"):
            raise
        sigil_src = Path(__file__).resolve().parents[2] / "apps" / "sigil" / "src"
        if not sigil_src.is_dir():
            raise
        if str(sigil_src) not in sys.path:
            sys.path.insert(0, str(sigil_src))
        return importlib.import_module(module_path)


class ServiceDiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service_key: str = Field(..., min_length=1, max_length=64)
    outcome: ServiceDiscoveryOutcome
    installation_status: ServiceInstallationStatus
    module_path: Optional[str] = Field(default=None, max_length=256)
    resolved_module_file: Optional[str] = Field(default=None, max_length=1024)
    enabled_default_confirmed_false: Optional[bool] = None
    capability_denials_confirmed: Tuple[str, ...] = ()
    detail: str = Field(default="", max_length=2048)
    checked_at: int = Field(..., ge=0)


def _capability_denials(instance: object) -> Tuple[Tuple[str, ...], bool]:
    """Return (sorted can_* attribute names, all_false) for one instance."""
    denials: List[str] = []
    all_false = True
    for attr in dir(instance):
        if not attr.startswith("can_"):
            continue
        try:
            value = getattr(instance, attr)
        except Exception:  # noqa: BLE001 - a broken property is not a safe grant
            continue
        if callable(value):
            continue
        if value is True:
            all_false = False
        elif value is False:
            denials.append(attr)
    return tuple(sorted(set(denials))), all_false


def discover_service(
    descriptor: EcosystemServiceDescriptor, *, now: int
) -> ServiceDiscoveryResult:
    """Actually import the described module and verify its real state.

    Never trusts :data:`KNOWN_ECOSYSTEM_SERVICES` or any documentation —
    every field on the returned result is derived from a real
    ``importlib.import_module`` call and real attribute reads.
    """
    if descriptor.module_path is None:
        return ServiceDiscoveryResult(
            service_key=descriptor.service_key,
            outcome=ServiceDiscoveryOutcome.IMPORT_FAILED,
            installation_status=ServiceInstallationStatus.NOT_FOUND,
            module_path=None,
            detail="no local module path is known for this service",
            checked_at=now,
        )

    try:
        module = import_ecosystem_module(descriptor.module_path)
    except ImportError as error:
        return ServiceDiscoveryResult(
            service_key=descriptor.service_key,
            outcome=ServiceDiscoveryOutcome.IMPORT_FAILED,
            installation_status=ServiceInstallationStatus.NOT_FOUND,
            module_path=descriptor.module_path,
            detail=f"import failed: {error}",
            checked_at=now,
        )

    resolved_file = getattr(module, "__file__", None)

    targets: List[object]
    if descriptor.config_class_name is not None:
        config_cls = getattr(module, descriptor.config_class_name, None)
        if config_cls is None:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
                installation_status=ServiceInstallationStatus.AMBIGUOUS,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                detail=(
                    f"expected class {descriptor.config_class_name!r} "
                    "not found on module"
                ),
                checked_at=now,
            )
        try:
            targets = [config_cls()]
        except TypeError as error:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
                installation_status=ServiceInstallationStatus.AMBIGUOUS,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                detail=(
                    f"could not construct {descriptor.config_class_name} "
                    f"with defaults: {error}"
                ),
                checked_at=now,
            )
    elif descriptor.service_key == "hermes_webui_adapter":
        targets_fn = getattr(module, "default_hermes_webui_targets", None)
        if targets_fn is None:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
                installation_status=ServiceInstallationStatus.AMBIGUOUS,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                detail="expected function default_hermes_webui_targets not found",
                checked_at=now,
            )
        try:
            targets = list(targets_fn())
        except Exception as error:  # noqa: BLE001
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
                installation_status=ServiceInstallationStatus.AMBIGUOUS,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                detail=f"default_hermes_webui_targets() raised: {error}",
                checked_at=now,
            )
        if not targets:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
                installation_status=ServiceInstallationStatus.AMBIGUOUS,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                detail="default_hermes_webui_targets() returned no targets",
                checked_at=now,
            )
    else:
        return ServiceDiscoveryResult(
            service_key=descriptor.service_key,
            outcome=ServiceDiscoveryOutcome.SHAPE_MISMATCH,
            installation_status=ServiceInstallationStatus.AMBIGUOUS,
            module_path=descriptor.module_path,
            resolved_module_file=resolved_file,
            detail="descriptor has neither a config_class_name nor a known special case",
            checked_at=now,
        )

    all_denials: List[str] = []
    for target in targets:
        enabled_value = getattr(target, descriptor.enabled_field, None)
        if enabled_value is not False:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.UNEXPECTEDLY_ENABLED,
                installation_status=ServiceInstallationStatus.UNSAFE,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                enabled_default_confirmed_false=False,
                detail=(
                    f"{descriptor.enabled_field!r} on a default-constructed "
                    f"instance is {enabled_value!r}, not False — treating "
                    "as unsafe rather than trusting documentation"
                ),
                checked_at=now,
            )
        denials, all_false = _capability_denials(target)
        if not all_false:
            return ServiceDiscoveryResult(
                service_key=descriptor.service_key,
                outcome=ServiceDiscoveryOutcome.UNEXPECTEDLY_ENABLED,
                installation_status=ServiceInstallationStatus.UNSAFE,
                module_path=descriptor.module_path,
                resolved_module_file=resolved_file,
                enabled_default_confirmed_false=True,
                detail="a can_* capability property returned True on a default instance",
                checked_at=now,
            )
        all_denials.extend(denials)

    return ServiceDiscoveryResult(
        service_key=descriptor.service_key,
        outcome=ServiceDiscoveryOutcome.VERIFIED_PRESENT_DISABLED,
        installation_status=ServiceInstallationStatus.PRESENT_DISABLED,
        module_path=descriptor.module_path,
        resolved_module_file=resolved_file,
        enabled_default_confirmed_false=True,
        capability_denials_confirmed=tuple(sorted(set(all_denials))),
        detail=(
            "module imported successfully; default configuration confirmed "
            "disabled with every capability grant denied"
        ),
        checked_at=now,
    )


# ── Verified external sources (future use — no entries exist today) ────────


_GITHUB_OR_GITLAB_URL = re.compile(
    r"^https://(github|gitlab)\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+/?$"
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class VerifiedExternalSource(BaseModel):
    """Provenance for a service installed from a real, reviewed external
    repository. No entries of this type exist in :data:`KNOWN_ECOSYSTEM_SERVICES`
    today — every target service turned out to have no verifiable external
    repository — but the validation this type enforces is real and used by
    :func:`validate_external_source`, so a future genuinely-verified service
    has a concrete, tested gate to pass through rather than an ad hoc one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repository_url: str = Field(..., min_length=1, max_length=512)
    revision: str = Field(..., min_length=7, max_length=40)
    license_spdx_id: str = Field(..., min_length=1, max_length=64)
    integrity_sha256: str = Field(..., min_length=64, max_length=64)
    verified_by: str = Field(..., min_length=1, max_length=256)
    verified_at: int = Field(..., ge=0)

    @field_validator("repository_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not _GITHUB_OR_GITLAB_URL.match(v):
            raise ValueError(
                "repository_url must be a plain https github.com or gitlab.com "
                "repository URL"
            )
        return v

    @field_validator("revision")
    @classmethod
    def _check_revision(cls, v: str) -> str:
        if not _COMMIT_SHA.match(v):
            raise ValueError("revision must be a pinned commit SHA (hex, 7-40 chars)")
        return v

    @field_validator("integrity_sha256")
    @classmethod
    def _check_integrity(cls, v: str) -> str:
        if not re.match(r"^[0-9a-f]{64}$", v):
            raise ValueError("integrity_sha256 must be a lowercase 64-char hex digest")
        return v


REJECTED_LICENSE_IDENTIFIERS = frozenset({
    "unlicense-unclear", "proprietary-no-grant", "unknown", "",
})


def validate_external_source(
    source: Optional[VerifiedExternalSource],
) -> Tuple[bool, Optional[str]]:
    """Deterministically decide whether an external source is admissible.

    ``None`` (no external source claimed — the correct value for every
    service in :data:`KNOWN_ECOSYSTEM_SERVICES` today) is always rejected
    for the purposes of :func:`register_external_service` — this function
    exists to gate a *claimed* external source, not to permit skipping
    provenance entirely.
    """
    if source is None:
        return False, "no_external_source_supplied"
    if source.license_spdx_id.strip().lower() in REJECTED_LICENSE_IDENTIFIERS:
        return False, "unclear_or_incompatible_license"
    return True, None


# ── Durable registry ─────────────────────────────────────────────────────────


class ServiceRegistrationOutcome(str, Enum):
    REGISTERED = "registered"
    UPDATED = "updated"
    REJECTED = "rejected"


class ServiceRegistrationRejectionCode(str, Enum):
    UNKNOWN_SERVICE_KEY = "unknown_service_key"
    DUPLICATE_REGISTRATION = "duplicate_registration"
    REVOKED = "revoked"
    UNVERIFIED_EXTERNAL_SOURCE = "unverified_external_source"


class ServiceRecord(BaseModel):
    """The durable, current-state record for one ecosystem service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=SERVICE_REGISTRY_SCHEMA_VERSION)
    identity_id: str = Field(..., min_length=1, max_length=128)
    service_key: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    category: EcosystemServiceCategory
    installation_status: ServiceInstallationStatus
    discovery_outcome: ServiceDiscoveryOutcome
    module_path: Optional[str] = Field(default=None, max_length=256)
    external_source: Optional[VerifiedExternalSource] = None
    certification_gate: str = Field(..., max_length=128)
    certification_gate_met: bool = False
    capability_denials_confirmed: Tuple[str, ...] = ()
    revoked: bool = False
    revoked_at: Optional[int] = Field(default=None, ge=0)
    revocation_reason: Optional[str] = Field(default=None, max_length=512)
    registered_at: int = Field(..., ge=0)
    updated_at: int = Field(..., ge=0)
    last_checked_at: int = Field(..., ge=0)
    notes: str = Field(default="", max_length=2048)

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        return _validate_schema(v)

    @model_validator(mode="after")
    def _revocation_consistency(self) -> "ServiceRecord":
        if self.revoked and self.revoked_at is None:
            raise ValueError("a revoked service record must record revoked_at")
        if not self.revoked and self.revoked_at is not None:
            raise ValueError("a non-revoked service record cannot carry revoked_at")
        return self

    def is_dispatchable(self) -> bool:
        """Fail-closed: True only for a non-revoked, verified-and-certified
        external service. Every service in :data:`KNOWN_ECOSYSTEM_SERVICES`
        today is ``PRESENT_DISABLED`` with ``certification_gate_met=False``,
        so this is always False for the current catalog — by policy, not
        by omission."""
        return (
            not self.revoked
            and self.installation_status == ServiceInstallationStatus.AVAILABLE_VERIFIED
            and self.certification_gate_met
        )


def _default_state_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "prime"


class EcosystemServiceRegistryStore:
    """Durable, atomically-written, keyed store for :class:`ServiceRecord`.

    Mirrors :class:`hermes_cli.prime.fleet_registry.FleetRegistryStore`'s
    single-JSON-document-with-atomic-rewrite-under-fcntl-lock pattern.
    """

    def __init__(self, state_root: Optional[Path] = None) -> None:
        root = state_root if state_root is not None else _default_state_root()
        if not root.is_absolute():
            raise ServiceRegistryError("service registry state root must be an absolute path")
        if root.is_symlink():
            raise ServiceRegistryError("service registry state root cannot be a symlink")

        self.directory = root / "service-registry-v1"
        self.records_path = self.directory / "services.json"
        self.lock_path = self.directory / "services.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise ServiceRegistryError("service registry directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise ServiceRegistryError("service registry lock cannot be a symlink")

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _read_unlocked(self) -> Dict[str, ServiceRecord]:
        if not self.records_path.exists():
            return {}
        if self.records_path.is_symlink():
            raise ServiceRegistryError("service registry records file cannot be a symlink")
        try:
            raw = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ServiceRegistryError("service registry records file is unreadable") from error
        if not isinstance(raw, dict):
            raise ServiceRegistryError("service registry records file shape is invalid")
        try:
            return {key: ServiceRecord(**value) for key, value in raw.items()}
        except Exception as error:  # noqa: BLE001 - fail closed on any malformed record
            raise ServiceRegistryError(
                "service registry records file contains an invalid record"
            ) from error

    def _write_unlocked(self, records: Dict[str, ServiceRecord]) -> None:
        payload = {key: record.model_dump(mode="json") for key, record in records.items()}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.directory), prefix=".services.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.records_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def get(self, service_key: str) -> Optional[ServiceRecord]:
        with self._lock():
            return self._read_unlocked().get(service_key)

    def all(self) -> Tuple[ServiceRecord, ...]:
        with self._lock():
            return tuple(self._read_unlocked().values())

    def put(self, record: ServiceRecord) -> ServiceRecord:
        with self._lock():
            records = self._read_unlocked()
            records[record.service_key] = record
            self._write_unlocked(records)
        return record


def _derive_service_identity(service_key: str, *, registered_at: int) -> FleetIdentity:
    return FleetIdentity(
        kind=IdentityKind.SERVICE,
        natural_key=service_key,
        source=IdentitySource.NATIVE,
        source_reference=f"prime_service_registry:{service_key}",
        registered_at=registered_at,
    )


class EcosystemServiceRegistry:
    """Governed, fail-closed ecosystem-service registration and lookup."""

    def __init__(self, store: Optional[EcosystemServiceRegistryStore] = None) -> None:
        self._store = store or EcosystemServiceRegistryStore()

    def register_known_service(
        self, service_key: str, *, now: int, allow_reregistration: bool = False
    ) -> Tuple[ServiceRegistrationOutcome, Optional[ServiceRecord], Optional[ServiceRegistrationRejectionCode]]:
        """Register (or refresh) one service from the closed
        :data:`KNOWN_ECOSYSTEM_SERVICES` catalog. Rejects any key not in
        that catalog outright — this is the "unverified repository/service
        rejection" gate: nothing outside the reviewed table can ever be
        registered through this path."""
        descriptor = _KNOWN_SERVICE_BY_KEY.get(service_key)
        if descriptor is None:
            return ServiceRegistrationOutcome.REJECTED, None, ServiceRegistrationRejectionCode.UNKNOWN_SERVICE_KEY

        existing = self._store.get(service_key)
        if existing is not None:
            if existing.revoked:
                return ServiceRegistrationOutcome.REJECTED, None, ServiceRegistrationRejectionCode.REVOKED
            if not allow_reregistration:
                return (
                    ServiceRegistrationOutcome.REJECTED,
                    None,
                    ServiceRegistrationRejectionCode.DUPLICATE_REGISTRATION,
                )

        result = discover_service(descriptor, now=now)
        identity = _derive_service_identity(service_key, registered_at=now)
        record = ServiceRecord(
            identity_id=identity.identity_id,
            service_key=service_key,
            display_name=descriptor.display_name,
            category=descriptor.category,
            installation_status=result.installation_status,
            discovery_outcome=result.outcome,
            module_path=result.module_path,
            certification_gate=descriptor.certification_gate,
            certification_gate_met=False,
            capability_denials_confirmed=result.capability_denials_confirmed,
            registered_at=existing.registered_at if existing is not None else now,
            updated_at=now,
            last_checked_at=now,
            notes=descriptor.notes,
        )
        self._store.put(record)
        outcome = (
            ServiceRegistrationOutcome.UPDATED
            if existing is not None
            else ServiceRegistrationOutcome.REGISTERED
        )
        return outcome, record, None

    def register_external_service(
        self,
        service_key: str,
        *,
        external_source: Optional[VerifiedExternalSource],
        now: int,
    ) -> Tuple[ServiceRegistrationOutcome, Optional[ServiceRecord], Optional[ServiceRegistrationRejectionCode]]:
        """Register a service claimed to come from a verified external
        repository. No entry in :data:`KNOWN_ECOSYSTEM_SERVICES` uses this
        path today (none has a verified external repository) — this exists
        so a future genuinely-verified service has a real gate to pass."""
        descriptor = _KNOWN_SERVICE_BY_KEY.get(service_key)
        if descriptor is None:
            return ServiceRegistrationOutcome.REJECTED, None, ServiceRegistrationRejectionCode.UNKNOWN_SERVICE_KEY

        ok, _reason = validate_external_source(external_source)
        if not ok or external_source is None:
            return (
                ServiceRegistrationOutcome.REJECTED,
                None,
                ServiceRegistrationRejectionCode.UNVERIFIED_EXTERNAL_SOURCE,
            )

        identity = _derive_service_identity(service_key, registered_at=now)
        record = ServiceRecord(
            identity_id=identity.identity_id,
            service_key=service_key,
            display_name=descriptor.display_name,
            category=descriptor.category,
            installation_status=ServiceInstallationStatus.AVAILABLE_VERIFIED,
            discovery_outcome=ServiceDiscoveryOutcome.VERIFIED_PRESENT_DISABLED,
            module_path=descriptor.module_path,
            external_source=external_source,
            certification_gate=descriptor.certification_gate,
            certification_gate_met=False,
            registered_at=now,
            updated_at=now,
            last_checked_at=now,
            notes=descriptor.notes,
        )
        self._store.put(record)
        return ServiceRegistrationOutcome.REGISTERED, record, None

    def revoke(self, service_key: str, *, now: int, reason: str) -> ServiceRecord:
        existing = self._store.get(service_key)
        if existing is None:
            raise ServiceRegistryError(f"cannot revoke unknown service {service_key!r}")
        if existing.revoked:
            return existing
        record = existing.model_copy(
            update={
                "revoked": True,
                "revoked_at": now,
                "revocation_reason": reason,
                "updated_at": now,
            }
        )
        return self._store.put(record)

    def get(self, service_key: str) -> Optional[ServiceRecord]:
        return self._store.get(service_key)

    def all(self) -> Tuple[ServiceRecord, ...]:
        return self._store.all()

    def register_all_known_services(self, *, now: int) -> Tuple[ServiceRecord, ...]:
        """Convenience: register/refresh every entry in
        :data:`KNOWN_ECOSYSTEM_SERVICES` in one call."""
        records = []
        for descriptor in KNOWN_ECOSYSTEM_SERVICES:
            _outcome, record, _rejection = self.register_known_service(
                descriptor.service_key, now=now, allow_reregistration=True
            )
            if record is not None:
                records.append(record)
        return tuple(records)
