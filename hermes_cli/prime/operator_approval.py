"""Governed operator approval records.

Fleet Unification live-runtime work. Several pre-existing subsystems each
have their own partial approval precedent —
``hermes_cli.agent_roles.remote_maintenance.RepairApproval`` (action-bound
via a proposal checksum, but no expiry field of its own),
``hermes_cli.agent_roles.runtime_recovery_authorization.RuntimeRecoveryHumanApproval``
(strong identity/action binding via a content-addressed checksum, but no
expiry or replay-nonce), ``hermes_cli.agent_roles.model_execution.ApprovalEvidence``
(has ``issued_at``/``expires_at``/``revoked``, but is model-execution
specific), and ``hermes_cli.hermes_link.security.DurableReplayStore``
(real nonce-based replay rejection, but scoped to Hermes Link's signed
node-to-node transport). This module is the general-purpose approval record
this live runtime needs for remote maintenance, desktop use,
credential-sensitive operations, deployment, model changes, and
Sigil-sensitive actions: it combines all four properties — expiring,
non-replayable, identity-bound, action-bound — behind one evidence-backed
type, composing the ``DurableReplayStore`` nonce-rejection pattern rather
than re-deriving it differently a fifth time.

An :class:`OperatorApproval` never grants execution authority by itself —
see :meth:`OperatorApproval.grants_no_execution_authority`. It only records
that a specific, named operator approved one specific, content-addressed
action against one specific subject, through one specific channel, within
one specific validity window, exactly once. Whatever governed executor
consumes the approval (remote maintenance, desktop use, Sigil routing, ...)
still independently decides whether to proceed — an approval is necessary,
never sufficient.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only lock, matches evidence.py
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field, model_validator

OPERATOR_APPROVAL_SCHEMA_VERSION = 1
SUPPORTED_OPERATOR_APPROVAL_SCHEMA_VERSIONS = frozenset({1})

# Sensitive live actions get short-lived approvals by default — this is not
# a long-lived credential, it authorizes one occurrence of one action.
DEFAULT_MAX_APPROVAL_AGE_SECONDS = 900


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_schema(version: int) -> int:
    if version not in SUPPORTED_OPERATOR_APPROVAL_SCHEMA_VERSIONS:
        raise ValueError(
            f"operator approval schema version {version} not supported "
            f"(supported: {sorted(SUPPORTED_OPERATOR_APPROVAL_SCHEMA_VERSIONS)})"
        )
    return version


def compute_action_id(payload: Dict[str, object]) -> str:
    """Content-address exactly what is being approved.

    Callers build ``payload`` from the specific action under approval (e.g.
    a desktop-use request's action/app/scope, or a maintenance proposal's
    checksum) so a granted approval can only ever be matched against the
    exact action it was granted for — mirrors
    ``RepairProposal.checksum``/``RuntimeRecoveryHumanApproval.checksum``.
    """
    return f"actn_{_digest(payload)[:32]}"


class OperatorApprovalScope(str, Enum):
    REMOTE_MAINTENANCE = "remote_maintenance"
    DESKTOP_USE = "desktop_use"
    CREDENTIAL_SENSITIVE = "credential_sensitive"
    DEPLOYMENT = "deployment"
    MODEL_CHANGE = "model_change"
    SIGIL_SENSITIVE = "sigil_sensitive"


class ApprovalChannel(str, Enum):
    TELEGRAM = "telegram"
    PHONE = "phone"
    CLI = "cli"
    WEB = "web"


class OperatorApproval(BaseModel):
    """A single, evidence-backed, expiring, action-bound operator approval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=OPERATOR_APPROVAL_SCHEMA_VERSION)
    approval_id: str = Field(..., min_length=1, max_length=160)
    scope: OperatorApprovalScope
    action_id: str = Field(..., min_length=1, max_length=160)
    subject_identity_id: str = Field(..., min_length=1, max_length=128)
    operator_identity: str = Field(..., min_length=1, max_length=256)
    channel: ApprovalChannel
    nonce: str = Field(..., min_length=16, max_length=128)
    granted_at: int = Field(..., ge=0)
    expires_at: int = Field(..., ge=0)
    evidence_ref: str = Field(..., min_length=1, max_length=256)
    correlation_id: Optional[str] = Field(default=None, max_length=128)
    revoked: bool = False
    revoked_at: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "OperatorApproval":
        if self.schema_version != OPERATOR_APPROVAL_SCHEMA_VERSION:
            raise ValueError("unsupported operator approval schema version")
        if self.expires_at <= self.granted_at:
            raise ValueError("an approval must expire strictly after it is granted")
        if self.expires_at - self.granted_at > DEFAULT_MAX_APPROVAL_AGE_SECONDS:
            raise ValueError(
                "an approval's validity window exceeds the governed maximum "
                f"({DEFAULT_MAX_APPROVAL_AGE_SECONDS}s)"
            )
        if self.revoked and self.revoked_at is None:
            raise ValueError("a revoked approval must record revoked_at")
        if not self.revoked and self.revoked_at is not None:
            raise ValueError("a non-revoked approval cannot carry revoked_at")
        return self

    @classmethod
    def grant(
        cls,
        *,
        scope: OperatorApprovalScope,
        action_id: str,
        subject_identity_id: str,
        operator_identity: str,
        channel: ApprovalChannel,
        granted_at: int,
        evidence_ref: str,
        max_age_seconds: int = DEFAULT_MAX_APPROVAL_AGE_SECONDS,
        correlation_id: Optional[str] = None,
    ) -> "OperatorApproval":
        """Construct a fresh approval with a random, unpredictable nonce."""
        nonce = secrets.token_urlsafe(24)
        payload = {
            "scope": scope.value,
            "action_id": action_id,
            "subject_identity_id": subject_identity_id,
            "operator_identity": operator_identity,
            "channel": channel.value,
            "nonce": nonce,
            "granted_at": granted_at,
        }
        approval_id = f"opap_{_digest(payload)[:24]}"
        return cls(
            approval_id=approval_id,
            scope=scope,
            action_id=action_id,
            subject_identity_id=subject_identity_id,
            operator_identity=operator_identity,
            channel=channel,
            nonce=nonce,
            granted_at=granted_at,
            expires_at=granted_at + max_age_seconds,
            evidence_ref=evidence_ref,
            correlation_id=correlation_id,
        )

    def revoke(self, *, now: int) -> "OperatorApproval":
        if self.revoked:
            return self
        return self.model_copy(update={"revoked": True, "revoked_at": now})

    def grants_no_execution_authority(self) -> None:
        """Documentation no-op — see ``FleetIdentity.grants_no_authority`` for
        the same convention. An approval record is never itself execution
        authority; it is a precondition a separately-governed executor must
        still independently check."""
        return None


class ApprovalRejectionCode(str, Enum):
    SCOPE_MISMATCH = "scope_mismatch"
    ACTION_MISMATCH = "action_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    REVOKED = "revoked"
    NOT_YET_VALID = "not_yet_valid"
    EXPIRED = "expired"
    REPLAYED = "replayed"


def _default_state_root() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "prime"


class OperatorApprovalReplayError(RuntimeError):
    """An approval could not be safely consumed."""


class OperatorApprovalReplayStore:
    """Durable, single-use consumption ledger for :class:`OperatorApproval`.

    Mirrors ``hermes_cli.hermes_link.security.DurableReplayStore``'s
    nonce-digest rejection pattern (reject on a previously-seen
    ``approval_id`` or nonce digest), implemented with the same
    fcntl-locked, atomically-rewritten JSON convention used by
    :class:`hermes_cli.prime.fleet_registry.FleetRegistryStore`. An approval
    can be consumed at most once, ever — this is what makes "non-replayable"
    true even if the exact same approval message is captured and resent.
    """

    def __init__(self, state_root: Optional[Path] = None) -> None:
        root = state_root if state_root is not None else _default_state_root()
        if not root.is_absolute():
            raise OperatorApprovalReplayError("replay store state root must be an absolute path")
        if root.is_symlink():
            raise OperatorApprovalReplayError("replay store state root cannot be a symlink")

        self.directory = root / "operator-approval-v1"
        self.records_path = self.directory / "consumed.json"
        self.lock_path = self.directory / "consumed.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise OperatorApprovalReplayError("replay store directory cannot be a symlink")
        if self.lock_path.is_symlink():
            raise OperatorApprovalReplayError("replay store lock cannot be a symlink")

        with self.lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _read_unlocked(self) -> Dict[str, dict]:
        if not self.records_path.exists():
            return {}
        if self.records_path.is_symlink():
            raise OperatorApprovalReplayError("replay store records file cannot be a symlink")
        try:
            raw = json.loads(self.records_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OperatorApprovalReplayError("replay store records file is unreadable") from error
        if not isinstance(raw, dict):
            raise OperatorApprovalReplayError("replay store records file shape is invalid")
        return raw

    def _write_unlocked(self, records: Dict[str, dict]) -> None:
        encoded = json.dumps(records, sort_keys=True, separators=(",", ":"))
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.directory), prefix=".consumed.", suffix=".tmp"
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

    def is_consumed(self, approval_id: str) -> bool:
        with self._lock():
            return approval_id in self._read_unlocked()

    def consume(self, approval: OperatorApproval, *, now: int) -> None:
        nonce_digest = hashlib.sha256(approval.nonce.encode("utf-8")).hexdigest()
        with self._lock():
            records = self._read_unlocked()
            if approval.approval_id in records:
                raise OperatorApprovalReplayError(
                    f"approval {approval.approval_id} was already consumed"
                )
            if any(item.get("nonce_digest") == nonce_digest for item in records.values()):
                raise OperatorApprovalReplayError("approval nonce was already consumed")
            records[approval.approval_id] = {
                "nonce_digest": nonce_digest,
                "consumed_at": now,
                "action_id": approval.action_id,
            }
            self._write_unlocked(records)


def validate_operator_approval(
    approval: Optional[OperatorApproval],
    *,
    expected_scope: OperatorApprovalScope,
    expected_action_id: str,
    expected_subject_identity_id: str,
    now: int,
    replay_store: OperatorApprovalReplayStore,
) -> Tuple[bool, Optional[ApprovalRejectionCode]]:
    """Deterministically validate and consume an approval. Fail closed.

    On success the approval is atomically consumed (see
    :meth:`OperatorApprovalReplayStore.consume`) — a second call with the
    same approval, even with everything else identical, is rejected as
    ``REPLAYED``. A ``None`` approval is always rejected: this function
    provides no path by which the *absence* of an approval could be treated
    as one being granted.
    """
    if approval is None:
        return False, ApprovalRejectionCode.SCOPE_MISMATCH
    if approval.scope != expected_scope:
        return False, ApprovalRejectionCode.SCOPE_MISMATCH
    if approval.action_id != expected_action_id:
        return False, ApprovalRejectionCode.ACTION_MISMATCH
    if approval.subject_identity_id != expected_subject_identity_id:
        return False, ApprovalRejectionCode.IDENTITY_MISMATCH
    if approval.revoked:
        return False, ApprovalRejectionCode.REVOKED
    if now < approval.granted_at:
        return False, ApprovalRejectionCode.NOT_YET_VALID
    if now > approval.expires_at:
        return False, ApprovalRejectionCode.EXPIRED

    try:
        replay_store.consume(approval, now=now)
    except OperatorApprovalReplayError:
        return False, ApprovalRejectionCode.REPLAYED

    return True, None
