"""Governed lifecycle operations for the autonomous engineering backlog."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from hermes_cli.autonomous_backlog import models as m
from hermes_cli.autonomous_backlog.store import (
    AutonomousBacklogStore,
    get_store,
)


_TERMINAL_STATUSES = {
    m.BacklogStatus.COMPLETED,
    m.BacklogStatus.CANCELLED,
    m.BacklogStatus.SUPERSEDED,
    m.BacklogStatus.FAILED,
}


_ALLOWED_TRANSITIONS: Dict[
    m.BacklogStatus,
    set[m.BacklogStatus],
] = {
    m.BacklogStatus.CANDIDATE: {
        m.BacklogStatus.TRIAGED,
        m.BacklogStatus.APPROVED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
    },
    m.BacklogStatus.TRIAGED: {
        m.BacklogStatus.APPROVED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
    },
    m.BacklogStatus.APPROVED: {
        m.BacklogStatus.SCHEDULED,
        m.BacklogStatus.CLAIMED,
        m.BacklogStatus.EXECUTING,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
    },
    m.BacklogStatus.SCHEDULED: {
        m.BacklogStatus.CLAIMED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
    },
    m.BacklogStatus.CLAIMED: {
        m.BacklogStatus.PLANNING,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.UNKNOWN,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.PLANNING: {
        m.BacklogStatus.EXECUTING,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.UNKNOWN,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.EXECUTING: {
        m.BacklogStatus.VERIFYING,
        m.BacklogStatus.COMPLETED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.UNKNOWN,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.VERIFYING: {
        m.BacklogStatus.AWAITING_APPROVAL,
        m.BacklogStatus.COMPLETED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.UNKNOWN,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.AWAITING_APPROVAL: {
        m.BacklogStatus.COMPLETED,
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.BLOCKED: {
        m.BacklogStatus.TRIAGED,
        m.BacklogStatus.APPROVED,
        m.BacklogStatus.SCHEDULED,
        m.BacklogStatus.CLAIMED,
        m.BacklogStatus.PLANNING,
        m.BacklogStatus.EXECUTING,
        m.BacklogStatus.VERIFYING,
        m.BacklogStatus.CANCELLED,
        m.BacklogStatus.SUPERSEDED,
    },
    m.BacklogStatus.UNKNOWN: {
        m.BacklogStatus.BLOCKED,
        m.BacklogStatus.FAILED,
        m.BacklogStatus.CANCELLED,
    },
    m.BacklogStatus.COMPLETED: set(),
    m.BacklogStatus.FAILED: set(),
    m.BacklogStatus.CANCELLED: set(),
    m.BacklogStatus.SUPERSEDED: set(),
}


_EVENT_FOR_STATUS: Dict[
    m.BacklogStatus,
    m.BacklogEventType,
] = {
    m.BacklogStatus.TRIAGED: m.BacklogEventType.TRIAGED,
    m.BacklogStatus.APPROVED: m.BacklogEventType.APPROVED,
    m.BacklogStatus.SCHEDULED: m.BacklogEventType.SCHEDULED,
    m.BacklogStatus.CLAIMED: m.BacklogEventType.CLAIMED,
    m.BacklogStatus.PLANNING: m.BacklogEventType.PLANNING_STARTED,
    m.BacklogStatus.EXECUTING: m.BacklogEventType.EXECUTION_STARTED,
    m.BacklogStatus.VERIFYING: m.BacklogEventType.VERIFICATION_STARTED,
    m.BacklogStatus.AWAITING_APPROVAL: (
        m.BacklogEventType.APPROVAL_REQUESTED
    ),
    m.BacklogStatus.BLOCKED: m.BacklogEventType.BLOCKED,
    m.BacklogStatus.COMPLETED: m.BacklogEventType.COMPLETED,
    m.BacklogStatus.FAILED: m.BacklogEventType.FAILED,
    m.BacklogStatus.UNKNOWN: m.BacklogEventType.MARKED_UNKNOWN,
    m.BacklogStatus.CANCELLED: m.BacklogEventType.CANCELLED,
    m.BacklogStatus.SUPERSEDED: m.BacklogEventType.SUPERSEDED,
}


def _normalise_text_list(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []

    for value in values:
        normalised = value.strip()

        if normalised and normalised not in seen:
            seen.add(normalised)
            output.append(normalised)

    return output


class AutonomousBacklogService:
    """Governed application service for durable backlog lifecycle changes."""

    def __init__(
        self,
        store: Optional[AutonomousBacklogStore] = None,
    ) -> None:
        self._store = store or get_store()

    @property
    def store(self) -> AutonomousBacklogStore:
        return self._store

    def create_item(
        self,
        *,
        project_id: str,
        title: str,
        description: str,
        source: m.BacklogSource,
        actor: Optional[str] = None,
        item_id: Optional[str] = None,
        priority: m.BacklogPriority = m.BacklogPriority.NORMAL,
        risk_level: m.BacklogRiskLevel = m.BacklogRiskLevel.MEDIUM,
        dependencies: Optional[Iterable[str]] = None,
        blocked_by: Optional[Iterable[str]] = None,
        acceptance_criteria: Optional[Iterable[str]] = None,
        evidence_requirements: Optional[
            Iterable[m.EvidenceRequirement]
        ] = None,
        required_capabilities: Optional[Iterable[str]] = None,
        allowed_paths: Optional[Iterable[str]] = None,
        denied_paths: Optional[Iterable[str]] = None,
        execution_policy_id: Optional[str] = None,
        schedule_policy: Optional[m.SchedulePolicy] = None,
        retry_policy: Optional[m.RetryPolicy] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        created_at: Optional[int] = None,
    ) -> m.BacklogItem:
        """Create and persist one candidate backlog item."""
        identifier = item_id or m.new_backlog_item_id()

        if self._store.get_item(project_id, identifier) is not None:
            raise ValueError(
                f"backlog item {identifier} already exists "
                f"in project {project_id}"
            )

        item_data: Dict[str, Any] = {
            "item_id": identifier,
            "project_id": project_id,
            "title": title,
            "description": description,
            "status": m.BacklogStatus.CANDIDATE,
            "priority": priority,
            "risk_level": risk_level,
            "source": source,
            "dependencies": _normalise_text_list(
                dependencies or []
            ),
            "blocked_by": _normalise_text_list(
                blocked_by or []
            ),
            "acceptance_criteria": _normalise_text_list(
                acceptance_criteria or []
            ),
            "evidence_requirements": list(
                evidence_requirements or []
            ),
            "required_capabilities": _normalise_text_list(
                required_capabilities or []
            ),
            "allowed_paths": _normalise_text_list(
                allowed_paths or []
            ),
            "denied_paths": _normalise_text_list(
                denied_paths or []
            ),
            "execution_policy_id": execution_policy_id,
            "schedule_policy": (
                schedule_policy or m.SchedulePolicy()
            ),
            "retry_policy": (
                retry_policy or m.RetryPolicy()
            ),
            "created_by": actor,
            "correlation_id": correlation_id,
            "version": 1,
        }

        if created_at is not None:
            item_data["created_at"] = created_at
            item_data["updated_at"] = created_at

        item = m.BacklogItem(**item_data)

        event = m.BacklogEvent(
            event_id=m.new_backlog_event_id(),
            event_type=m.BacklogEventType.CREATED,
            project_id=item.project_id,
            item_id=item.item_id,
            timestamp=item.created_at,
            sequence=1,
            actor=actor,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            expected_version=0,
            resulting_version=1,
            payload={
                "item": item.model_dump(mode="json"),
            },
        )

        if idempotency_key is None:
            self._store.append_event(event)
            return item

        appended = self._store.append_event_once(event)

        if appended is not None:
            return item

        existing = self._find_by_idempotency_key(
            project_id,
            idempotency_key,
        )

        if existing is None:
            raise ValueError(
                "idempotent create was rejected but no existing "
                "backlog item could be resolved"
            )

        return existing

    def approve_item(
        self,
        project_id: str,
        item_id: str,
        *,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.APPROVED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
        )

    def schedule_item(
        self,
        project_id: str,
        item_id: str,
        *,
        schedule_policy: m.SchedulePolicy,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        if schedule_policy.mode != m.ScheduleMode.SCHEDULED:
            raise ValueError(
                "scheduling an item requires scheduled mode"
            )

        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.SCHEDULED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
            changes={
                "schedule_policy": schedule_policy,
            },
        )

    def start_item(
        self,
        project_id: str,
        item_id: str,
        *,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.EXECUTING,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
        )

    def block_item(
        self,
        project_id: str,
        item_id: str,
        *,
        reason: str,
        blocked_by: Optional[Iterable[str]] = None,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        changes: Dict[str, Any] = {
            "blocked_reason": reason,
        }

        if blocked_by is not None:
            changes["blocked_by"] = _normalise_text_list(
                blocked_by
            )

        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.BLOCKED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
            changes=changes,
        )

    def fail_item(
        self,
        project_id: str,
        item_id: str,
        *,
        reason: str,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.FAILED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
            changes={
                "failure_reason": reason,
            },
        )

    def complete_item(
        self,
        project_id: str,
        item_id: str,
        *,
        evidence_refs: Iterable[str],
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.COMPLETED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
            changes={
                "evidence_refs": _normalise_text_list(
                    evidence_refs
                ),
            },
        )

    def cancel_item(
        self,
        project_id: str,
        item_id: str,
        *,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.CANCELLED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
        )

    def supersede_item(
        self,
        project_id: str,
        item_id: str,
        *,
        superseded_by: str,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
    ) -> m.BacklogItem:
        replacement = self._store.get_item(
            project_id,
            superseded_by,
        )

        if replacement is None:
            raise ValueError(
                f"replacement backlog item {superseded_by} "
                f"does not exist in project {project_id}"
            )

        return self.transition_item(
            project_id,
            item_id,
            target_status=m.BacklogStatus.SUPERSEDED,
            actor=actor,
            expected_version=expected_version,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            updated_at=updated_at,
            changes={
                "superseded_by": superseded_by,
            },
        )

    def transition_item(
        self,
        project_id: str,
        item_id: str,
        *,
        target_status: m.BacklogStatus,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        updated_at: Optional[int] = None,
        changes: Optional[Dict[str, Any]] = None,
    ) -> m.BacklogItem:
        """Apply one validated lifecycle transition."""
        current = self._store.get_item(project_id, item_id)

        if current is None:
            raise ValueError(
                f"backlog item {item_id} does not exist "
                f"in project {project_id}"
            )

        if idempotency_key is not None:
            existing = self._find_by_idempotency_key(
                project_id,
                idempotency_key,
            )

            if existing is not None:
                return existing

        if current.status in _TERMINAL_STATUSES:
            raise ValueError(
                f"terminal backlog item {item_id} cannot transition "
                f"from {current.status.value}"
            )

        allowed = _ALLOWED_TRANSITIONS.get(
            current.status,
            set(),
        )

        if target_status not in allowed:
            raise ValueError(
                f"invalid backlog transition: "
                f"{current.status.value} -> {target_status.value}"
            )

        if (
            expected_version is not None
            and current.version != expected_version
        ):
            raise ValueError(
                f"backlog version conflict for {item_id}: "
                f"expected {expected_version}, current "
                f"{current.version}"
            )

        next_version = current.version + 1

        update_data: Dict[str, Any] = dict(changes or {})
        update_data.update(
            {
                "status": target_status,
                "version": next_version,
                "updated_at": (
                    updated_at
                    if updated_at is not None
                    else m._utc_now()
                ),
            }
        )

        if target_status != m.BacklogStatus.BLOCKED:
            update_data.setdefault("blocked_reason", None)

        if target_status != m.BacklogStatus.FAILED:
            update_data.setdefault("failure_reason", None)

        if target_status != m.BacklogStatus.SUPERSEDED:
            update_data.setdefault("superseded_by", None)

        next_item = m.BacklogItem(
            **{
                **current.model_dump(),
                **update_data,
            }
        )

        event_type = _EVENT_FOR_STATUS.get(target_status)

        if event_type is None:
            raise ValueError(
                f"no lifecycle event exists for status "
                f"{target_status.value}"
            )

        event = m.BacklogEvent(
            event_id=m.new_backlog_event_id(),
            event_type=event_type,
            project_id=project_id,
            item_id=item_id,
            timestamp=next_item.updated_at,
            sequence=1,
            actor=actor,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            expected_version=current.version,
            resulting_version=next_version,
            payload={
                "item": next_item.model_dump(mode="json"),
                "previous_status": current.status.value,
                "target_status": target_status.value,
            },
        )

        if idempotency_key is None:
            self._store.append_event(event)
            return next_item

        appended = self._store.append_event_once(event)

        if appended is None:
            existing = self._find_by_idempotency_key(
                project_id,
                idempotency_key,
            )

            if existing is None:
                raise ValueError(
                    "idempotent transition was rejected but no "
                    "existing backlog item could be resolved"
                )

            return existing

        return next_item

    def _find_by_idempotency_key(
        self,
        project_id: str,
        idempotency_key: str,
    ) -> Optional[m.BacklogItem]:
        for event in self._store.iter_events(
            project_id=project_id
        ):
            if event.idempotency_key != idempotency_key:
                continue

            return self._store.get_item(
                project_id,
                event.item_id,
            )

        return None
