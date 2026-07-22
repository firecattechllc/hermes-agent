"""Foreman integration adapter — minimal seam for Hermes to ingest Foreman state.

Foreman is Hermes's execution engine for autonomous engineering tasks (launches,
evidence collection, validation, PR creation, promotion, and merge). This adapter
translates Foreman output into :mod:`hermes_cli.context_engine.models` entities
without embedding Foreman logic in the core context engine.

This adapter intentionally defines only the translation layer. It does NOT wire
deep Foreman behavior — that is deferred until Phase 3. The boundary is designed
so Foreman state can be imported as a structured payload without coupling the two
codebases.

Foreman state shapes this adapter handles:
- Launch execution record (task_id, backlog_id, status, evidence paths, commits)
- Selected engine / agent
- Fallback usage
- Branch and PR metadata
- Promotion state
- Failure reasons

Usage:
    adapter = ForemanAdapter(context_service)
    adapter.import_launch_from_foreman(
        project_id="proj_...",
        launch_id="laun_...",
        foreman_payload={...},  # See FOREMAN_PAYLOAD_SCHEMA below.
    )
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_cli.context_engine import models as m
from hermes_cli.context_engine.service import ContextService

logger = logging.getLogger("hermes.context_engine.foreman")

# ── Foreman payload schema ──────────────────────────────────────────────────
# Minimal set of fields Foreman emits that this adapter understands.
# Unknown fields are accepted but logged (forward compatibility).

FOREMAN_PAYLOAD_SCHEMA = {
    # Identity
    "launch_id": str,
    "task_id": (str, None.__class__),
    "backlog_id": (str, None.__class__),
    # Status
    "status": str,  # pending | running | complete | failed | cancelled
    "stage": (str, None.__class__),  # planning | implementation | validation | ...
    # Engine
    "selected_agents": list,
    # Evidence
    "evidence_paths": list,
    "commits": list,
    "branches": list,
    "pull_request_urls": list,
    "promotion_state": (str, None.__class__),
    "failure_reason": (str, None.__class__),
}


def _validate_payload(data: Dict[str, Any]) -> None:
    """Log warnings for unknown payload keys (forward compatibility)."""
    known = set(FOREMAN_PAYLOAD_SCHEMA)
    unknown = set(data.keys()) - known
    for key in sorted(unknown):
        logger.debug("foreman adapter: unknown payload key %r (ignored)", key)


# ── Adapter class ────────────────────────────────────────────────────────────

class ForemanAdapter:
    """Translates Foreman execution state into Hermes context records.

    Instantiate with a :class:`ContextService` to persist translated state.
    """

    def __init__(self, service: ContextService) -> None:
        self._service = service

    def import_launch_from_foreman(
        self,
        project_id: str,
        foreman_payload: Dict[str, Any],
        actor: str = "foreman",
    ) -> m.LaunchContext:
        """Import or update a launch from a Foreman execution payload.

        If the launch_id exists, the record is updated (versioned in the journal).
        If it does not exist, a new record is created.
        """
        _validate_payload(foreman_payload)
        payload = foreman_payload

        launch_id = payload.get("launch_id")
        if not launch_id:
            raise ValueError("foreman_payload must include launch_id")

        task_id = payload.get("task_id")
        backlog_id = payload.get("backlog_id")
        selected_agents = payload.get("selected_agents", [])

        # Status mapping.
        raw_status = payload.get("status", "pending")
        try:
            status = m.parse_launch_status(raw_status)
        except ValueError:
            logger.warning("unknown foreman status %r, treating as pending", raw_status)
            status = m.LaunchStatus.PENDING

        # Stage mapping.
        raw_stage = payload.get("stage")
        stage: Optional[m.LaunchStage] = None
        if raw_stage is not None:
            try:
                stage = m.parse_launch_stage(raw_stage)
            except ValueError:
                logger.warning("unknown foreman stage %r, skipping", raw_stage)

        # Check for existing launch.
        existing = None
        try:
            launches = self._service.list_launches(project_id)
            for launch in reversed(launches):
                if launch.launch_id == launch_id:
                    existing = launch
                    break
        except ValueError:
            pass

        if existing is None:
            # Create new launch.
            launch = m.LaunchContext(
                launch_id=launch_id,
                project_id=project_id,
                task_id=task_id,
                backlog_id=backlog_id,
                stage=stage or m.LaunchStage.PLANNING,
                selected_agents=list(selected_agents),
                status=status,
                evidence_refs=list(payload.get("evidence_paths", [])),
                commits=list(payload.get("commits", [])),
                branches=list(payload.get("branches", [])),
                pull_request_urls=list(payload.get("pull_request_urls", [])),
                promotion_state=payload.get("promotion_state"),
                failure_reason=payload.get("failure_reason"),
                started_at=m._utc_now(),
                updated_at=m._utc_now(),
            )
            self._service._store.save_launch(launch)
            self._service._store.append_event(m.ContextEvent(
                event_id=m.new_event_id(),
                event_type="launch_started",
                project_id=project_id,
                actor=actor,
                payload={"launch_id": launch_id, "source": "foreman"},
            ))
            logger.info("imported launch %s from foreman (new)", launch_id)
            return launch

        # Update existing launch.
        return self._service.update_launch(
            project_id=project_id,
            launch_id=launch_id,
            stage=stage,
            status=status,
            evidence_refs=payload.get("evidence_paths"),
            commits=payload.get("commits"),
            branches=payload.get("branches"),
            pull_request_urls=payload.get("pull_request_urls"),
            promotion_state=payload.get("promotion_state"),
            failure_reason=payload.get("failure_reason"),
            actor=actor,
        )

    def translate_foreman_evidence(
        self,
        evidence_paths: List[str],
    ) -> List[m.SourceReference]:
        """Translate Foreman evidence paths into provenance source references."""
        refs: List[m.SourceReference] = []
        for path in evidence_paths:
            # Foreman evidence paths are workspace-relative or absolute.
            # We capture them as-is with a content hash where available.
            ref = m.SourceReference(
                source_type="foreman_evidence",
                source_identifier=str(path),
                captured_at=m._utc_now(),
                metadata={},
            )
            refs.append(ref)
        return refs

    def translate_foreman_commits(
        self,
        commits: List[str],
    ) -> List[m.SourceReference]:
        """Translate Foreman commit SHAs into provenance references."""
        refs: List[m.SourceReference] = []
        for sha in commits:
            ref = m.SourceReference(
                source_type="git_commit",
                source_identifier=str(sha),
                captured_at=m._utc_now(),
            )
            refs.append(ref)
        return refs

    def translate_foreman_prs(
        self,
        urls: List[str],
    ) -> List[m.SourceReference]:
        """Translate Foreman pull request URLs into provenance references."""
        refs: List[m.SourceReference] = []
        for url in urls:
            ref = m.SourceReference(
                source_type="pull_request",
                source_identifier=str(url),
                captured_at=m._utc_now(),
            )
            refs.append(ref)
        return refs
