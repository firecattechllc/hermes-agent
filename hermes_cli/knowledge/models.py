"""Strict Step 33 knowledge graph domain and federation models."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_SENSITIVE_KEYS = re.compile(
    r"(?i)(api[_-]?key|authorization|credential|password|private[_-]?key|secret|token)"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}:{stable_hash([str(part).strip().lower() for part in parts])[:32]}"


def _bounded_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError("payload nesting exceeds safe bound")
    if isinstance(value, Mapping):
        if len(value) > 200:
            raise ValueError("payload contains too many fields")
        result = {}
        for key, child in value.items():
            key = str(key)
            if _SENSITIVE_KEYS.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = _bounded_safe(child, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 1000:
            raise ValueError("payload contains too many items")
        return [_bounded_safe(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return value[:4096]
    if isinstance(value, (int, float, bool, type(None))):
        return value
    raise ValueError("payload contains a non-JSON value")


class TrustLevel(str, Enum):
    UNVERIFIED = "unverified"
    OBSERVED = "observed"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"


class LifecycleState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    STALE = "stale"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class RelationshipType(str, Enum):
    HOSTS = "HOSTS"
    RUNS = "RUNS"
    DEPLOYED_ON = "DEPLOYED_ON"
    DEPENDS_ON = "DEPENDS_ON"
    USES = "USES"
    CONNECTED_TO = "CONNECTED_TO"
    STORES = "STORES"
    EXPOSES = "EXPOSES"
    SCHEDULES = "SCHEDULES"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    BACKED_UP_TO = "BACKED_UP_TO"
    MANAGED_BY = "MANAGED_BY"
    PART_OF = "PART_OF"
    COMMUNICATES_WITH = "COMMUNICATES_WITH"


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    STALE = "stale"
    RESTORED = "restored"
    CONFLICT = "conflict"


class ChangeSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def aware_datetimes(cls, value: Any, info):
        if info.field_name.endswith("_at") and value is not None:
            parsed = (
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if isinstance(value, str)
                else value
            )
            if isinstance(parsed, datetime):
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError(f"{info.field_name} must be timezone-aware")
                return parsed.astimezone(timezone.utc)
        return value


class KnowledgeEntity(_StrictModel):
    entity_id: str
    entity_type: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=512)
    canonical_name: str = Field(..., min_length=1, max_length=512)
    node_id: Optional[str] = None
    host_id: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=1024)
    purpose: Optional[str] = Field(default=None, max_length=1024)
    owner: Optional[str] = Field(default=None, max_length=256)
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    operational_status: str = Field(default="unknown", max_length=64)
    version: Optional[str] = Field(default=None, max_length=256)
    attributes: dict[str, Any] = Field(default_factory=dict)
    labels: Tuple[str, ...] = ()
    trust_level: TrustLevel = TrustLevel.OBSERVED
    confidence: float = Field(default=0.8, ge=0, le=1)
    first_seen_at: datetime
    last_seen_at: datetime
    observed_at: datetime
    evidence_refs: Tuple[str, ...] = ()
    source_collectors: Tuple[str, ...]

    @field_validator("attributes")
    @classmethod
    def safe_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_safe(value)

    @field_validator("labels", "evidence_refs", "source_collectors")
    @classmethod
    def sorted_unique(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def valid_times(self) -> "KnowledgeEntity":
        if (
            self.first_seen_at > self.last_seen_at
            or self.observed_at > self.last_seen_at
        ):
            raise ValueError("entity observation timestamps are inconsistent")
        return self


class KnowledgeRelationship(_StrictModel):
    relationship_id: str
    source_entity_id: str
    relationship_type: RelationshipType
    target_entity_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    trust_level: TrustLevel = TrustLevel.OBSERVED
    confidence: float = Field(default=0.8, ge=0, le=1)
    first_seen_at: datetime
    last_seen_at: datetime
    observed_at: datetime
    evidence_refs: Tuple[str, ...] = ()
    source_collectors: Tuple[str, ...]

    @field_validator("attributes")
    @classmethod
    def safe_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_safe(value)

    @model_validator(mode="after")
    def valid_edge(self) -> "KnowledgeRelationship":
        expected = stable_id(
            "rel",
            self.source_entity_id,
            self.relationship_type.value,
            self.target_entity_id,
        )
        if self.relationship_id != expected:
            raise ValueError("relationship_id is not deterministic")
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("self relationships are not accepted")
        return self


class DiscoveryEvidence(_StrictModel):
    evidence_id: str
    collector: str
    node_id: str
    collected_at: datetime
    source_kind: str
    source_locator: str = Field(..., max_length=1024)
    content_hash: str = Field(..., min_length=64, max_length=64)
    summary: str = Field(..., max_length=2048)
    raw_record: Optional[dict[str, Any]] = None
    payload_ref: Optional[str] = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    sensitivity: str = Field(default="internal", max_length=64)
    schema_version: int = SCHEMA_VERSION

    @field_validator("raw_record")
    @classmethod
    def safe_raw(cls, value: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return None if value is None else _bounded_safe(value)

    @model_validator(mode="after")
    def bounded_reference(self) -> "DiscoveryEvidence":
        if (self.raw_record is None) == (self.payload_ref is None):
            raise ValueError(
                "evidence requires exactly one bounded record or payload reference"
            )
        return self


class CollectorResult(_StrictModel):
    collector_id: str
    success: bool
    duration_ms: int = Field(ge=0)
    entity_ids: Tuple[str, ...] = ()
    relationship_ids: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    warning: Optional[str] = Field(default=None, max_length=1024)
    error: Optional[str] = Field(default=None, max_length=1024)


class DiscoverySnapshot(_StrictModel):
    snapshot_id: str
    node_id: str
    started_at: datetime
    completed_at: datetime
    collector_results: Tuple[CollectorResult, ...]
    entity_count: int = Field(ge=0)
    relationship_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    warnings: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION


class GraphChange(_StrictModel):
    change_id: str
    change_type: ChangeType
    entity_id: Optional[str] = None
    relationship_id: Optional[str] = None
    prior_hash: Optional[str] = None
    current_hash: Optional[str] = None
    detected_at: datetime
    severity: ChangeSeverity
    summary: str = Field(..., max_length=2048)
    changed_fields: Tuple[str, ...] = ()
    evidence_refs: Tuple[str, ...] = ()
    requires_approval: bool = False
    acknowledged: bool = False

    @model_validator(mode="after")
    def one_subject(self) -> "GraphChange":
        if (self.entity_id is None) == (self.relationship_id is None):
            raise ValueError("change must reference exactly one graph subject")
        return self


DriftEvent = GraphChange


class EvidencePath(_StrictModel):
    entity_ids: Tuple[str, ...]
    relationship_ids: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]


class ImpactAnalysis(_StrictModel):
    subject_id: str
    scenario: str
    upstream_dependencies: Tuple[str, ...]
    downstream_dependents: Tuple[str, ...]
    affected_capabilities: Tuple[str, ...]
    unaffected_capabilities: Tuple[str, ...] = ()
    uncertainty: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]
    traversal_depth: int = Field(ge=0)
    paths: Tuple[EvidencePath, ...]
    generated_at: datetime


class FederationMessageType(str, Enum):
    DISCOVERY_SNAPSHOT_SUMMARY = "discovery_snapshot_summary"
    DISCOVERY_CHANGE_BATCH = "discovery_change_batch"
    GRAPH_SYNC_REQUEST = "graph_sync_request"
    GRAPH_SYNC_RESPONSE = "graph_sync_response"
    EVIDENCE_REQUEST = "evidence_request"
    EVIDENCE_RESPONSE = "evidence_response"


class FederationPayload(_StrictModel):
    message_type: FederationMessageType
    records: Tuple[dict[str, Any], ...] = ()
    cursor: Optional[str] = None
    complete: bool = True

    @field_validator("records")
    @classmethod
    def bounded_records(
        cls, value: Tuple[dict[str, Any], ...]
    ) -> Tuple[dict[str, Any], ...]:
        if len(value) > 500:
            raise ValueError("federation batch exceeds 500 records")
        return tuple(_bounded_safe(item) for item in value)


class KnowledgeFederationEnvelope(_StrictModel):
    schema_version: int = SCHEMA_VERSION
    sender_node: str
    recipient_node: str
    message_id: str
    correlation_id: str
    created_at: datetime
    content_hash: str = Field(..., min_length=64, max_length=64)
    payload: FederationPayload

    @classmethod
    def build(cls, **values: Any) -> "KnowledgeFederationEnvelope":
        payload = values["payload"]
        digest = stable_hash(payload)
        return cls(content_hash=digest, **values)

    @model_validator(mode="after")
    def integrity(self) -> "KnowledgeFederationEnvelope":
        if self.sender_node == self.recipient_node:
            raise ValueError("federation sender and recipient must differ")
        if self.content_hash != stable_hash(self.payload):
            raise ValueError("federation content hash mismatch")
        return self
