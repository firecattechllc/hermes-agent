"""Governed, disabled-by-default Goose CLI worker adapter.

Scope boundary: Goose (https://github.com/block/goose) is an optional local
execution backend for governed Sigil advisory tasks (``Capability.CODING``
only). It is a worker, never the control plane — Hermes/Sigil remain
authoritative. Every invocation is bounded by ``GOOSE_GOVERNANCE_BOUNDARIES``,
baked into every result via the same authority-denial vocabulary as
``ai/mac_ollama.py``: no broker submission, no capital/portfolio/policy
authority, no credential access, no arbitrary shell or filesystem access, and
no fleet-administrative authority.

Goose's own extension system (Computer Controller, Memory, Top Of Mind,
remote-host control, unrestricted MCP servers, etc.) is never auto-enabled:
every invocation runs with ``--no-profile`` and no ``--with-extension`` /
``--with-builtin`` flags, so the spawned process has no filesystem, shell,
network-tool, memory, or computer-control capability beyond exchanging text
with the configured local model provider. This is enforced structurally by
the fixed argument list in :func:`_build_goose_args`, not by policy alone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .evidence import build_invocation_evidence
from .ledger import AIEvidenceRecordType, DurableAIEvidenceLedger, GovernedAIEvidenceRecord
from .models import (
    Capability,
    ExecutionLocation,
    ProviderHealth,
    ProviderIdentity,
    validate_identifier,
)
from .provider import ProviderFailure, ProviderFailureClass, ProviderInvocation, ProviderResult

GOOSE_PROVIDER_ID = "goose-cli"
DEFAULT_GOOSE_PROVIDER = "ollama"
DEFAULT_GOOSE_MODEL = "gemma4:12b"

GOOSE_GOVERNANCE_BOUNDARIES: dict[str, bool] = {
    "paper_only": True,
    "broker_submission": False,
    "execution_authorized": False,
    "approval_authority": False,
    "portfolio_mutation": False,
    "capital_authority": False,
    "policy_mutation": False,
    "credential_access": False,
    "arbitrary_shell": False,
    "arbitrary_filesystem": False,
    "governance_bypass": False,
    "fleet_administrative_authority": False,
}


class GooseConfigurationError(ValueError):
    """A Goose worker configuration violates a governed invariant."""


class GooseWorkspaceDenied(ValueError):
    """A requested Goose workspace is outside the governed allowlist."""


@dataclass(frozen=True, slots=True)
class GooseWorkerConfig:
    enabled: bool = False
    executable: str = "goose"
    provider: str = DEFAULT_GOOSE_PROVIDER
    model: str = DEFAULT_GOOSE_MODEL
    allowed_workspaces: tuple[str, ...] = ()
    timeout_ms: int = 120_000
    max_turns: int = 10
    max_tool_repetitions: int = 3
    max_output_bytes: int = 200_000
    max_concurrent_jobs: int = 1
    version_probe_timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise GooseConfigurationError("Goose executable must not be blank")
        validate_identifier(self.provider, "Goose provider")
        validate_identifier(self.model, "Goose model")
        if not 1_000 <= self.timeout_ms <= 600_000:
            raise GooseConfigurationError("Goose timeout is outside its governed bound")
        if not 1 <= self.max_turns <= 100:
            raise GooseConfigurationError("Goose max turns is outside its governed bound")
        if not 1 <= self.max_tool_repetitions <= 50:
            raise GooseConfigurationError(
                "Goose max tool repetitions is outside its governed bound"
            )
        if not 1_000 <= self.max_output_bytes <= 10_000_000:
            raise GooseConfigurationError("Goose output bound is outside its governed bound")
        if not 1 <= self.max_concurrent_jobs <= 8:
            raise GooseConfigurationError("Goose concurrency bound is outside its governed bound")
        if not 1_000 <= self.version_probe_timeout_ms <= 60_000:
            raise GooseConfigurationError("Goose version probe timeout is invalid")
        for workspace in self.allowed_workspaces:
            if not workspace or not os.path.isabs(workspace):
                raise GooseConfigurationError("Goose allowed workspaces must be absolute paths")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "GooseWorkerConfig":
        source = os.environ if environment is None else environment
        truth = {"1", "true", "yes"}

        def integer(name: str, default: int) -> int:
            try:
                return int(source.get(name, str(default)))
            except ValueError as error:
                raise GooseConfigurationError(f"{name} must be an integer") from error

        workspaces_raw = source.get("SIGIL_AI_GOOSE_ALLOWED_WORKSPACES", "")
        workspaces = tuple(
            entry.strip() for entry in workspaces_raw.split(os.pathsep) if entry.strip()
        )

        return cls(
            enabled=source.get("SIGIL_AI_GOOSE_ENABLED", "").lower() in truth,
            executable=source.get("SIGIL_AI_GOOSE_EXECUTABLE", "goose"),
            provider=source.get("SIGIL_AI_GOOSE_PROVIDER", DEFAULT_GOOSE_PROVIDER),
            model=source.get("SIGIL_AI_GOOSE_MODEL", DEFAULT_GOOSE_MODEL),
            allowed_workspaces=workspaces,
            timeout_ms=integer("SIGIL_AI_GOOSE_TIMEOUT_MS", 120_000),
            max_turns=integer("SIGIL_AI_GOOSE_MAX_TURNS", 10),
            max_tool_repetitions=integer("SIGIL_AI_GOOSE_MAX_TOOL_REPETITIONS", 3),
            max_output_bytes=integer("SIGIL_AI_GOOSE_MAX_OUTPUT_BYTES", 200_000),
            max_concurrent_jobs=integer("SIGIL_AI_GOOSE_MAX_CONCURRENT_JOBS", 1),
            version_probe_timeout_ms=integer("SIGIL_AI_GOOSE_VERSION_PROBE_TIMEOUT_MS", 10_000),
        )


_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class GooseVersion:
    raw: str
    major: int
    minor: int
    patch: int


def parse_goose_version(text: str) -> GooseVersion | None:
    match = _VERSION_PATTERN.search(text or "")
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return GooseVersion(raw=match.group(0), major=major, minor=minor, patch=patch)


def resolve_goose_executable(executable: str) -> str | None:
    """Resolve an explicit path or a bare command name via PATH. Never uses a shell."""

    if os.sep in executable or (os.altsep and os.altsep in executable):
        if os.path.isfile(executable) and os.access(executable, os.X_OK):
            return executable
        return None
    return shutil.which(executable)


def _minimal_environment(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build an allowlisted subprocess environment. Never inherits the full process env."""

    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "USER"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if extra:
        env.update(extra)
    return env


def _validate_workspace(workspace: str, allowed: tuple[str, ...]) -> str:
    if not workspace or not os.path.isabs(workspace):
        raise GooseWorkspaceDenied("workspace must be an absolute path")
    real = os.path.realpath(workspace)
    for candidate in allowed:
        allowed_real = os.path.realpath(candidate)
        if real == allowed_real or real.startswith(allowed_real + os.sep):
            return real
    raise GooseWorkspaceDenied("workspace is not in the governed allowlist")


_SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|private[_-]?key|client[_-]?secret|"
    r"password)\s*[:=]\s*\S+|"
    r"bearer\s+[a-zA-Z0-9._-]{10,}|"
    r"(?:sk|ghp|gho|ghu|ghs|xox[baprs])[-_][a-zA-Z0-9]{8,}"
)
_REDACTED = "[redacted]"


def redact_secrets(text: str) -> str:
    """Best-effort redaction of common secret patterns from Goose output/stderr."""

    return _SECRET_PATTERN.sub(_REDACTED, text)


@dataclass(frozen=True, slots=True)
class GooseProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    cancelled: bool = False
    executable_missing: bool = False


class GooseSubprocessRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None,
        env: Mapping[str, str],
        timeout_seconds: float,
        cancel_event: "threading.Event | None" = None,
    ) -> GooseProcessResult: ...


def _terminate(process: "subprocess.Popen[bytes]") -> None:
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


class SubprocessGooseRunner:
    """Argument-array-only subprocess execution. Never uses ``shell=True``."""

    _poll_interval_seconds = 0.05

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None,
        env: Mapping[str, str],
        timeout_seconds: float,
        cancel_event: threading.Event | None = None,
    ) -> GooseProcessResult:
        try:
            process = subprocess.Popen(  # noqa: S603 - argument array only, never shell=True
                list(args),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            return GooseProcessResult(
                returncode=None, stdout=b"", stderr=b"", executable_missing=True
            )

        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                _terminate(process)
                stdout, stderr = process.communicate()
                return GooseProcessResult(
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    cancelled=True,
                )
            if time.monotonic() >= deadline:
                _terminate(process)
                stdout, stderr = process.communicate()
                return GooseProcessResult(
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=True,
                )
            time.sleep(self._poll_interval_seconds)

        stdout, stderr = process.communicate()
        return GooseProcessResult(returncode=process.returncode, stdout=stdout, stderr=stderr)


class GooseInspector:
    def __init__(
        self, config: GooseWorkerConfig, runner: GooseSubprocessRunner | None = None
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessGooseRunner()

    def resolve_executable(self) -> str | None:
        return resolve_goose_executable(self.config.executable)

    def detect_version(self) -> GooseVersion | None:
        executable = self.resolve_executable()
        if executable is None:
            return None
        result = self.runner.run(
            [executable, "--version"],
            cwd=None,
            env=_minimal_environment(),
            timeout_seconds=self.config.version_probe_timeout_ms / 1_000,
        )
        if result.executable_missing or result.timed_out or result.cancelled:
            return None
        if result.returncode != 0:
            return None
        return parse_goose_version(result.stdout.decode("utf-8", errors="replace"))

    def status(self) -> dict[str, object]:
        executable_path = self.resolve_executable()
        installed = executable_path is not None
        version = self.detect_version() if installed else None

        if not self.config.enabled:
            health, reason = "disabled", None
        elif not installed:
            health, reason = "unavailable", "executable_not_found"
        elif version is None:
            health, reason = "unavailable", "version_probe_failed"
        else:
            health, reason = "healthy", None

        return {
            "enabled": self.config.enabled,
            "installed": installed,
            "executable": executable_path,
            "version": None if version is None else version.raw,
            "provider": self.config.provider,
            "model": self.config.model,
            "health": health,
            "readiness": "ready" if health == "healthy" else "not_ready",
            "reason": reason,
            **GOOSE_GOVERNANCE_BOUNDARIES,
        }


class GooseConcurrencyLimiter:
    """Bounded, non-blocking concurrency gate. Fails closed, never queues."""

    def __init__(self, max_concurrent_jobs: int) -> None:
        self._max = max_concurrent_jobs
        self._lock = threading.Lock()
        self._active = 0

    @property
    def active_jobs(self) -> int:
        with self._lock:
            return self._active

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self._max:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)


@dataclass(frozen=True, slots=True)
class GooseHealthProbe:
    health: ProviderHealth
    classification: str | None
    provider_id: str


def _build_goose_args(
    executable: str,
    config: GooseWorkerConfig,
    *,
    instructions: str,
    system: str | None,
) -> list[str]:
    """Build the governed, non-interactive Goose invocation.

    ``--no-profile`` with no ``--with-extension``/``--with-builtin`` flags is
    load-bearing: it is what guarantees Goose has zero tool/extension access
    (no filesystem, shell, network, memory, or computer-control capability)
    regardless of what the local user's default Goose profile contains.
    """

    args = [
        executable,
        "run",
        "--no-profile",
        "--no-session",
        "-q",
        "--output-format",
        "json",
        "--provider",
        config.provider,
        "--model",
        config.model,
        "--max-turns",
        str(config.max_turns),
        "--max-tool-repetitions",
        str(config.max_tool_repetitions),
    ]
    if system:
        args += ["--system", system]
    args += ["-t", instructions]
    return args


def _extract_assistant_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        parts = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if parts:
            return "\n".join(parts)
    return None


def _safe_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _interpret_process_result(
    result: GooseProcessResult, config: GooseWorkerConfig
) -> tuple[ProviderFailure | None, dict[str, object] | None]:
    if result.executable_missing:
        return (
            ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, "Goose executable was not found.", True
            ),
            None,
        )
    if result.cancelled:
        return (
            ProviderFailure(
                ProviderFailureClass.CANCELLED, "Goose invocation was cancelled.", False
            ),
            None,
        )
    if result.timed_out:
        return (
            ProviderFailure(ProviderFailureClass.TIMEOUT, "Goose invocation timed out.", True),
            None,
        )
    if result.returncode != 0:
        detail = redact_secrets(result.stderr.decode("utf-8", errors="replace")).strip()[:500]
        message = f"Goose exited with status {result.returncode}."
        if detail:
            message = f"{message} {detail}"
        return ProviderFailure(ProviderFailureClass.UNAVAILABLE, message, True), None

    try:
        payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return (
            ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT, "Goose output was not valid JSON.", False
            ),
            None,
        )

    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    status = metadata.get("status") if isinstance(metadata, dict) else None

    text_value = _extract_assistant_text(payload)
    if text_value is None:
        return (
            ProviderFailure(
                ProviderFailureClass.MALFORMED_OUTPUT,
                "Goose output did not contain an assistant response.",
                False,
            ),
            None,
        )

    if status is not None and status != "completed":
        return (
            ProviderFailure(
                ProviderFailureClass.UNAVAILABLE, f"Goose run did not complete: {status}", True
            ),
            None,
        )

    redacted = redact_secrets(text_value)
    encoded = redacted.encode("utf-8")
    truncated = len(encoded) > config.max_output_bytes
    if truncated:
        redacted = encoded[: config.max_output_bytes].decode("utf-8", errors="ignore")

    usage = metadata if isinstance(metadata, dict) else {}
    output: dict[str, object] = {
        "schema_version": 1,
        "text": redacted,
        "truncated": truncated,
        "input_tokens": _safe_int(usage.get("input_tokens")),
        "output_tokens": _safe_int(usage.get("output_tokens")),
        "total_tokens": _safe_int(usage.get("total_tokens")),
        "provider": config.provider,
        "model": config.model,
        **GOOSE_GOVERNANCE_BOUNDARIES,
    }
    return None, output


class GooseWorkerProvider:
    """Governed Goose CLI worker. Implements :class:`ai.provider.ModelProvider`."""

    capabilities = frozenset({Capability.CODING})
    model_family = "goose"
    input_contract = "application/json;schema=sigil.ai.input.goose.v1"
    output_contract = "application/json;schema=sigil.ai.output.goose.v1"

    def __init__(
        self,
        config: GooseWorkerConfig,
        *,
        runner: GooseSubprocessRunner | None = None,
        ledger: DurableAIEvidenceLedger | None = None,
        concurrency: GooseConcurrencyLimiter | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessGooseRunner()
        self.ledger = ledger
        self.concurrency = concurrency or GooseConcurrencyLimiter(config.max_concurrent_jobs)
        self.model_id = config.model
        self.model_version = "goose-cli-v1"
        self.request_timeout_ms = config.timeout_ms
        self._state_lock = threading.Lock()
        self._last_execution: dict[str, object] | None = None
        self.identity = ProviderIdentity(
            GOOSE_PROVIDER_ID,
            ExecutionLocation.LOCAL,
            health=ProviderHealth.DEGRADED if config.enabled else ProviderHealth.UNAVAILABLE,
            enabled=config.enabled,
            metadata=(("provider", config.provider),),
        )

    @property
    def active_jobs(self) -> int:
        return self.concurrency.active_jobs

    @property
    def last_execution(self) -> dict[str, object] | None:
        with self._state_lock:
            return None if self._last_execution is None else dict(self._last_execution)

    def health_probe(self) -> GooseHealthProbe:
        status = GooseInspector(self.config, self.runner).status()
        health = {
            "healthy": ProviderHealth.HEALTHY,
            "disabled": ProviderHealth.UNAVAILABLE,
            "unavailable": ProviderHealth.UNAVAILABLE,
        }.get(str(status["health"]), ProviderHealth.UNAVAILABLE)
        classification = status["reason"] or status["health"]
        return GooseHealthProbe(
            health=health,
            classification=str(classification) if classification is not None else None,
            provider_id=self.identity.provider_id,
        )

    def invoke(
        self,
        invocation: ProviderInvocation,
        *,
        cancel_event: threading.Event | None = None,
    ) -> ProviderResult:
        version: GooseVersion | None = None

        def finish(
            failure: ProviderFailure | None, output: dict[str, object] | None
        ) -> ProviderResult:
            evidence = build_invocation_evidence(
                request_id=invocation.request_id,
                task_correlation_id=invocation.task_correlation_id,
                provider_id=self.identity.provider_id,
                model_id=invocation.model_id,
                registry_revision=invocation.registry_revision,
                capability=invocation.capability,
                execution_location=ExecutionLocation.LOCAL,
                started_at=invocation.started_at,
                ended_at=invocation.ended_at,
                succeeded=failure is None,
                failure_classification=None if failure is None else failure.classification.value,
                input_payload={
                    "has_workspace": invocation.input_payload.get("workspace") is not None
                },
                output_payload=None
                if output is None
                else {key: value for key, value in output.items() if key != "text"},
                provider_metadata=tuple(
                    sorted(
                        (
                            ("executable_version", version.raw if version else "unknown"),
                            ("goose_provider", self.config.provider),
                        )
                    )
                ),
            )
            result = ProviderResult(output=output, failure=failure, evidence=evidence)

            with self._state_lock:
                self._last_execution = {
                    "succeeded": result.succeeded,
                    "failure_classification": (
                        None if failure is None else failure.classification.value
                    ),
                    "ended_at": invocation.ended_at,
                    "truncated": False if output is None else bool(output.get("truncated", False)),
                }

            if self.ledger is not None:
                self.ledger.append(
                    GovernedAIEvidenceRecord(
                        evidence_identity=evidence.evidence_identity,
                        record_type=(
                            AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED
                            if result.succeeded
                            else AIEvidenceRecordType.PROVIDER_RESULT_FAILED
                        ),
                        request_id=invocation.request_id,
                        task_correlation_id=invocation.task_correlation_id,
                        provider_id=self.identity.provider_id,
                        model_id=self.model_id,
                        model_version=self.model_version,
                        registry_revision=invocation.registry_revision,
                        capability=invocation.capability,
                        execution_location=ExecutionLocation.LOCAL,
                        routing_status="selected",
                        fallback=False,
                        started_at=invocation.started_at,
                        ended_at=invocation.ended_at,
                        succeeded=result.succeeded,
                        failure_classification=evidence.failure_classification,
                        input_digest=evidence.input_digest,
                        output_digest=evidence.output_digest,
                        provider_metadata=evidence.provider_metadata,
                    )
                )
            return result

        if not self.config.enabled:
            return finish(
                ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE, "Goose worker is disabled.", False
                ),
                None,
            )
        if invocation.model_id != self.model_id:
            return finish(
                ProviderFailure(
                    ProviderFailureClass.MODEL_IDENTITY_MISMATCH,
                    "Goose model identity mismatch.",
                    False,
                ),
                None,
            )
        if invocation.capability not in self.capabilities:
            return finish(
                ProviderFailure(
                    ProviderFailureClass.CAPABILITY_MISMATCH, "Goose capability mismatch.", False
                ),
                None,
            )

        instructions = invocation.input_payload.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            return finish(
                ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT, "Goose instructions are required.", False
                ),
                None,
            )

        workspace = invocation.input_payload.get("workspace")
        if workspace is not None and not isinstance(workspace, str):
            return finish(
                ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Goose workspace must be a string path.",
                    False,
                ),
                None,
            )

        resolved_workspace: str | None = None
        if workspace is not None:
            try:
                resolved_workspace = _validate_workspace(workspace, self.config.allowed_workspaces)
            except GooseWorkspaceDenied as error:
                return finish(
                    ProviderFailure(
                        ProviderFailureClass.MALFORMED_OUTPUT,
                        f"Goose workspace denied: {error}",
                        False,
                    ),
                    None,
                )

        system = invocation.input_payload.get("system")
        if system is not None and not isinstance(system, str):
            return finish(
                ProviderFailure(
                    ProviderFailureClass.MALFORMED_OUTPUT,
                    "Goose system instructions must be a string.",
                    False,
                ),
                None,
            )

        executable_path = resolve_goose_executable(self.config.executable)
        if executable_path is None:
            return finish(
                ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE, "Goose executable was not found.", True
                ),
                None,
            )

        if not self.concurrency.try_acquire():
            return finish(
                ProviderFailure(
                    ProviderFailureClass.UNAVAILABLE, "Goose concurrency limit exceeded.", True
                ),
                None,
            )

        try:
            version = GooseInspector(self.config, self.runner).detect_version()
            args = _build_goose_args(
                executable_path, self.config, instructions=instructions, system=system
            )
            timeout_seconds = min(invocation.timeout_ms, self.config.timeout_ms) / 1_000
            process_result = self.runner.run(
                args,
                cwd=resolved_workspace,
                env=_minimal_environment(),
                timeout_seconds=timeout_seconds,
                cancel_event=cancel_event,
            )
            failure, output = _interpret_process_result(process_result, self.config)
        finally:
            self.concurrency.release()

        return finish(failure, output)
