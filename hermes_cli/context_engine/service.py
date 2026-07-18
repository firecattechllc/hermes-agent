"""Application-layer service for Hermes Shared Engineering Context.

This layer contains all business logic, event emission, and the public API used
by CLI commands, agent renderers, and Foreman integration. The store layer below
handles only persistence; this layer handles identity, validation, and
coordination.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

from hermes_cli.context_engine import models as m
from hermes_cli.context_engine.store import ContextStore, get_store

logger = logging.getLogger("hermes.context_engine.service")

# ── Service class ────────────────────────────────────────────────────────────

class ContextService:
    """Application service for Hermes Shared Engineering Context.

    All methods return domain objects (or raise typed exceptions) and emit
    audit events for every mutation.

    Args:
        store: Explicit store instance. Defaults to the global store.
               Intended for tests and multi-store scenarios.
    """

    def __init__(self, store: Optional[ContextStore] = None) -> None:
        self._store = store or get_store()

    # ── Project management ─────────────────────────────────────────────────

    def register_project(
        self,
        display_name: str,
        *,
        project_id: Optional[str] = None,
        repository_identity: Optional[str] = None,
        local_path: Optional[str] = None,
        default_branch: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> m.Project:
        """Register a project idempotently.

        If the project already exists with the same repository_identity, return
        the existing record (idempotent). If repository_identity differs, raise
        ValueError.
        """
        project_id = project_id or m.new_project_id()

        existing = self._store.get_project(project_id)
        if existing is not None:
            if repository_identity and existing.repository_identity:
                if existing.repository_identity != repository_identity:
                    raise ValueError(
                        f"project {project_id} already registered for "
                        f"{existing.repository_identity!r}, cannot re-register for "
                        f"{repository_identity!r}"
                    )
            logger.debug("project %s already exists, returning existing", project_id)
            return existing

        project = m.Project(
            project_id=project_id,
            display_name=display_name,
            repository_identity=repository_identity,
            local_path=local_path,
            default_branch=default_branch,
            created_at=m._utc_now(),
            updated_at=m._utc_now(),
        )
        self._store.save_project(project)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="project_registered",
            project_id=project_id,
            actor=actor,
            payload={
                "display_name": display_name,
                "repository_identity": repository_identity,
                "local_path": local_path,
            },
        ))
        logger.info("registered project %s (%s)", project_id, display_name)
        return project

    def get_project(self, project_id: str) -> Optional[m.Project]:
        return self._store.get_project(project_id)

    def list_projects(self) -> List[m.Project]:
        return self._store.list_projects()

    def archive_project(self, project_id: str, actor: Optional[str] = None) -> m.Project:
        """Archive a project (soft delete — state preserved in journal)."""
        project = self._store.get_project(project_id)
        if project is None:
            raise ValueError(f"no such project: {project_id}")
        if project.status == m.ProjectStatus.ARCHIVED:
            return project

        updated = m.Project(
            **project.model_dump(),
            status=m.ProjectStatus.ARCHIVED,
            updated_at=m._utc_now(),
        )
        self._store.save_project(updated)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="project_archived",
            project_id=project_id,
            actor=actor,
            payload={"previous_status": project.status.value},
        ))
        return updated

    # ── Context record management ──────────────────────────────────────────

    def add_record(
        self,
        project_id: str,
        record_type: m.RecordType,
        title: str,
        *,
        body: Optional[str] = None,
        structured_payload: Optional[Dict] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        source_refs: Optional[List[m.SourceReference]] = None,
        related: Optional[List[str]] = None,
        created_by: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> m.ContextRecord:
        """Add a new context record to a project."""
        if self._store.get_project(project_id) is None:
            raise ValueError(f"no such project: {project_id}")

        record = m.ContextRecord(
            record_id=m.new_record_id(),
            project_id=project_id,
            record_type=record_type,
            title=title,
            body=body,
            structured_payload=structured_payload,
            confidence=confidence,
            tags=tags or [],
            source_refs=source_refs or [],
            related=related or [],
            created_by=created_by or actor,
            created_at=m._utc_now(),
            updated_at=m._utc_now(),
        )
        self._store.save_record(record)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="record_created",
            project_id=project_id,
            actor=actor,
            payload={
                "record_id": record.record_id,
                "record_type": record.record_type.value,
                "title": title,
            },
        ))
        logger.info(
            "added %s record %s to project %s",
            record.record_type.value,
            record.record_id,
            project_id,
        )
        return record

    def list_records(
        self,
        project_id: str,
        record_type: Optional[m.RecordType] = None,
        status: Optional[m.RecordStatus] = None,
        include_inactive: bool = False,
    ) -> List[m.ContextRecord]:
        """List records for a project."""
        return self._store.list_records(
            project_id,
            record_type=record_type,
            status=status,
            include_inactive=include_inactive,
        )

    def get_record(self, project_id: str, record_id: str) -> Optional[m.ContextRecord]:
        """Get the latest version of a specific record."""
        records = self._store.list_records(project_id, include_inactive=True)
        for rec in reversed(records):
            if rec.record_id == record_id:
                return rec
        return None

    def update_record(
        self,
        project_id: str,
        record_id: str,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        structured_payload: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
        source_refs: Optional[List[m.SourceReference]] = None,
        actor: Optional[str] = None,
    ) -> m.ContextRecord:
        """Update an existing record (creates a new version in the journal)."""
        records = self._store.list_records(project_id, include_inactive=True)
        existing = None
        for rec in reversed(records):
            if rec.record_id == record_id:
                existing = rec
                break
        if existing is None:
            raise ValueError(f"no such record: {record_id}")

        updated = m.ContextRecord(
            **existing.model_dump(),
            title=title if title is not None else existing.title,
            body=body if body is not None else existing.body,
            structured_payload=(
                structured_payload
                if structured_payload is not None
                else existing.structured_payload
            ),
            tags=tags if tags is not None else existing.tags,
            source_refs=source_refs if source_refs is not None else existing.source_refs,
            updated_at=m._utc_now(),
        )
        self._store.save_record(updated)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="record_updated",
            project_id=project_id,
            actor=actor,
            payload={"record_id": record_id},
        ))
        return updated

    def supersede_record(
        self,
        project_id: str,
        record_id: str,
        new_record: m.ContextRecord,
        actor: Optional[str] = None,
    ) -> m.ContextRecord:
        """Mark a record as superseded by a new record (original preserved)."""
        records = self._store.list_records(project_id, include_inactive=True)
        existing = None
        for rec in reversed(records):
            if rec.record_id == record_id:
                existing = rec
                break
        if existing is None:
            raise ValueError(f"no such record: {record_id}")

        # Mark existing as deprecated.
        deprecated = m.ContextRecord(
            **existing.model_dump(),
            status=m.RecordStatus.DEPRECATED,
            updated_at=m._utc_now(),
        )
        self._store.save_record(deprecated)

        # Write new record with supersedes link.
        new_with_supersedes = m.ContextRecord(
            **new_record.model_dump(),
            supersedes=new_record.supersedes + [record_id],
        )
        self._store.save_record(new_with_supersedes)

        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="record_superseded",
            project_id=project_id,
            actor=actor,
            payload={
                "superseded_id": record_id,
                "replacement_id": new_with_supersedes.record_id,
            },
        ))
        return new_with_supersedes

    def change_record_status(
        self,
        project_id: str,
        record_id: str,
        new_status: m.RecordStatus,
        actor: Optional[str] = None,
    ) -> m.ContextRecord:
        """Change a record's status (resolved, deprecated, invalidated, active)."""
        records = self._store.list_records(project_id, include_inactive=True)
        existing = None
        for rec in reversed(records):
            if rec.record_id == record_id:
                existing = rec
                break
        if existing is None:
            raise ValueError(f"no such record: {record_id}")

        updated = m.ContextRecord(
            **existing.model_dump(),
            status=new_status,
            updated_at=m._utc_now(),
        )
        self._store.save_record(updated)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="record_status_changed",
            project_id=project_id,
            actor=actor,
            payload={
                "record_id": record_id,
                "previous_status": existing.status.value,
                "new_status": new_status.value,
            },
        ))
        return updated

    # ── Launch management ───────────────────────────────────────────────────

    def start_launch(
        self,
        project_id: str,
        launch_id: Optional[str] = None,
        task_id: Optional[str] = None,
        backlog_id: Optional[str] = None,
        selected_agents: Optional[List[str]] = None,
        actor: Optional[str] = None,
    ) -> m.LaunchContext:
        """Start a new launch/execution record."""
        if self._store.get_project(project_id) is None:
            raise ValueError(f"no such project: {project_id}")

        launch = m.LaunchContext(
            launch_id=launch_id or m.new_launch_id(),
            project_id=project_id,
            task_id=task_id,
            backlog_id=backlog_id,
            stage=m.LaunchStage.PLANNING,
            selected_agents=selected_agents or [],
            status=m.LaunchStatus.PENDING,
            started_at=m._utc_now(),
            updated_at=m._utc_now(),
        )
        self._store.save_launch(launch)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="launch_started",
            project_id=project_id,
            actor=actor,
            payload={
                "launch_id": launch.launch_id,
                "task_id": task_id,
                "backlog_id": backlog_id,
            },
        ))
        return launch

    def update_launch(
        self,
        project_id: str,
        launch_id: str,
        *,
        stage: Optional[m.LaunchStage] = None,
        status: Optional[m.LaunchStatus] = None,
        evidence_refs: Optional[List[str]] = None,
        commits: Optional[List[str]] = None,
        branches: Optional[List[str]] = None,
        pull_request_urls: Optional[List[str]] = None,
        promotion_state: Optional[str] = None,
        failure_reason: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> m.LaunchContext:
        """Update a launch record."""
        launches = self._store.list_launches(project_id, status=None)
        existing = None
        for launch in reversed(launches):
            if launch.launch_id == launch_id:
                existing = launch
                break
        if existing is None:
            raise ValueError(f"no such launch: {launch_id}")

        now = m._utc_now()
        updated = m.LaunchContext(
            **existing.model_dump(),
            stage=stage if stage is not None else existing.stage,
            status=status if status is not None else existing.status,
            evidence_refs=evidence_refs if evidence_refs is not None else existing.evidence_refs,
            commits=commits if commits is not None else existing.commits,
            branches=branches if branches is not None else existing.branches,
            pull_request_urls=(
                pull_request_urls
                if pull_request_urls is not None
                else existing.pull_request_urls
            ),
            promotion_state=(
                promotion_state
                if promotion_state is not None
                else existing.promotion_state
            ),
            failure_reason=(
                failure_reason
                if failure_reason is not None
                else existing.failure_reason
            ),
            updated_at=now,
            completed_at=now if (status and status in {
                m.LaunchStatus.COMPLETE,
                m.LaunchStatus.FAILED,
                m.LaunchStatus.CANCELLED,
            }) else existing.completed_at,
        )
        self._store.save_launch(updated)
        self._store.append_event(m.ContextEvent(
            event_id=m.new_event_id(),
            event_type="launch_updated",
            project_id=project_id,
            actor=actor,
            payload={"launch_id": launch_id, "status": updated.status.value},
        ))
        return updated

    def list_launches(
        self,
        project_id: str,
        status: Optional[m.LaunchStatus] = None,
    ) -> List[m.LaunchContext]:
        return self._store.list_launches(project_id, status=status)

    # ── Snapshot ───────────────────────────────────────────────────────────

    def build_snapshot(
        self,
        project_id: str,
        generated_by: Optional[str] = None,
    ) -> m.ContextSnapshot:
        """Build a deterministic snapshot for a project."""
        return self._store.build_snapshot(project_id, generated_by=generated_by)

    # ── Audit ──────────────────────────────────────────────────────────────

    def list_events(
        self,
        project_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[m.ContextEvent]:
        """List audit events, optionally scoped to a project."""
        events = list(self._store.iter_events(project_id=project_id))
        if limit is not None:
            events = events[-limit:]
        return events
