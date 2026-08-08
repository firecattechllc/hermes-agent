"""Signed Hermes Link authentication, credential lifecycle, and replay evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import clean_identifier

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


SIGNING_SCHEMA_VERSION = 1
SIGNING_ALGORITHM = "hmac-sha256"
MAX_CLOCK_SKEW_SECONDS = 120
REPLAY_RETENTION_SECONDS = 600
MAX_REPLAY_RECORDS = 20_000
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_SAFE_PATH = re.compile(r"^/[a-z0-9][a-z0-9._~!$&'()*+,;=:@%/-]{0,511}$")


class HermesLinkAuthenticationError(ValueError):
    """Safe authentication failure with a stable non-secret reason code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CredentialStatus(str):
    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


class SigningCredential(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    credential_id: str
    secret_reference: str
    coordinator_node_id: str
    target_node_id: str
    status: str = CredentialStatus.ACTIVE
    not_before: int = Field(ge=0)
    expires_at: int = Field(gt=0)

    @field_validator("credential_id", "coordinator_node_id", "target_node_id")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("secret_reference")
    @classmethod
    def reference_only(cls, value: str) -> str:
        if not value.startswith(("env:", "file:")):
            raise ValueError("credential must use an env: or file: reference")
        if value.startswith("file:") and not Path(value[5:]).is_absolute():
            raise ValueError("credential file reference must be absolute")
        return value

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str) -> str:
        if value not in {
            CredentialStatus.ACTIVE,
            CredentialStatus.RETIRING,
            CredentialStatus.REVOKED,
        }:
            raise ValueError("credential status is invalid")
        return value

    @model_validator(mode="after")
    def valid_lifetime(self) -> "SigningCredential":
        if self.coordinator_node_id == self.target_node_id:
            raise ValueError("coordinator and target identities must differ")
        if self.expires_at <= self.not_before:
            raise ValueError("credential expiry must follow activation")
        return self

    def usable(self, *, now: int) -> bool:
        return (
            self.status in {CredentialStatus.ACTIVE, CredentialStatus.RETIRING}
            and self.not_before <= now <= self.expires_at
        )


class CredentialRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    credentials: tuple[SigningCredential, ...]
    schema_version: int = 1

    @model_validator(mode="after")
    def unique_and_bounded(self) -> "CredentialRegistry":
        if self.schema_version != 1 or not 1 <= len(self.credentials) <= 16:
            raise ValueError("credential registry is invalid")
        ids = [item.credential_id for item in self.credentials]
        if len(ids) != len(set(ids)):
            raise ValueError("credential identities must be unique")
        active_pairs = [
            (item.coordinator_node_id, item.target_node_id)
            for item in self.credentials
            if item.status == CredentialStatus.ACTIVE
        ]
        if len(active_pairs) != len(set(active_pairs)):
            raise ValueError("only one active credential is allowed per node pair")
        return self

    @classmethod
    def load(cls, path: Path) -> "CredentialRegistry":
        _require_restricted_file(path)
        return cls.model_validate_json(path.read_bytes())

    def credential(self, credential_id: str) -> SigningCredential:
        try:
            return next(
                item for item in self.credentials if item.credential_id == credential_id
            )
        except StopIteration as exc:
            raise HermesLinkAuthenticationError(
                "unknown_credential", "request credential is not enrolled"
            ) from exc

    def active_for(
        self, coordinator: str, target: str, *, now: int
    ) -> SigningCredential:
        candidates = [
            item
            for item in self.credentials
            if item.coordinator_node_id == coordinator
            and item.target_node_id == target
            and item.status == CredentialStatus.ACTIVE
            and item.usable(now=now)
        ]
        if len(candidates) != 1:
            raise HermesLinkAuthenticationError(
                "credential_unavailable",
                "one active credential is required for the node pair",
            )
        return candidates[0]


class CredentialEvidenceStore:
    """Hash-chained sanitized credential lifecycle evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = threading.RLock()

    def append(
        self,
        event: str,
        credential_id: str,
        coordinator_node_id: str,
        target_node_id: str,
        *,
        recorded_at: int,
    ) -> None:
        event = clean_identifier(event)
        credential_id = clean_identifier(credential_id)
        coordinator_node_id = clean_identifier(coordinator_node_id)
        target_node_id = clean_identifier(target_node_id)
        with self._thread_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            records = self.read()
            previous = "0" * 64 if not records else records[-1]["entry_hash"]
            value = {
                "sequence": len(records) + 1,
                "event": event,
                "credential_id": credential_id,
                "coordinator_node_id": coordinator_node_id,
                "target_node_id": target_node_id,
                "recorded_at": recorded_at,
                "previous_entry_hash": previous,
            }
            value["entry_hash"] = hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            payload = (
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            fd = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("credential evidence write made no progress")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)

    def read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        _require_restricted_file(self.path)
        values: list[dict[str, object]] = []
        previous = "0" * 64
        for number, line in enumerate(self.path.read_bytes().splitlines(), 1):
            try:
                value = json.loads(line)
                entry_hash = value.pop("entry_hash")
                expected = hashlib.sha256(
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                value["entry_hash"] = entry_hash
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"corrupt credential evidence line {number}") from exc
            if (
                value.get("sequence") != number
                or value.get("previous_entry_hash") != previous
                or entry_hash != expected
            ):
                raise ValueError("credential evidence hash chain is invalid")
            values.append(value)
            previous = entry_hash
        return values


def _require_restricted_file(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("credential reference must be a regular file")
    if path.stat().st_mode & 0o077:
        raise ValueError("credential file permissions must be 0600 or stricter")


def resolve_secret(reference: str) -> bytes:
    if reference.startswith("env:"):
        value = os.environ.get(reference[4:])
        if value is None:
            raise ValueError("credential environment reference is unavailable")
    elif reference.startswith("file:"):
        path = Path(reference[5:])
        _require_restricted_file(path)
        value = path.read_text(encoding="utf-8").strip()
    else:
        raise ValueError("unsupported credential reference")
    if len(value) < 32 or len(value) > 1024:
        raise ValueError("credential material has an invalid length")
    return value.encode()


def generate_secret() -> str:
    """Return new secret material to a caller that must store it without printing it."""
    return secrets.token_urlsafe(48)


def payload_digest(body: bytes) -> str:
    if not body:
        canonical = b""
    else:
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HermesLinkAuthenticationError(
                "malformed_payload", "request payload is not canonical JSON"
            ) from exc
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


class SignedRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request_id: str
    coordinator_node_id: str
    target_node_id: str
    method: str
    canonical_path: str
    payload_sha256: str
    timestamp: int = Field(ge=0)
    nonce: str
    credential_id: str
    algorithm: str = SIGNING_ALGORITHM
    schema_version: int = SIGNING_SCHEMA_VERSION

    @field_validator(
        "request_id", "coordinator_node_id", "target_node_id", "credential_id"
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("method")
    @classmethod
    def supported_method(cls, value: str) -> str:
        value = value.upper()
        if value not in {"GET", "POST"}:
            raise ValueError("signed method is unsupported")
        return value

    @field_validator("canonical_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        if _SAFE_PATH.fullmatch(value) is None or ".." in value or "?" in value:
            raise ValueError("signed path is invalid")
        return value

    @field_validator("payload_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        if _DIGEST.fullmatch(value) is None:
            raise ValueError("payload digest is invalid")
        return value

    @field_validator("nonce")
    @classmethod
    def valid_nonce(cls, value: str) -> str:
        if _NONCE.fullmatch(value) is None:
            raise ValueError("request nonce is invalid")
        return value

    @model_validator(mode="after")
    def supported_contract(self) -> "SignedRequest":
        if (
            self.algorithm != SIGNING_ALGORITHM
            or self.schema_version != SIGNING_SCHEMA_VERSION
        ):
            raise ValueError("signing contract is unsupported")
        return self

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()

    def sign(self, secret: bytes) -> str:
        return hmac.new(secret, self.canonical_bytes(), hashlib.sha256).hexdigest()

    def headers(self, secret: bytes) -> dict[str, str]:
        values = {
            "X-Hermes-Link-Request-Id": self.request_id,
            "X-Hermes-Link-Coordinator": self.coordinator_node_id,
            "X-Hermes-Link-Target": self.target_node_id,
            "X-Hermes-Link-Timestamp": str(self.timestamp),
            "X-Hermes-Link-Nonce": self.nonce,
            "X-Hermes-Link-Credential": self.credential_id,
            "X-Hermes-Link-Algorithm": self.algorithm,
            "X-Hermes-Link-Schema": str(self.schema_version),
            "X-Hermes-Link-Payload-SHA256": self.payload_sha256,
            "X-Hermes-Link-Signature": self.sign(secret),
        }
        return values

    @classmethod
    def from_headers(
        cls, method: str, path: str, headers: Mapping[str, str]
    ) -> "SignedRequest":
        normalized = {key.lower(): value for key, value in headers.items()}
        try:
            return cls(
                request_id=normalized["x-hermes-link-request-id"],
                coordinator_node_id=normalized["x-hermes-link-coordinator"],
                target_node_id=normalized["x-hermes-link-target"],
                method=method,
                canonical_path=path,
                payload_sha256=normalized["x-hermes-link-payload-sha256"],
                timestamp=int(normalized["x-hermes-link-timestamp"]),
                nonce=normalized["x-hermes-link-nonce"],
                credential_id=normalized["x-hermes-link-credential"],
                algorithm=normalized["x-hermes-link-algorithm"],
                schema_version=int(normalized["x-hermes-link-schema"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HermesLinkAuthenticationError(
                "authentication_failed", "complete signed authentication is required"
            ) from exc


@dataclass(frozen=True)
class ReplayRecord:
    request_id: str
    nonce_digest: str
    credential_id: str
    accepted_at: int
    event: str


class DurableReplayStore:
    """Permission-restricted append-only replay and sanitized transport evidence."""

    def __init__(
        self, path: Path, *, retention_seconds: int = REPLAY_RETENTION_SECONDS
    ) -> None:
        self.path = path
        self.retention_seconds = retention_seconds
        self._thread_lock = threading.RLock()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        with self._thread_lock, lock.open("a+") as handle:
            os.chmod(lock, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def accept(self, request: SignedRequest, *, now: int) -> None:
        nonce = hashlib.sha256(request.nonce.encode()).hexdigest()
        with self._lock():
            records = self._read()
            recent = [
                item
                for item in records
                if now - item.accepted_at <= self.retention_seconds
            ]
            if any(item.request_id == request.request_id for item in recent):
                raise HermesLinkAuthenticationError(
                    "duplicate_request", "signed request identity was already accepted"
                )
            if any(item.nonce_digest == nonce for item in recent):
                raise HermesLinkAuthenticationError(
                    "replayed_nonce", "signed request nonce was already accepted"
                )
            if len(records) >= MAX_REPLAY_RECORDS:
                raise HermesLinkAuthenticationError(
                    "replay_store_full", "replay protection capacity was reached"
                )
            record = ReplayRecord(
                request.request_id,
                nonce,
                request.credential_id,
                now,
                "signature_accepted",
            )
            self._append(record)

    def reject(self, request: SignedRequest | None, *, code: str, now: int) -> None:
        record = ReplayRecord(
            "unparsed" if request is None else request.request_id,
            (
                "0" * 64
                if request is None
                else hashlib.sha256(request.nonce.encode()).hexdigest()
            ),
            "unparsed" if request is None else request.credential_id,
            now,
            f"signature_rejected:{clean_identifier(code)}",
        )
        with self._lock():
            if len(self._read()) < MAX_REPLAY_RECORDS:
                self._append(record)

    def _append(self, record: ReplayRecord) -> None:
        payload = (
            json.dumps(record.__dict__, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        fd = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        )
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("replay evidence write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read(self) -> list[ReplayRecord]:
        if not self.path.exists():
            return []
        _require_restricted_file(self.path)
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("replay evidence has a torn or corrupt tail")
        records: list[ReplayRecord] = []
        for number, line in enumerate(raw.splitlines(), 1):
            try:
                value = json.loads(line)
                records.append(ReplayRecord(**value))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"corrupt replay evidence line {number}") from exc
        return records


class SignedRequestAuthenticator:
    def __init__(
        self,
        registry: CredentialRegistry,
        replay_store: DurableReplayStore,
        *,
        target_node_id: str,
        maximum_clock_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS,
    ) -> None:
        self.registry = registry
        self.replay_store = replay_store
        self.target_node_id = clean_identifier(target_node_id)
        self.maximum_clock_skew_seconds = maximum_clock_skew_seconds

    def verify(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        now: int | None = None,
    ) -> SignedRequest:
        observed_at = int(time.time()) if now is None else now
        request: SignedRequest | None = None
        try:
            request = SignedRequest.from_headers(method, path, headers)
            return self._verify(request, headers, body, observed_at=observed_at)
        except HermesLinkAuthenticationError as exc:
            self.replay_store.reject(request, code=exc.code, now=observed_at)
            raise

    def _verify(
        self,
        request: SignedRequest,
        headers: Mapping[str, str],
        body: bytes,
        *,
        observed_at: int,
    ) -> SignedRequest:
        if request.target_node_id != self.target_node_id:
            raise HermesLinkAuthenticationError(
                "target_identity_mismatch", "signed request target identity is invalid"
            )
        credential = self.registry.credential(request.credential_id)
        if credential.target_node_id != request.target_node_id:
            raise HermesLinkAuthenticationError(
                "target_identity_mismatch", "credential target identity is invalid"
            )
        if credential.coordinator_node_id != request.coordinator_node_id:
            raise HermesLinkAuthenticationError(
                "coordinator_identity_mismatch",
                "credential coordinator identity is invalid",
            )
        if not credential.usable(now=observed_at):
            raise HermesLinkAuthenticationError(
                "credential_inactive", "request credential is expired or revoked"
            )
        skew = request.timestamp - observed_at
        if skew > self.maximum_clock_skew_seconds:
            raise HermesLinkAuthenticationError(
                "future_request", "signed request timestamp is in the future"
            )
        if skew < -self.maximum_clock_skew_seconds:
            raise HermesLinkAuthenticationError(
                "expired_request", "signed request timestamp is expired"
            )
        actual_digest = payload_digest(body)
        if request.payload_sha256 != actual_digest:
            raise HermesLinkAuthenticationError(
                "payload_digest_mismatch", "signed request payload digest is invalid"
            )
        supplied_signature = next(
            (
                value
                for key, value in headers.items()
                if key.lower() == "x-hermes-link-signature"
            ),
            "",
        )
        expected_signature = request.sign(resolve_secret(credential.secret_reference))
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise HermesLinkAuthenticationError(
                "invalid_signature", "signed request authentication failed"
            )
        self.replay_store.accept(request, now=observed_at)
        return request


def build_signed_request(
    method: str,
    path: str,
    body: bytes,
    credential: SigningCredential,
    *,
    now: int | None = None,
    request_id: str | None = None,
    nonce: str | None = None,
) -> SignedRequest:
    return SignedRequest(
        request_id=request_id or f"request-{secrets.token_hex(16)}",
        coordinator_node_id=credential.coordinator_node_id,
        target_node_id=credential.target_node_id,
        method=method,
        canonical_path=path,
        payload_sha256=payload_digest(body),
        timestamp=int(time.time()) if now is None else now,
        nonce=nonce or secrets.token_urlsafe(24),
        credential_id=credential.credential_id,
    )
