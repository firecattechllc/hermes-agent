"""Hermes Shared Engineering Context.

A persistent, project-isolated, audit-logged engineering context service that
Hermes, Codex, Claude, Foreman, and future specialized agents can read from and
contribute to through explicit interfaces.

Architecture:
  models.py        — Pydantic domain models (Project, ContextRecord, LaunchContext, ContextEvent)
  store.py         — Local JSON + JSONL persistence (atomic writes, append-only journal)
  service.py       — Application layer with business logic and event emission
  renderer.py      — Agent context renderer with role-based filtering
  foreman_adapter.py — Foreman integration boundary

CLI entry point: ``hermes context`` (see hermes_cli/context_commands.py)
"""

from hermes_cli.context_engine.models import (
    CURRENT_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ContextEvent,
    ContextRecord,
    ContextSnapshot,
    LaunchContext,
    Project,
    ProjectStatus,
    RecordStatus,
    RecordType,
    SourceReference,
    format_timestamp,
    new_event_id,
    new_launch_id,
    new_project_id,
    new_record_id,
    parse_launch_stage,
    parse_launch_status,
    parse_project_status,
    parse_record_status,
    parse_record_type,
    utc_timestamp,
)
from hermes_cli.context_engine.renderer import (
    AgentContextPackage,
    export_json,
    export_json_file,
    render_context,
)
from hermes_cli.context_engine.service import ContextService
from hermes_cli.context_engine.store import ContextStore, get_store

__all__ = [
    # Models
    "CURRENT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "Project",
    "ContextRecord",
    "LaunchContext",
    "ContextEvent",
    "ContextSnapshot",
    "SourceReference",
    "RecordType",
    "RecordStatus",
    "ProjectStatus",
    # Helpers
    "new_project_id",
    "new_record_id",
    "new_launch_id",
    "new_event_id",
    "parse_record_type",
    "parse_record_status",
    "parse_project_status",
    "parse_launch_stage",
    "parse_launch_status",
    "format_timestamp",
    "utc_timestamp",
    # Store & service
    "ContextStore",
    "get_store",
    "ContextService",
    # Renderer
    "render_context",
    "export_json",
    "export_json_file",
    "AgentContextPackage",
]
