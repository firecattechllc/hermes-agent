"""Deterministic, descriptive-only governed integration registry.

Stage 1 deliberately contains no installer, activator, worker, or dispatch surface.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterator

from sigil.ai.registry import canonical_digest

INTEGRATION_REGISTRY_SCHEMA_VERSION = 1
_ZERO_HASH = "0" * 64
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REPOSITORY = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_PUBLIC_REPOSITORY_URL = re.compile(
    r"^https://(?:github\.com|gitlab\.com)/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+?)(?:\.git)?$"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|client[_-]?secret|cookie|session[_-]?id)\s*[:=]|"
    r"(?:sk|ghp|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_PRIVATE_ENDPOINT = re.compile(
    r"(?i)(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?"
)
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s:=\"'\[])(?:/Users/|/home/|/root/|~[/\\]|[A-Za-z]:\\Users\\)"
)


def _validate_sanitized(value: object, context: str) -> None:
    serialized = json.dumps(value, sort_keys=True)
    if _SECRET.search(serialized):
        raise RegistryValidationError(f"credential material is prohibited in {context}")
    if _PRIVATE_ENDPOINT.search(serialized):
        raise RegistryValidationError(f"private endpoints are prohibited in {context}")
    if _PRIVATE_PATH.search(serialized):
        raise RegistryValidationError(f"private host paths are prohibited in {context}")


class RegistryValidationError(ValueError):
    """Registry data failed closed."""


class RegistryStorageError(RuntimeError):
    """Registry persistence or evidence failed closed."""


class LifecycleState(str, Enum):
    DISCOVERED = "discovered"
    UNDER_REVIEW = "under_review"
    REJECTED = "rejected"
    SANDBOX_APPROVED = "sandbox_approved"
    PILOT = "pilot"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"
    QUARANTINED = "quarantined"


class IntegrationCategory(str, Enum):
    OPERATOR_SURFACE = "operator_surface"
    COLLABORATION = "collaboration"
    ORGANIZATION = "organization"
    WORKER = "worker"
    KNOWLEDGE = "knowledge"
    DISCOVERY = "discovery"
    INTERNET_CAPABILITY = "internet_capability"
    MODEL_PROVIDER = "model_provider"
    OTHER = "other"


LIFECYCLE_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.DISCOVERED: frozenset({LifecycleState.UNDER_REVIEW, LifecycleState.REJECTED}),
    LifecycleState.UNDER_REVIEW: frozenset({LifecycleState.REJECTED, LifecycleState.SANDBOX_APPROVED}),
    LifecycleState.REJECTED: frozenset(),
    LifecycleState.SANDBOX_APPROVED: frozenset(
        {LifecycleState.PILOT, LifecycleState.REJECTED, LifecycleState.QUARANTINED}
    ),
    LifecycleState.PILOT: frozenset(
        {
            LifecycleState.CERTIFIED,
            LifecycleState.REJECTED,
            LifecycleState.DEPRECATED,
            LifecycleState.QUARANTINED,
        }
    ),
    LifecycleState.CERTIFIED: frozenset(
        {LifecycleState.DEPRECATED, LifecycleState.QUARANTINED}
    ),
    LifecycleState.DEPRECATED: frozenset({LifecycleState.QUARANTINED}),
    LifecycleState.QUARANTINED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class AuthorityDenials:
    paper_only: bool = True
    broker_submission: bool = False
    execution_authorized: bool = False
    approval_authority: bool = False
    capital_authority: bool = False
    portfolio_mutation: bool = False
    policy_mutation: bool = False
    credential_access: bool = False
    arbitrary_shell: bool = False
    arbitrary_filesystem: bool = False
    governance_bypass: bool = False
    activation_authorized: bool = False
    installation_authorized: bool = False

    def validate(self) -> None:
        if self != AuthorityDenials():
            raise RegistryValidationError("integration authority must remain fully denied")


@dataclass(frozen=True, slots=True)
class IntegrationRegistryEntry:
    integration_id: str
    canonical_project_name: str
    category: IntegrationCategory
    repository_url: str
    pinned_identity: str
    release_label: str | None
    upstream_repository_identity: str
    maintainer_identity: str
    maturity: str
    license_classification: str
    license_evidence_source: str
    activity_evidence: str
    activity_observed_at: str
    credential_requirements: tuple[str, ...]
    authentication_requirements: tuple[str, ...]
    declared_network_access: tuple[str, ...]
    declared_egress_destinations: tuple[str, ...]
    declared_filesystem_access: tuple[str, ...]
    declared_tool_permissions: tuple[str, ...]
    declared_shell_process_authority: tuple[str, ...]
    declared_browser_authority: tuple[str, ...]
    declared_execution_model: str
    declared_external_data_transmission: tuple[str, ...]
    install_mechanism: str
    dependency_summary: tuple[str, ...]
    supported_machines: tuple[str, ...]
    approved_machines: tuple[str, ...]
    supported_profiles: tuple[str, ...]
    approved_profiles: tuple[str, ...]
    capabilities: tuple[str, ...]
    integration_overlap: tuple[str, ...]
    known_risks: tuple[str, ...]
    threat_model_references: tuple[str, ...]
    evaluation_evidence_references: tuple[str, ...]
    rollback_instructions: str
    disable_instructions: str
    quarantine_instructions: str
    lifecycle_state: LifecycleState
    lifecycle_reason: str
    created_at: str
    observed_at: str
    reviewed_at: str | None = None
    certified_at: str | None = None
    registry_schema_version: int = INTEGRATION_REGISTRY_SCHEMA_VERSION
    entry_revision: int = 1
    content_digest: str = ""
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        self.validate()
        expected = self.expected_digest()
        if self.content_digest and self.content_digest != expected:
            raise RegistryValidationError("entry content digest mismatch")
        if not self.content_digest:
            object.__setattr__(self, "content_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["category"] = self.category.value
        payload["lifecycle_state"] = self.lifecycle_state.value
        payload.pop("content_digest", None)
        return payload

    def expected_digest(self) -> str:
        return f"sha256:{canonical_digest(self.digest_payload())}"

    def validate(self) -> None:
        if self.registry_schema_version != INTEGRATION_REGISTRY_SCHEMA_VERSION:
            raise RegistryValidationError("unsupported registry schema version")
        if not isinstance(self.category, IntegrationCategory):
            raise RegistryValidationError("unknown integration category")
        if not isinstance(self.lifecycle_state, LifecycleState):
            raise RegistryValidationError("unknown lifecycle state")
        if not _IDENTIFIER.fullmatch(self.integration_id):
            raise RegistryValidationError("malformed integration ID")
        if not self.canonical_project_name.strip():
            raise RegistryValidationError("canonical project name is required")
        match = _PUBLIC_REPOSITORY_URL.fullmatch(self.repository_url)
        if match is None or _REPOSITORY.fullmatch(self.upstream_repository_identity) is None:
            raise RegistryValidationError("malformed repository identity")
        if match.group(1).removesuffix(".git").lower() != self.upstream_repository_identity.lower():
            raise RegistryValidationError("conflicting repository and project identity")
        if self.pinned_identity.lower() == "latest" or not (
            _COMMIT.fullmatch(self.pinned_identity) or _RELEASE_DIGEST.fullmatch(self.pinned_identity)
        ):
            raise RegistryValidationError("immutable commit or release digest is required")
        if self.entry_revision < 1:
            raise RegistryValidationError("entry revision must be positive")
        timestamps = (self.created_at, self.observed_at, self.activity_observed_at)
        if self.reviewed_at is not None:
            timestamps += (self.reviewed_at,)
        if self.certified_at is not None:
            timestamps += (self.certified_at,)
        if any(_UTC_TIMESTAMP.fullmatch(timestamp) is None for timestamp in timestamps):
            raise RegistryValidationError("registry timestamps must be canonical UTC values")
        required = {
            "maintainer identity": self.maintainer_identity,
            "maturity": self.maturity,
            "license classification": self.license_classification,
            "license evidence": self.license_evidence_source,
            "activity evidence": self.activity_evidence,
            "activity observation time": self.activity_observed_at,
            "execution model": self.declared_execution_model,
            "install mechanism": self.install_mechanism,
            "rollback instructions": self.rollback_instructions,
            "disable instructions": self.disable_instructions,
            "quarantine instructions": self.quarantine_instructions,
            "lifecycle reason": self.lifecycle_reason,
            "created time": self.created_at,
            "observed time": self.observed_at,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise RegistryValidationError(f"missing required fields: {', '.join(missing)}")
        self.authority.validate()
        _validate_sanitized(self.digest_payload(), "registry entry")

    @property
    def pinned(self) -> bool:
        return True

    @property
    def can_activate(self) -> bool:
        return False


def validate_transition(current: LifecycleState, requested: LifecycleState) -> None:
    if requested not in LIFECYCLE_TRANSITIONS.get(current, frozenset()):
        raise RegistryValidationError(
            f"lifecycle transition {current.value} -> {requested.value} is denied"
        )


@dataclass(frozen=True, slots=True)
class LifecycleRequest:
    request_id: str
    integration_id: str
    current_state: LifecycleState
    requested_state: LifecycleState
    reason: str
    requesting_actor_identity: str
    policy_revision: str
    evidence_references: tuple[str, ...]
    requested_at: str
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.request_id) or not _IDENTIFIER.fullmatch(
            self.integration_id
        ):
            raise RegistryValidationError("malformed lifecycle request identity")
        if not all((self.reason, self.requesting_actor_identity, self.policy_revision, self.requested_at)):
            raise RegistryValidationError("lifecycle request fields cannot be blank")
        if _UTC_TIMESTAMP.fullmatch(self.requested_at) is None:
            raise RegistryValidationError("lifecycle request timestamp must be canonical UTC")
        self.authority.validate()
        validate_transition(self.current_state, self.requested_state)
        _validate_sanitized(_enum_payload(asdict(self)), "lifecycle request")


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    request_id: str
    integration_id: str
    deciding_actor_identity: str
    decided_at: str
    approved: bool
    rejection_classification: str | None
    resulting_registry_revision: str | None
    authority: AuthorityDenials = AuthorityDenials()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.request_id) or not _IDENTIFIER.fullmatch(
            self.integration_id
        ):
            raise RegistryValidationError("malformed lifecycle decision identity")
        if not self.deciding_actor_identity or not self.decided_at:
            raise RegistryValidationError("lifecycle decision fields cannot be blank")
        if _UTC_TIMESTAMP.fullmatch(self.decided_at) is None:
            raise RegistryValidationError("lifecycle decision timestamp must be canonical UTC")
        if self.approved and self.rejection_classification is not None:
            raise RegistryValidationError("approved decision cannot have a rejection classification")
        if not self.approved and not self.rejection_classification:
            raise RegistryValidationError("rejected decision requires a classification")
        if self.resulting_registry_revision is None or _RELEASE_DIGEST.fullmatch(
            self.resulting_registry_revision
        ) is None:
            raise RegistryValidationError("resulting registry revision must be a SHA-256 identity")
        self.authority.validate()
        _validate_sanitized(_enum_payload(asdict(self)), "lifecycle decision")

    def validate_for(self, request: LifecycleRequest) -> None:
        if self.request_id != request.request_id or self.integration_id != request.integration_id:
            raise RegistryValidationError("lifecycle decision does not match request")
        if self.deciding_actor_identity == request.requesting_actor_identity:
            raise RegistryValidationError("lifecycle request cannot self-approve")


@dataclass(frozen=True, slots=True)
class GovernedIntegrationRegistry:
    entries: tuple[IntegrationRegistryEntry, ...] = ()
    schema_version: int = INTEGRATION_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != INTEGRATION_REGISTRY_SCHEMA_VERSION:
            raise RegistryValidationError("unsupported registry schema version")
        ids = [entry.integration_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise RegistryValidationError("duplicate integration ID")
        active = [
            (entry.upstream_repository_identity.lower(), entry.pinned_identity)
            for entry in self.entries
            if entry.lifecycle_state not in {
                LifecycleState.REJECTED,
                LifecycleState.DEPRECATED,
                LifecycleState.QUARANTINED,
            }
        ]
        if len(active) != len(set(active)):
            raise RegistryValidationError("duplicate active pinned identity")

    @property
    def revision(self) -> str:
        return f"sha256:{canonical_digest(self.payload())}"

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "entries": [
                _entry_payload(entry) for entry in sorted(self.entries, key=lambda item: item.integration_id)
            ],
        }


def _entry_payload(entry: IntegrationRegistryEntry) -> dict[str, object]:
    payload = asdict(entry)
    payload["category"] = entry.category.value
    payload["lifecycle_state"] = entry.lifecycle_state.value
    return payload


def _decode_entry(payload: object) -> IntegrationRegistryEntry:
    if not isinstance(payload, dict):
        raise RegistryValidationError("registry entry must be an object")
    values = dict(payload)
    values["category"] = IntegrationCategory(values["category"])
    values["lifecycle_state"] = LifecycleState(values["lifecycle_state"])
    for field in (
        "credential_requirements", "authentication_requirements", "declared_network_access",
        "declared_egress_destinations", "declared_filesystem_access", "declared_tool_permissions",
        "declared_shell_process_authority", "declared_browser_authority",
        "declared_external_data_transmission", "dependency_summary", "supported_machines",
        "approved_machines", "supported_profiles", "approved_profiles", "capabilities",
        "integration_overlap", "known_risks", "threat_model_references",
        "evaluation_evidence_references",
    ):
        values[field] = tuple(values[field])
    values["authority"] = AuthorityDenials(**values.get("authority", {}))
    try:
        return IntegrationRegistryEntry(**values)
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, RegistryValidationError):
            raise
        raise RegistryValidationError(f"malformed registry entry: {error}") from error


class DurableIntegrationRegistryStore:
    """Atomic registry snapshots plus append-only hash-linked lifecycle evidence."""

    def __init__(self, state_root: Path) -> None:
        if not state_root.is_absolute() or state_root.is_symlink():
            raise RegistryStorageError("registry state root must be an absolute non-symlink Path")
        self.directory = state_root / "governed-integration-registry-v1"
        self.registry_path = self.directory / "registry.json"
        self.evidence_path = self.directory / "lifecycle-evidence.jsonl"
        self.lock_path = self.directory / "registry.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise RegistryStorageError("registry directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise RegistryStorageError("registry lock cannot be a symlink")
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def load(self) -> GovernedIntegrationRegistry:
        if not self.directory.exists():
            return GovernedIntegrationRegistry()
        if self.directory.is_symlink() or not self.lock_path.exists() or self.lock_path.is_symlink():
            raise RegistryStorageError("registry read lock is unavailable")
        with self.lock_path.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._load_unlocked()

    def _load_unlocked(self) -> GovernedIntegrationRegistry:
        if not self.registry_path.exists():
            return GovernedIntegrationRegistry()
        if self.registry_path.is_symlink():
            raise RegistryStorageError("registry path cannot be a symlink")
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"}:
                raise RegistryValidationError("registry storage shape is invalid")
            return GovernedIntegrationRegistry(
                tuple(_decode_entry(item) for item in payload["entries"]),
                payload["schema_version"],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RegistryStorageError(f"registry storage is invalid: {error}") from error

    def replace(self, registry: GovernedIntegrationRegistry) -> str:
        encoded = json.dumps(registry.payload(), sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock():
            temporary = self.directory / f".registry.{os.getpid()}.tmp"
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.registry_path)
                descriptor = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return registry.revision

    def read_evidence(self) -> tuple[dict[str, object], ...]:
        if not self.directory.exists() or not self.evidence_path.exists():
            return ()
        if not self.lock_path.exists() or self.lock_path.is_symlink():
            raise RegistryStorageError("registry read lock is unavailable")
        with self.lock_path.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
            return self._read_evidence_unlocked()

    def append_decision(
        self, request: LifecycleRequest, decision: LifecycleDecision
    ) -> dict[str, object]:
        decision.validate_for(request)
        with self._lock():
            if self.evidence_path.is_symlink():
                raise RegistryStorageError("lifecycle evidence path cannot be a symlink")
            records = self._read_evidence_unlocked()
            base: dict[str, object] = {
                "sequence": len(records) + 1,
                "previous_record_hash": records[-1]["entry_hash"] if records else _ZERO_HASH,
                "request": _enum_payload(asdict(request)),
                "decision": _enum_payload(asdict(decision)),
                "paper_only": True,
                "broker_submission": False,
                "execution_authorized": False,
                "approval_authority": False,
                "capital_authority": False,
                "portfolio_mutation": False,
                "policy_mutation": False,
                "credential_access": False,
                "arbitrary_shell": False,
                "arbitrary_filesystem": False,
                "governance_bypass": False,
                "activation_authorized": False,
                "installation_authorized": False,
            }
            base["entry_hash"] = canonical_digest(base)
            with self.evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(base, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return base

    def _read_evidence_unlocked(self) -> tuple[dict[str, object], ...]:
        if not self.evidence_path.exists():
            return ()
        try:
            records = tuple(json.loads(line) for line in self.evidence_path.read_text().splitlines())
        except (OSError, json.JSONDecodeError) as error:
            raise RegistryStorageError(f"lifecycle evidence is invalid: {error}") from error
        previous = _ZERO_HASH
        for sequence, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise RegistryStorageError("lifecycle evidence record shape is invalid")
            expected = canonical_digest({k: v for k, v in record.items() if k != "entry_hash"})
            if (
                record.get("sequence") != sequence
                or record.get("previous_record_hash") != previous
                or record.get("entry_hash") != expected
            ):
                raise RegistryStorageError("lifecycle evidence integrity is invalid")
            previous = expected
        return records


def _enum_payload(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _enum_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_enum_payload(item) for item in value]
    return value


def apply_lifecycle_decision(
    entry: IntegrationRegistryEntry,
    request: LifecycleRequest,
    decision: LifecycleDecision,
) -> IntegrationRegistryEntry:
    """Apply an independently approved transition; approval still grants no activation."""
    decision.validate_for(request)
    if request.integration_id != entry.integration_id or request.current_state != entry.lifecycle_state:
        raise RegistryValidationError("lifecycle request does not match current registry entry")
    if not decision.approved:
        return entry
    validate_transition(entry.lifecycle_state, request.requested_state)
    return replace(
        entry,
        lifecycle_state=request.requested_state,
        lifecycle_reason=request.reason,
        reviewed_at=decision.decided_at,
        certified_at=decision.decided_at
        if request.requested_state == LifecycleState.CERTIFIED
        else entry.certified_at,
        entry_revision=entry.entry_revision + 1,
        content_digest="",
    )


def integration_registry_status(environment: dict[str, str] | None = None) -> dict[str, object]:
    source = dict(os.environ if environment is None else environment)
    enabled = source.get("SIGIL_INTEGRATION_REGISTRY_ENABLED", "").lower() in {"1", "true", "yes"}
    root_value = source.get("SIGIL_DESKTOP_STATE_DIR")
    registry = GovernedIntegrationRegistry()
    health = "empty"
    reason: str | None = None
    evidence: tuple[dict[str, object], ...] = ()
    if root_value:
        root = Path(root_value)
        try:
            store = DurableIntegrationRegistryStore(root)
            registry = store.load()
            evidence = store.read_evidence()
            health = "healthy" if registry.entries else "empty"
        except (RegistryStorageError, RegistryValidationError):
            health = "invalid"
            reason = "registry storage failed integrity validation"
            registry = GovernedIntegrationRegistry()
            evidence = ()
    lifecycle = Counter(entry.lifecycle_state.value for entry in registry.entries)
    categories = Counter(entry.category.value for entry in registry.entries)
    authority_fields = (
        "declared_shell_process_authority", "declared_filesystem_access",
        "declared_browser_authority", "declared_network_access",
    )
    return {
        "enabled": enabled,
        "state": "invalid" if health == "invalid" else "disabled" if not enabled else health,
        "store_health": health,
        "reason": reason,
        "schema_version": INTEGRATION_REGISTRY_SCHEMA_VERSION,
        "registry_revision": registry.revision,
        "entry_count": len(registry.entries),
        "counts_by_lifecycle": {state.value: lifecycle[state.value] for state in LifecycleState},
        "counts_by_category": dict(sorted(categories.items())),
        "pinned_count": len(registry.entries),
        "unpinned_count": 0,
        "valid_count": len(registry.entries),
        "invalid_count": 0 if health != "invalid" else 1,
        "certified_count": lifecycle[LifecycleState.CERTIFIED.value],
        "quarantined_count": lifecycle[LifecycleState.QUARANTINED.value],
        "deprecated_count": lifecycle[LifecycleState.DEPRECATED.value],
        "missing_license_evidence_count": sum(not entry.license_evidence_source for entry in registry.entries),
        "missing_activity_evidence_count": sum(not entry.activity_evidence for entry in registry.entries),
        "missing_rollback_count": sum(not entry.rollback_instructions for entry in registry.entries),
        "credential_required_count": sum(bool(entry.credential_requirements) for entry in registry.entries),
        "external_transmission_count": sum(bool(entry.declared_external_data_transmission) for entry in registry.entries),
        "declared_authority_counts": {
            field.removeprefix("declared_").removesuffix("_authority").removesuffix("_access"): sum(
                bool(getattr(entry, field)) for entry in registry.entries
            )
            for field in authority_fields
        },
        "latest_lifecycle_evidence_identity": None if not evidence else f"sha256:{evidence[-1]['entry_hash']}",
        **asdict(AuthorityDenials()),
    }
