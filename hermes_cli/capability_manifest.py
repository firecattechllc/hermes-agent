"""Capability activation manifest.

A machine-readable record of every Hermes capability's activation state for
this specific FireCat deployment (Titan + Mac, Tailscale-linked, Telegram-
first). The manifest data lives in ``docs/capability-manifest.yaml``; this
module is its schema and loader.

Every entry records not just a state but *how that state was determined* —
``validation_command`` is the exact command someone can re-run to confirm
the classification still holds, and ``implementation_paths`` point at the
real code backing the capability. This mirrors the rest of the repository's
convention (Prime, Mission Control) of never asserting a status without
attached evidence: a capability manifest that just says "active" with no
way to check it would be worth nothing.

States, and what each one means for this deployment:

- ``active``: implemented, configured, and reachable right now.
- ``available``: implemented and safe, but not yet wired into a reachable
  code path or not yet configured for this deployment.
- ``blocked_credentials``: implemented and reachable, but needs a secret
  (API key, token, OAuth) that is not present in this environment.
- ``blocked_runtime``: implemented, but needs infrastructure this
  deployment does not yet have running (e.g. the Mac coordinator being
  online, a local Docker daemon, Modal CLI auth).
- ``not_selected``: implemented and available, but deliberately excluded
  from this deployment (either irrelevant to the target architecture or
  unsafe to enable without a dedicated approval policy this deployment
  does not yet define).
- ``failed_validation``: was expected to work but the validation command
  did not pass when last checked.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CAPABILITY_MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_CAPABILITY_MANIFEST_SCHEMA_VERSIONS = frozenset({1})

DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "capability-manifest.yaml"
)


class CapabilityManifestError(ValueError):
    """The capability manifest failed to load or validate."""


class CapabilityState(str, Enum):
    ACTIVE = "active"
    AVAILABLE = "available"
    BLOCKED_CREDENTIALS = "blocked_credentials"
    BLOCKED_RUNTIME = "blocked_runtime"
    NOT_SELECTED = "not_selected"
    FAILED_VALIDATION = "failed_validation"


class CapabilityCategory(str, Enum):
    CORE_TOOL = "core_tool"
    GOVERNANCE = "governance"
    COMMUNICATION = "communication"
    FLEET = "fleet"


class CapabilityNode(str, Enum):
    TITAN = "titan"
    MAC = "mac"
    SHARED = "shared"


class CapabilityEntry(BaseModel):
    """One capability's recorded activation state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    category: CapabilityCategory
    state: CapabilityState
    reason: str = Field(..., min_length=1, max_length=1024)
    validation_command: Optional[str] = Field(default=None, max_length=512)
    implementation_paths: Tuple[str, ...] = ()
    requires_credentials: Tuple[str, ...] = ()
    node: Optional[CapabilityNode] = None
    notes: str = Field(default="", max_length=2048)

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum() or v != v.lower():
            raise ValueError(f"capability key must be lowercase snake_case: {v!r}")
        return v


class CapabilityManifest(BaseModel):
    """The full manifest: every capability considered for this deployment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=CAPABILITY_MANIFEST_SCHEMA_VERSION)
    generated_at: str = Field(..., min_length=1, max_length=32)
    deployment: str = Field(default="firecat-hermes", max_length=128)
    entries: Tuple[CapabilityEntry, ...]

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v not in SUPPORTED_CAPABILITY_MANIFEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"capability manifest schema version {v} not supported "
                f"(supported: {sorted(SUPPORTED_CAPABILITY_MANIFEST_SCHEMA_VERSIONS)})"
            )
        return v

    @model_validator(mode="after")
    def _unique_keys(self) -> "CapabilityManifest":
        keys = [e.key for e in self.entries]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(f"duplicate capability keys: {sorted(duplicates)}")
        return self

    def by_state(self, state: CapabilityState) -> Tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.state == state)

    def by_category(self, category: CapabilityCategory) -> Tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.category == category)

    def by_node(self, node: CapabilityNode) -> Tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.node == node)

    def find(self, key: str) -> Optional[CapabilityEntry]:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def counts_by_state(self) -> Dict[str, int]:
        counts = {state.value: 0 for state in CapabilityState}
        for entry in self.entries:
            counts[entry.state.value] += 1
        return counts


def load_capability_manifest(path: Optional[Path] = None) -> CapabilityManifest:
    """Load and schema-validate the capability manifest. Fail closed on any
    malformed or unreadable manifest — never returns a partially-loaded
    result."""
    manifest_path = path or DEFAULT_MANIFEST_PATH
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CapabilityManifestError(
            f"cannot read capability manifest at {manifest_path}: {error}"
        ) from error
    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as error:
        raise CapabilityManifestError(
            f"capability manifest at {manifest_path} is not valid YAML: {error}"
        ) from error
    if not isinstance(raw, dict):
        raise CapabilityManifestError(
            f"capability manifest at {manifest_path} must be a YAML mapping"
        )
    try:
        return CapabilityManifest.model_validate(raw)
    except Exception as error:  # noqa: BLE001 - fail closed on any schema violation
        raise CapabilityManifestError(
            f"capability manifest at {manifest_path} failed schema validation: {error}"
        ) from error


def validate_capability_manifest(manifest: CapabilityManifest) -> Tuple[bool, Tuple[str, ...]]:
    """Semantic (non-structural) lint checks beyond the pydantic schema.

    Returns ``(ok, warnings)``. These are deliberately warnings, not
    schema-level rejections — a reasonably-formed manifest entry can have a
    legitimate reason to omit a field these checks look for, but a
    reviewer should see it flagged.
    """
    warnings: list[str] = []
    for entry in manifest.entries:
        if entry.state == CapabilityState.ACTIVE and entry.validation_command is None:
            warnings.append(f"{entry.key}: state=active but no validation_command recorded")
        if (
            entry.state == CapabilityState.BLOCKED_CREDENTIALS
            and not entry.requires_credentials
        ):
            warnings.append(
                f"{entry.key}: state=blocked_credentials but requires_credentials is empty"
            )
        if entry.state == CapabilityState.FAILED_VALIDATION and entry.validation_command is None:
            warnings.append(
                f"{entry.key}: state=failed_validation but no validation_command recorded "
                "(how was failure determined?)"
            )
        if not entry.implementation_paths and entry.state in (
            CapabilityState.ACTIVE,
            CapabilityState.AVAILABLE,
            CapabilityState.BLOCKED_CREDENTIALS,
            CapabilityState.BLOCKED_RUNTIME,
            CapabilityState.FAILED_VALIDATION,
        ):
            warnings.append(
                f"{entry.key}: state={entry.state.value} but no implementation_paths recorded"
            )
    return len(warnings) == 0, tuple(warnings)
