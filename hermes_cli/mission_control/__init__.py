"""Hermes Mission Control — telemetry visibility and operational telemetry.

A persistent, project-isolated, append-only telemetry journal that provides
read-only Mission Control visibility into agent, backlog, approval, evidence,
and promotion state.

Architecture mirrors ``hermes_cli.context_engine`` with Mission Control's own
domain models and store path (``$HERMES_HOME/mission_control/``).

Package-level imports (no context_engine dependency):
  models.py        — TelemetryEvent, AgentStateSnapshot, BacklogItemStateSnapshot,
                     ApprovalStateSnapshot, EvidenceStateSnapshot, PromotionStateSnapshot,
                     MissionControlSnapshot, helpers
  store.py         — MissionControlStore, get_store(), journal path helpers
  service.py       — MissionControlService, append/ingest/snapshot API
  adapters/        — context_adapter (ContextAdapter for context_engine ingestion)
"""

from hermes_cli.mission_control.models import (
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    TelemetryEvent,
    AgentStateSnapshot,
    BacklogItemStateSnapshot,
    ApprovalStateSnapshot,
    EvidenceStateSnapshot,
    PromotionStateSnapshot,
    MissionControlSnapshot,
    AgentState,
    BacklogItemState,
    ApprovalState,
    EvidenceState,
    PromotionState,
    TelemetrySeverity,
    format_timestamp,
    new_telemetry_event_id,
    new_agent_id,
    new_backlog_id,
    new_approval_id,
    new_evidence_id,
    new_promotion_id,
    utc_timestamp,
    parse_agent_state,
    parse_backlog_state,
    parse_approval_state,
    parse_evidence_state,
    parse_promotion_state,
    parse_telemetry_severity,
)
from hermes_cli.mission_control.store import (
    MissionControlStore,
    get_store,
    mission_control_root,
    event_log_path,
    project_event_log_path,
)
from hermes_cli.mission_control.service import (
    MissionControlService,
)
from hermes_cli.mission_control.adapters.context_adapter import (
    ContextAdapter,
)

__all__ = [
    # Schema
    "CURRENT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    # Domain models
    "TelemetryEvent",
    "AgentStateSnapshot",
    "BacklogItemStateSnapshot",
    "ApprovalStateSnapshot",
    "EvidenceStateSnapshot",
    "PromotionStateSnapshot",
    "MissionControlSnapshot",
    # Enums
    "AgentState",
    "BacklogItemState",
    "ApprovalState",
    "EvidenceState",
    "PromotionState",
    "TelemetrySeverity",
    # ID helpers
    "new_telemetry_event_id",
    "new_agent_id",
    "new_backlog_id",
    "new_approval_id",
    "new_evidence_id",
    "new_promotion_id",
    # Serialisation helpers
    "utc_timestamp",
    "format_timestamp",
    "parse_agent_state",
    "parse_backlog_state",
    "parse_approval_state",
    "parse_evidence_state",
    "parse_promotion_state",
    "parse_telemetry_severity",
    # Store
    "MissionControlStore",
    "get_store",
    "mission_control_root",
    "event_log_path",
    "project_event_log_path",
    # Service
    "MissionControlService",
    # Adapters
    "ContextAdapter",
]
