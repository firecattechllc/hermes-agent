"""Best-effort runtime telemetry bridge for Mission Control.

The Shared Engineering Context remains the canonical launch source. This
module only resolves the active Hermes project, creates/updates a context
launch for the current turn, and mirrors existing hook events into Mission
Control. All failures are logged and swallowed so operational telemetry cannot
break the primary agent task.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

from hermes_cli.mission_control import models as m

logger = logging.getLogger("hermes.mission_control.runtime")


@dataclass
class RuntimeTelemetryContext:
    project_id: str
    launch_id: str
    task_id: str
    agent_id: str
    session_id: str
    evidence_refs: list[str] = field(default_factory=list)
    final_status: Optional[str] = None
    failure_reason: Optional[str] = None
    agent_finished: bool = False


_CURRENT_CONTEXT: contextvars.ContextVar[Optional[RuntimeTelemetryContext]] = (
    contextvars.ContextVar("mission_control_runtime_context", default=None)
)


def current_context() -> Optional[RuntimeTelemetryContext]:
    return _CURRENT_CONTEXT.get()


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part) for part in parts if part not in (None, ""))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _source_key(*parts: Any) -> str:
    return ":".join(str(part) for part in parts if part not in (None, ""))


def _safe_json_payload(value: Any) -> Any:
    try:
        json.dumps(value, sort_keys=True, default=str)
        return value
    except Exception:
        return repr(value)


def _append_once(event: m.TelemetryEvent) -> None:
    try:
        from hermes_cli.mission_control.service import MissionControlService

        MissionControlService().append_event_once(event)
    except Exception as exc:
        logger.warning("Mission Control telemetry append failed: %s", exc)


def _resolve_runtime_project() -> Optional[Any]:
    try:
        from hermes_cli import projects_db as pdb

        with pdb.connect_closing() as conn:
            active = pdb.get_active_id(conn)
            if active:
                project = pdb.get_project(conn, active)
                if project is not None and not project.archived:
                    return project
            return pdb.project_for_path(conn, os.getcwd())
    except Exception as exc:
        logger.debug("Mission Control project resolution failed: %s", exc)
        return None


def _ensure_context_project(project: Any) -> None:
    try:
        from hermes_cli.context_engine.service import ContextService

        service = ContextService()
        if service.get_project(project.id) is not None:
            return
        service.register_project(
            display_name=project.name,
            project_id=project.id,
            repository_identity=project.primary_path,
            local_path=project.primary_path,
            actor="mission_control_runtime",
        )
    except Exception as exc:
        logger.debug("Mission Control context project registration failed: %s", exc)


@contextlib.contextmanager
def telemetry_turn(agent: Any, user_message: Any, task_id: Optional[str]) -> Iterator[None]:
    """Create a canonical context launch around one agent turn when possible."""
    project = _resolve_runtime_project()
    if project is None:
        yield None
        return

    _ensure_context_project(project)
    session_id = getattr(agent, "session_id", "") or "session"
    effective_task_id = task_id or getattr(agent, "_current_task_id", None) or session_id
    started_ns = time.time_ns()
    launch_id = _stable_id("launch", project.id, session_id, effective_task_id, started_ns)
    agent_id = _stable_id("agnt", session_id, getattr(agent, "platform", "") or "cli")
    ctx = RuntimeTelemetryContext(
        project_id=project.id,
        launch_id=launch_id,
        task_id=str(effective_task_id),
        agent_id=agent_id,
        session_id=session_id,
    )

    token = _CURRENT_CONTEXT.set(ctx)
    try:
        try:
            from hermes_cli.context_engine import models as cm
            from hermes_cli.context_engine.service import ContextService

            service = ContextService()
            service.start_launch(
                project.id,
                launch_id=launch_id,
                task_id=str(effective_task_id),
                selected_agents=[agent_id],
                actor="mission_control_runtime",
            )
            service.update_launch(
                project.id,
                launch_id,
                stage=cm.LaunchStage.IMPLEMENTATION,
                status=cm.LaunchStatus.RUNNING,
                actor="mission_control_runtime",
            )
        except Exception as exc:
            logger.warning("Mission Control launch start failed: %s", exc)
        yield ctx
    except BaseException:
        _finish_context_launch(ctx, status="failed", failure_reason="agent turn raised")
        raise
    else:
        _finish_context_launch(
            ctx,
            status=ctx.final_status or "complete",
            failure_reason=ctx.failure_reason,
        )
    finally:
        _CURRENT_CONTEXT.reset(token)


def mark_turn_result(result: Any) -> None:
    """Record the result dict that should drive launch final status."""
    ctx = current_context()
    if ctx is None or not isinstance(result, dict):
        return
    if result.get("interrupted"):
        ctx.final_status = "cancelled"
        ctx.failure_reason = str(result.get("interrupt_message") or "interrupted")
    elif result.get("failed") or result.get("error"):
        ctx.final_status = "failed"
        ctx.failure_reason = str(result.get("error") or result.get("turn_exit_reason") or "failed")
    elif result.get("completed"):
        ctx.final_status = "complete"
    else:
        ctx.final_status = "failed"
        ctx.failure_reason = str(result.get("turn_exit_reason") or "incomplete")


def _finish_context_launch(
    ctx: RuntimeTelemetryContext,
    *,
    status: str,
    failure_reason: Optional[str] = None,
) -> None:
    try:
        from hermes_cli.context_engine import models as cm
        from hermes_cli.context_engine.service import ContextService

        service = ContextService()
        if status == "complete":
            service.update_launch(
                ctx.project_id,
                ctx.launch_id,
                stage=cm.LaunchStage.COMPLETE,
                status=cm.LaunchStatus.COMPLETE,
                evidence_refs=list(dict.fromkeys(ctx.evidence_refs)),
                actor="mission_control_runtime",
            )
        elif status == "cancelled":
            service.update_launch(
                ctx.project_id,
                ctx.launch_id,
                status=cm.LaunchStatus.CANCELLED,
                evidence_refs=list(dict.fromkeys(ctx.evidence_refs)),
                actor="mission_control_runtime",
            )
        else:
            service.update_launch(
                ctx.project_id,
                ctx.launch_id,
                stage=cm.LaunchStage.FAILED,
                status=cm.LaunchStatus.FAILED,
                evidence_refs=list(dict.fromkeys(ctx.evidence_refs)),
                failure_reason=failure_reason,
                actor="mission_control_runtime",
            )
    except Exception as exc:
        logger.warning("Mission Control launch finish failed: %s", exc)
    if not ctx.agent_finished:
        event_type = "agent_complete" if status == "complete" else "agent_error"
        _append_once(_event(ctx, event_type, severity="info" if status == "complete" else "warning", payload={
            "completed": status == "complete",
            "interrupted": status == "cancelled",
            "failure_reason": failure_reason,
            "source_idempotency_key": _source_key("runtime_agent_launch_finished", ctx.project_id, ctx.launch_id, status),
        }))
        ctx.agent_finished = True


def observe_hook(hook_name: str, **kwargs: Any) -> None:
    """Observe existing Hermes lifecycle hooks and mirror selected events."""
    ctx = current_context()
    if ctx is None:
        return
    try:
        if hook_name == "pre_tool_call":
            _record_tool_started(ctx, kwargs)
        elif hook_name == "post_tool_call":
            _record_tool_completed(ctx, kwargs)
        elif hook_name == "pre_approval_request":
            _record_approval_requested(ctx, kwargs)
        elif hook_name == "post_approval_response":
            _record_approval_resolved(ctx, kwargs)
        elif hook_name == "on_session_end":
            _record_agent_finished(ctx, kwargs)
    except Exception as exc:
        logger.warning("Mission Control hook observation failed: %s", exc)


def _event(
    ctx: RuntimeTelemetryContext,
    event_type: str,
    *,
    payload: Dict[str, Any],
    severity: str = "info",
    tool_task_id: Optional[str] = None,
) -> m.TelemetryEvent:
    key = payload.get("source_idempotency_key") or _source_key(
        "runtime", event_type, ctx.project_id, ctx.launch_id, tool_task_id
    )
    payload = dict(payload)
    payload["source"] = "hermes_runtime"
    payload["source_idempotency_key"] = key
    return m.TelemetryEvent(
        event_id=_stable_id("tevt", key),
        event_type=event_type,
        project_id=ctx.project_id,
        launch_id=ctx.launch_id,
        task_id=tool_task_id or ctx.task_id,
        agent_id=ctx.agent_id,
        severity=severity,
        payload=payload,
    )


def _record_tool_started(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> None:
    tool_call_id = kwargs.get("tool_call_id") or ""
    tool_name = kwargs.get("tool_name") or ""
    key = _source_key("runtime_tool_start", ctx.project_id, ctx.launch_id, tool_call_id, tool_name)
    _append_once(_event(
        ctx,
        "agent_tools_started",
        tool_task_id=kwargs.get("task_id") or ctx.task_id,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args": _safe_json_payload(kwargs.get("args") or {}),
            "source_idempotency_key": key,
        },
    ))


def _record_tool_completed(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> None:
    tool_call_id = kwargs.get("tool_call_id") or ""
    tool_name = kwargs.get("tool_name") or ""
    status = str(kwargs.get("status") or "ok")
    key = _source_key("runtime_tool_done", ctx.project_id, ctx.launch_id, tool_call_id, tool_name, status)
    result = kwargs.get("result")
    _append_once(_event(
        ctx,
        "agent_tools_completed",
        tool_task_id=kwargs.get("task_id") or ctx.task_id,
        severity="error" if status in {"error", "blocked", "cancelled"} else "info",
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
            "duration_ms": kwargs.get("duration_ms"),
            "error_type": kwargs.get("error_type"),
            "error_message": kwargs.get("error_message"),
            "result_preview": str(result)[:500] if result is not None else None,
            "source_idempotency_key": key,
        },
    ))
    _record_result_evidence(ctx, tool_name=tool_name, tool_call_id=tool_call_id, result=result)
    if status == "blocked":
        _append_once(_event(ctx, "agent_blocked", payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "reason": kwargs.get("error_message") or kwargs.get("error_type"),
            "source_idempotency_key": _source_key("runtime_agent_blocked", ctx.project_id, ctx.launch_id, tool_call_id),
        }, severity="warning"))
    elif status in {"error", "cancelled"}:
        _append_once(_event(ctx, "agent_error", payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "status": status,
            "error_type": kwargs.get("error_type"),
            "error_message": kwargs.get("error_message"),
            "source_idempotency_key": _source_key("runtime_agent_error", ctx.project_id, ctx.launch_id, tool_call_id, status),
        }, severity="error"))


def _record_result_evidence(
    ctx: RuntimeTelemetryContext,
    *,
    tool_name: str,
    tool_call_id: str,
    result: Any,
) -> None:
    try:
        payload = json.loads(result) if isinstance(result, str) else result
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    evidence = payload.get("verification_evidence")
    if not isinstance(evidence, dict):
        return
    source_path = evidence.get("canonical_command") or evidence.get("scope") or tool_name
    evidence_id = _stable_id("evdn", ctx.project_id, ctx.launch_id, source_path)
    ctx.evidence_refs.append(str(source_path))
    _append_once(_event(ctx, "evidence_collected", payload={
        "evidence_id": evidence_id,
        "source_path": str(source_path),
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "status": evidence.get("status"),
        "kind": evidence.get("kind"),
        "source_idempotency_key": _source_key("runtime_evidence", ctx.project_id, ctx.launch_id, tool_call_id, source_path),
    }))


def _approval_id(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> str:
    return _stable_id(
        "appr",
        ctx.project_id,
        ctx.launch_id,
        kwargs.get("tool_call_id") or "",
        kwargs.get("pattern_key") or "",
        kwargs.get("command") or "",
    )


def _record_approval_requested(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> None:
    aid = _approval_id(ctx, kwargs)
    _append_once(_event(ctx, "approval_requested", payload={
        "approval_id": aid,
        "summary": kwargs.get("description") or kwargs.get("command") or "approval requested",
        "requested_by": "hermes_runtime",
        "command": kwargs.get("command"),
        "pattern_key": kwargs.get("pattern_key"),
        "surface": kwargs.get("surface"),
        "source_idempotency_key": _source_key("runtime_approval_request", ctx.project_id, ctx.launch_id, aid),
    }))
    _append_once(_event(ctx, "agent_waiting_approval", payload={
        "approval_id": aid,
        "source_idempotency_key": _source_key("runtime_agent_waiting_approval", ctx.project_id, ctx.launch_id, aid),
    }))


def _record_approval_resolved(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> None:
    aid = _approval_id(ctx, kwargs)
    choice = str(kwargs.get("choice") or "timeout")
    event_type = "approval_granted" if choice in {"once", "session", "always", "approve", "approved"} else (
        "approval_expired" if choice == "timeout" else "approval_denied"
    )
    _append_once(_event(ctx, event_type, payload={
        "approval_id": aid,
        "resolved_by": "operator" if choice != "timeout" else None,
        "choice": choice,
        "source_idempotency_key": _source_key("runtime_approval_response", ctx.project_id, ctx.launch_id, aid, choice),
    }))


def _record_agent_finished(ctx: RuntimeTelemetryContext, kwargs: Dict[str, Any]) -> None:
    completed = bool(kwargs.get("completed"))
    interrupted = bool(kwargs.get("interrupted"))
    event_type = "agent_complete" if completed and not interrupted else "agent_error"
    _append_once(_event(ctx, event_type, severity="info" if event_type == "agent_complete" else "warning", payload={
        "completed": completed,
        "interrupted": interrupted,
        "model": kwargs.get("model"),
        "platform": kwargs.get("platform"),
        "source_idempotency_key": _source_key("runtime_agent_finished", ctx.project_id, ctx.launch_id, completed, interrupted),
    }))
    ctx.agent_finished = True
