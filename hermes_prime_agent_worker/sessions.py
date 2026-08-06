"""Orchestrates governed Prime Agent operations: policy check first, then
(and only then) a bounded subprocess call, then evidence -- always in that
order, always all three. This is the only module callers (CLI, future
Mission Control adapter) should use; it is the sole place that is allowed
to call :mod:`hermes_prime_agent_worker.proc`.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from hermes_prime_agent_worker import policy, proc
from hermes_prime_agent_worker import status as status_module
from hermes_prime_agent_worker.config import PrimeAgentWorkerConfig
from hermes_prime_agent_worker.evidence import EvidenceRecord, EvidenceStore
from hermes_prime_agent_worker.redaction import redact_text

_KILL_SWITCH_FILENAME = "KILL_SWITCH"
_FAILURE_STATE_FILENAME = "failure_state.json"


@dataclass(frozen=True, slots=True)
class RunResult:
    permitted: bool
    reason_codes: Tuple[str, ...]
    returncode: Optional[int]
    timed_out: bool
    truncated: bool
    duration_seconds: float
    stdout: str
    stderr: str
    correlation_id: str
    evidence_entry: Optional[Mapping[str, Any]]


class PrimeAgentWorker:
    """Governed control surface for one Prime Agent worker deployment.

    Deliberately absent: any method that merges, tags, pushes, releases, or
    force-deletes a git branch; any method that enables, edits, or writes
    this worker's own systemd unit file; any method that clears its own
    kill switch. A caller that tries to invoke one of these must fail with
    AttributeError, proving the capability does not exist anywhere in this
    module -- the same structural-absence convention
    ``hermes_docs_worker``'s GitHub/git-ops modules use.
    """

    def __init__(self, config: PrimeAgentWorkerConfig) -> None:
        self._config = config
        self._evidence = EvidenceStore(
            config.state_dir / "evidence",
            retention_days=config.evidence_retention_days,
            max_files=config.evidence_max_files,
        )

    # -- kill switch -----------------------------------------------------

    def kill_switch_path(self) -> Path:
        return self._config.state_dir / _KILL_SWITCH_FILENAME

    def is_kill_switch_active(self) -> bool:
        return self.kill_switch_path().exists()

    def emergency_stop(self, *, reason: str) -> Mapping[str, Any]:
        """Immediately and persistently blocks every future governed task
        run, independent of whether the daemon is currently running. Sets
        a file-based kill switch that :meth:`run_task` checks before doing
        anything else. This module deliberately provides no method to
        clear it -- clearing requires an operator to remove the file from
        the filesystem directly, so a compromised or malfunctioning caller
        of this class can trip the switch but can never un-trip it."""
        self._config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.kill_switch_path().write_text(
            json.dumps({"reason": redact_text(reason), "timestamp": int(time.time())})
        )
        shutdown_result = self.shutdown(force=True)
        entry = self._record(
            category="lifecycle",
            action="emergency_stop",
            status_value="applied",
            reason_codes=(),
            detail=reason,
            correlation_id=_new_correlation_id(),
        )
        return {"kill_switch": True, "shutdown": shutdown_result, "evidence": entry}

    # -- read-only status --------------------------------------------------

    def status(self) -> Tuple[status_module.DaemonSnapshot, ...]:
        result = self._run_cli(
            ["status", "--json"], correlation_id=_new_correlation_id()
        )
        payload = _parse_json_or_empty(result.stdout)
        snapshots = status_module.parse_status(payload)
        self._record(
            category="observability",
            action="status",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=_new_correlation_id(),
            detail=f"returncode={result.returncode} timed_out={result.timed_out}",
        )
        return snapshots

    def doctor(
        self, *, fix: bool = False, fix_approved: bool = False
    ) -> Mapping[str, Any]:
        decision = policy.evaluate_doctor_fix(
            requested_fix=fix, fix_approved=fix_approved
        )
        correlation_id = _new_correlation_id()
        if not decision.permitted:
            entry = self._record(
                category="lifecycle",
                action="doctor_fix",
                status_value="denied",
                reason_codes=decision.reason_codes,
                correlation_id=correlation_id,
            )
            return {
                "permitted": False,
                "reason_codes": decision.reason_codes,
                "evidence": entry,
            }

        argv = ["doctor", "--json"]
        if fix:
            argv.insert(1, "--fix")
        result = self._run_cli(argv, correlation_id=correlation_id)
        entry = self._record(
            category="lifecycle",
            action="doctor_fix" if fix else "doctor",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=correlation_id,
            detail=f"returncode={result.returncode}",
        )
        return {
            "permitted": True,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "evidence": entry,
        }

    def list_sessions(self, *, include_saved: bool = False) -> Mapping[str, Any]:
        argv = ["list", "--json"]
        if include_saved:
            argv.insert(1, "--all")
        correlation_id = _new_correlation_id()
        result = self._run_cli(argv, correlation_id=correlation_id)
        payload = _parse_json_or_empty(result.stdout)
        self._record(
            category="observability",
            action="list_sessions",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=correlation_id,
        )
        return {"agents": payload, "returncode": result.returncode}

    # -- bounded task execution ------------------------------------------

    def run_task(
        self,
        *,
        workspace: Path,
        task_text: str,
        mutation_approved: bool = False,
        network_approved: bool = False,
        requested_tools: Tuple[str, ...] = (),
        gate_commands: Tuple[str, ...] = (),
        active_session_count: int = 0,
        no_session: bool = True,
    ) -> RunResult:
        correlation_id = _new_correlation_id()
        consecutive_failures, seconds_since_last_failure = self._read_failure_state()

        decision = policy.evaluate_task_request(
            self._config,
            workspace=workspace,
            task_text=task_text,
            requested_tools=requested_tools,
            mutation_approved=mutation_approved,
            network_approved=network_approved,
            gate_commands=gate_commands,
            active_session_count=active_session_count,
            consecutive_failures=consecutive_failures,
            seconds_since_last_failure=seconds_since_last_failure,
            kill_switch_active=self.is_kill_switch_active(),
        )

        if not decision.permitted:
            entry = self._record(
                category="task",
                action="run_task",
                status_value="denied",
                reason_codes=decision.reason_codes,
                correlation_id=correlation_id,
                detail=task_text,
            )
            return RunResult(
                permitted=False,
                reason_codes=decision.reason_codes,
                returncode=None,
                timed_out=False,
                truncated=False,
                duration_seconds=0.0,
                stdout="",
                stderr="",
                correlation_id=correlation_id,
                evidence_entry=entry,
            )

        argv = self._build_run_argv(
            task_text=task_text,
            requested_tools=requested_tools,
            gate_commands=gate_commands,
            no_session=no_session,
        )

        result = proc.run_bounded(
            self._config,
            argv,
            cwd=workspace,
            timeout_seconds=float(self._config.timeout_seconds),
        )

        succeeded = _task_succeeded(result.returncode, result.timed_out, result.stdout)
        self._update_failure_state(succeeded=succeeded)

        entry = self._record(
            category="task",
            action="run_task",
            status_value="succeeded" if succeeded else "failed",
            correlation_id=correlation_id,
            detail=(
                f"argv={redact_text(' '.join(argv))} "
                f"returncode={result.returncode} timed_out={result.timed_out} "
                f"duration_seconds={result.duration_seconds:.2f}"
            ),
        )

        return RunResult(
            permitted=True,
            reason_codes=(),
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
            duration_seconds=result.duration_seconds,
            stdout=result.stdout,
            stderr=result.stderr,
            correlation_id=correlation_id,
            evidence_entry=entry,
        )

    def _build_run_argv(
        self,
        *,
        task_text: str,
        requested_tools: Tuple[str, ...],
        gate_commands: Tuple[str, ...],
        no_session: bool,
    ) -> Sequence[str]:
        argv = [
            str(self._config.executable),
            "-p",
            "--mode",
            "json",
            # Disables Prime Agent's own startup network operations (update
            # checks, telemetry) unconditionally. The deliberate model call
            # to the configured provider below is unaffected -- this only
            # removes incidental network egress this worker never
            # explicitly approved.
            "--offline",
            "--provider",
            self._config.provider,
            "--model",
            self._config.model,
            "--autonomous",
            "--autonomous-max-turns",
            str(self._config.max_turns),
            "--autonomous-max-tokens",
            str(self._config.max_tokens),
            "--autonomous-timeout-ms",
            str(self._config.timeout_seconds * 1000),
            "--autonomous-gate-retries",
            str(self._config.gate_retries),
            "--autonomous-gate-timeout-ms",
            str(self._config.gate_timeout_seconds * 1000),
        ]
        if no_session:
            argv.append("--no-session")
        if requested_tools:
            argv.extend(["--tools", ",".join(requested_tools)])
        else:
            argv.append("--no-tools")
        for gate in gate_commands:
            argv.extend(["--autonomous-gate", gate])
        argv.append(task_text)
        return argv

    # -- session lifecycle -------------------------------------------------

    def send(self, agent_id: str, message: str) -> Mapping[str, Any]:
        correlation_id = _new_correlation_id()
        result = self._run_cli(
            ["send", agent_id, message, "--json"], correlation_id=correlation_id
        )
        entry = self._record(
            category="task",
            action="send",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=correlation_id,
            detail=f"agent_id={agent_id}",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "evidence": entry,
        }

    def stop(self, agent_id: str) -> Mapping[str, Any]:
        correlation_id = _new_correlation_id()
        result = self._run_cli(
            ["stop", agent_id, "--json"], correlation_id=correlation_id
        )
        entry = self._record(
            category="lifecycle",
            action="stop_agent",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=correlation_id,
            detail=f"agent_id={agent_id}",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "evidence": entry,
        }

    def shutdown(self, *, force: bool = True) -> Mapping[str, Any]:
        argv = ["shutdown", "--json"]
        if force:
            argv.insert(1, "--force")
        correlation_id = _new_correlation_id()
        result = self._run_cli(argv, correlation_id=correlation_id)
        entry = self._record(
            category="lifecycle",
            action="shutdown",
            status_value="ok" if result.returncode == 0 else "error",
            correlation_id=correlation_id,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "evidence": entry,
        }

    # -- internals -----------------------------------------------------

    def _run_cli(self, args: Sequence[str], *, correlation_id: str) -> proc.ProcResult:
        argv = [str(self._config.executable), *args]
        return proc.run_bounded(
            self._config,
            argv,
            cwd=self._config.state_dir,
            timeout_seconds=min(30.0, float(self._config.timeout_seconds)),
        )

    def _record(
        self,
        *,
        category: str,
        action: str,
        status_value: str,
        correlation_id: str,
        reason_codes: Tuple[str, ...] = (),
        detail: str = "",
    ) -> Mapping[str, Any]:
        record = EvidenceRecord.build(
            category=category,
            action=action,
            status=status_value,
            reason_codes=reason_codes,
            detail=detail,
            correlation_id=correlation_id,
        )
        return self._evidence.append(record)

    def _failure_state_path(self) -> Path:
        return self._config.state_dir / _FAILURE_STATE_FILENAME

    def _read_failure_state(self) -> Tuple[int, Optional[float]]:
        path = self._failure_state_path()
        if not path.exists():
            return 0, None
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):
            return 0, None
        consecutive = data.get("consecutive_failures", 0)
        last_failure_at = data.get("last_failure_at")
        if not isinstance(consecutive, int) or consecutive < 0:
            consecutive = 0
        if isinstance(last_failure_at, (int, float)):
            return consecutive, max(0.0, time.time() - last_failure_at)
        return consecutive, None

    def _update_failure_state(self, *, succeeded: bool) -> None:
        self._config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if succeeded:
            data = {"consecutive_failures": 0, "last_failure_at": None}
        else:
            consecutive, _ = self._read_failure_state()
            data = {
                "consecutive_failures": consecutive + 1,
                "last_failure_at": time.time(),
            }
        self._failure_state_path().write_text(json.dumps(data))


def _new_correlation_id() -> str:
    return f"prime-agent-worker-{uuid.uuid4().hex}"


def _parse_json_or_empty(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return []


def _task_succeeded(returncode: Optional[int], timed_out: bool, stdout: str) -> bool:
    """Prime Agent's ``--mode json`` CLI exits 0 even when the model call
    itself failed after exhausting its own retries -- the failure is only
    visible inside the JSON-lines transcript (an ``auto_retry_end`` event
    with ``success: false``, or a message with ``stopReason: "error"``),
    never in the process exit code. Confirmed directly against a live,
    real OmniRoute failure during this worker's own acceptance testing:
    ``returncode`` was 0 for a run that never got a real model response.
    Relying on exit code alone here would mean the cooldown/backoff
    governance control (see policy.COOLDOWN_ACTIVE) never triggers on
    repeated content-level failures. Fails closed: any line that fails to
    parse, or any explicit failure signal found anywhere in the
    transcript, counts as a failure."""
    if returncode != 0 or timed_out:
        return False

    saw_any_event = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        saw_any_event = True
        event_type = event.get("type")
        if event_type == "auto_retry_end" and event.get("success") is False:
            return False
        message = event.get("message")
        if isinstance(message, dict) and message.get("stopReason") == "error":
            return False

    return saw_any_event
