"""Strict Step 32 wire and status models.

The envelope carries data and references only. It is deliberately incapable of
expressing executable commands or credentials.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hermes_cli.mission_control.models import ApprovalState

LINK_SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
PROHIBITED_KEYS = frozenset({
    "command",
    "shell",
    "shell_command",
    "sudo",
    "script",
    "executable",
    "deployment",
    "publish",
    "spend",
    "external_message",
    "production_mutation",
    "destructive_operation",
    "authorization",
    "api_key",
    "password",
    "secret",
    "token",
})
PROHIBITED_ACTIONS = frozenset({
    "shell",
    "sudo",
    "root",
    "deploy",
    "deployment",
    "publish",
    "spend",
    "external_message",
    "destructive_operation",
    "production_mutation",
})
ACTION_FIELDS = frozenset({"action", "operation", "requested_action", "capability"})


def utc_now() -> int:
    return int(time.time())


def new_message_id() -> str:
    return f"link-{uuid.uuid4().hex}"


def clean_identifier(value: str) -> str:
    value = value.strip().lower()
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(
            "identifier must be lowercase, stable, and at most 128 characters"
        )
    return value


def _validate_payload(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in PROHIBITED_KEYS or normalized.endswith((
                "_command",
                "_script",
                "_credential",
                "_token",
                "_secret",
                "_password",
            )):
                raise ValueError(f"{path} contains prohibited field {key!r}")
            if (
                normalized in ACTION_FIELDS
                and str(child).strip().lower() in PROHIBITED_ACTIONS
            ):
                raise ValueError(f"{path} requests prohibited action {child!r}")
            _validate_payload(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_payload(child, f"{path}[{index}]")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError(f"{path} contains a non-JSON value")


class NodeRole(str, Enum):
    BIG_SISTER = "big_sister"
    LITTLE_SISTER = "little_sister"


class MessageType(str, Enum):
    CHAT = "chat"
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    LESSON_PACKAGE = "lesson_package"
    STATUS = "status"
    ESCALATION = "escalation"
    ACKNOWLEDGEMENT = "acknowledgement"
    ERROR = "error"


class MessagePriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DeliveryState(str, Enum):
    QUEUED = "queued"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    REJECTED = "rejected"
    RETRYABLE = "retryable"
    DEAD_LETTERED = "dead_lettered"


class ComponentHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class PresenceState(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class RetryMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    attempt_count: int = Field(default=0, ge=0)
    maximum_attempts: int = Field(default=3, ge=1, le=20)
    next_attempt_at: Optional[int] = Field(default=None, ge=0)
    last_attempt_at: Optional[int] = Field(default=None, ge=0)
    last_error_code: Optional[str] = Field(default=None, max_length=128)


class HermesLinkEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    message_id: str = Field(default_factory=new_message_id)
    correlation_id: str
    conversation_id: Optional[str] = None
    sender_node: str
    recipient_node: str
    message_type: MessageType
    priority: MessagePriority = MessagePriority.NORMAL
    created_at: int = Field(default_factory=utc_now, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    evidence_references: Tuple[str, ...] = ()
    artifact_references: Tuple[str, ...] = ()
    approval_required: bool = False
    approval_state: Optional[ApprovalState] = None
    delivery_state: DeliveryState = DeliveryState.QUEUED
    retry: RetryMetadata = Field(default_factory=RetryMetadata)
    schema_version: int = LINK_SCHEMA_VERSION

    @field_validator("message_id", "correlation_id", "sender_node", "recipient_node")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("conversation_id")
    @classmethod
    def optional_identifier(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else clean_identifier(value)

    @field_validator("schema_version")
    @classmethod
    def version(cls, value: int) -> int:
        if value != LINK_SCHEMA_VERSION:
            raise ValueError("unsupported Hermes-link schema version")
        return value

    @field_validator("evidence_references", "artifact_references")
    @classmethod
    def references(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        cleaned = tuple(sorted(set(item.strip() for item in values)))
        if any(not item or "://" not in item for item in cleaned):
            raise ValueError("references must be sanitized URI references")
        if any(
            any(
                secret in item.lower()
                for secret in ("token=", "secret=", "password=", "api_key=")
            )
            for item in cleaned
        ):
            raise ValueError("references must not contain authentication material")
        return cleaned

    @model_validator(mode="after")
    def validate_boundary(self) -> "HermesLinkEnvelope":
        if self.sender_node == self.recipient_node:
            raise ValueError("sender and recipient nodes must differ")
        if self.approval_required and self.approval_state is None:
            raise ValueError("approval-required message must carry an approval state")
        if not self.approval_required and self.approval_state is not None:
            raise ValueError("approval state is inconsistent with approval requirement")
        _validate_payload(self.payload)
        return self

    def serialized_size(self) -> int:
        return len(
            json.dumps(self.model_dump(mode="json"), separators=(",", ":")).encode()
        )


class QueueCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    queued: int = 0
    delivered: int = 0
    acknowledged: int = 0
    failed: int = 0
    rejected: int = 0
    retryable: int = 0
    dead_lettered: int = 0


class HermesLinkStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str
    node_role: NodeRole
    presence: PresenceState
    service_version: str
    uptime_seconds: Optional[int] = Field(default=None, ge=0)
    queue_counts: QueueCounts = Field(default_factory=QueueCounts)
    nursery_state: ComponentHealth = ComponentHealth.UNKNOWN
    ollama_health: ComponentHealth = ComponentHealth.UNKNOWN
    finbert_health: ComponentHealth = ComponentHealth.UNKNOWN
    memory_index_health: ComponentHealth = ComponentHealth.UNKNOWN
    last_synchronization_at: Optional[int] = Field(default=None, ge=0)
    pending_escalations: int = Field(default=0, ge=0)
    degraded_components: Tuple[str, ...] = ()
    evidence_timestamp: int = Field(default_factory=utc_now, ge=0)

    @field_validator("node_id")
    @classmethod
    def node_identifier(cls, value: str) -> str:
        return clean_identifier(value)


class LinkError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str
    message: str
    retryable: bool = False


class ClientResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ok: bool
    envelope: Optional[HermesLinkEnvelope] = None
    status: Optional[HermesLinkStatus] = None
    queue: Tuple[HermesLinkEnvelope, ...] = ()
    error: Optional[LinkError] = None
