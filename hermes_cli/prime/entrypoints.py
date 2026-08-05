"""Production service entry points for Prime, Titan, and Mac.

Fleet Unification live-runtime work. All configuration is read from
environment variables — documented and validated below, with no hard-coded
paths, hosts, IPs, or secrets. Two run modes:

- ``prime``: runs the control-plane HTTP server
  (:mod:`hermes_cli.prime.server`) and a periodic evidence-integrity
  self-check.
- ``titan`` / ``mac``: runs a periodic registration + heartbeat loop against
  a remote Prime (:mod:`hermes_cli.prime.client`), reporting real Ollama
  model inventory via :mod:`hermes_cli.prime.ollama_node`.

Deployment templates that invoke this module live under ``deploy/prime/``
(systemd units for Prime/Titan on Linux, a launchd plist for Mac, plus
``.env.example`` files documenting every variable below).

Every run mode installs a ``SIGTERM``/``SIGINT`` handler that sets a
``threading.Event`` rather than killing the process outright, so the control
loop can finish its current iteration, stop accepting new work, and shut
down any listening socket cleanly.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.certification_cli import run_certification
from hermes_cli.prime.client import PrimeClientError, PrimeHTTPClient
from hermes_cli.prime.dispatch_gate import CertificationSnapshot
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import DependencyHealth, LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.ollama_node import OllamaNodeConfig, OllamaNodeInspector
from hermes_cli.prime.server import PrimeServerConfigError, build_prime_http_server
from hermes_cli.prime.sigil_route_server import NodeModelAliasConfig

logger = logging.getLogger("hermes.prime.entrypoints")


class PrimeEntrypointConfigError(ValueError):
    """A required environment variable is missing or invalid."""


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise PrimeEntrypointConfigError(f"{name} is required and must not be blank")
    return value


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise PrimeEntrypointConfigError(f"{name} must be an integer") from error


# ── Prime control-plane service ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PrimeServiceConfig:
    state_root: Path
    bind_host: str
    bind_port: int
    auth_token: str
    project_id: str
    integrity_check_interval_seconds: int
    node_model_aliases_raw: str
    certification_interval_seconds: int
    certification_skip_stage1: bool

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "PrimeServiceConfig":
        env = env if env is not None else os.environ
        state_root = Path(_require(env, "HERMES_PRIME_STATE_ROOT"))
        if not state_root.is_absolute():
            raise PrimeEntrypointConfigError("HERMES_PRIME_STATE_ROOT must be an absolute path")
        return cls(
            state_root=state_root,
            bind_host=_require(env, "HERMES_PRIME_BIND_HOST"),
            bind_port=_int_env(env, "HERMES_PRIME_BIND_PORT", 8743),
            auth_token=_require(env, "HERMES_PRIME_AUTH_TOKEN"),
            project_id=env.get("HERMES_PRIME_PROJECT_ID", "hermes-fleet").strip() or "hermes-fleet",
            integrity_check_interval_seconds=_int_env(
                env, "HERMES_PRIME_INTEGRITY_CHECK_INTERVAL_SECONDS", 300
            ),
            node_model_aliases_raw=env.get("HERMES_PRIME_NODE_MODEL_ALIASES", ""),
            certification_interval_seconds=_int_env(
                env, "HERMES_PRIME_CERTIFICATION_INTERVAL_SECONDS", 1800
            ),
            certification_skip_stage1=env.get(
                "HERMES_PRIME_CERTIFICATION_SKIP_STAGE1", ""
            ).strip().lower() in {"1", "true", "yes"},
        )


class _CertificationHolder:
    """Thread-safe latest-certification cache shared between the background
    refresh thread and every HTTP request thread that reads it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = CertificationSnapshot(status=CertificationStatus.UNKNOWN, evidence_ref=None)

    def get(self) -> CertificationSnapshot:
        with self._lock:
            return self._snapshot

    def set(self, snapshot: CertificationSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot


def _refresh_certification(
    holder: _CertificationHolder, config: PrimeServiceConfig, *, repo_root: Path
) -> None:
    try:
        payload, status = run_certification(
            repo_root=repo_root,
            state_root=config.state_root,
            certifier_identity_id="prime-service",
            skip_stage1=config.certification_skip_stage1,
        )
        certification_status = (
            CertificationStatus.CERTIFIED
            if status.value == "certified"
            else CertificationStatus.NOT_CERTIFIED
        )
        evidence_ref = f"prime_certification:{payload['certification_id']}"
        holder.set(CertificationSnapshot(status=certification_status, evidence_ref=evidence_ref))
        logger.info("Prime fleet certification refreshed: %s", status.value)
    except Exception:  # noqa: BLE001 - a broken certification run must not crash the service
        logger.exception("Prime fleet certification refresh FAILED; leaving prior snapshot in place")


def run_prime_service(
    config: PrimeServiceConfig, *, stop_event: threading.Event, runtime: Optional[FleetRuntime] = None
) -> None:
    """Run the Prime control-plane service until ``stop_event`` is set."""
    runtime = runtime or FleetRuntime(state_root=config.state_root, project_id=config.project_id)
    node_aliases = NodeModelAliasConfig.from_env(
        {"HERMES_PRIME_NODE_MODEL_ALIASES": config.node_model_aliases_raw}
    )
    certification_holder = _CertificationHolder()
    # hermes_cli/prime/entrypoints.py -> hermes_cli/prime -> hermes_cli -> repo root.
    repo_root = Path(__file__).resolve().parents[2]

    try:
        server = build_prime_http_server(
            host=config.bind_host,
            port=config.bind_port,
            fleet_runtime=runtime,
            auth_token=config.auth_token,
            node_aliases=node_aliases,
            certification_provider=certification_holder.get,
        )
    except PrimeServerConfigError:
        logger.exception("Prime control-plane failed to start: invalid server configuration")
        raise

    thread = threading.Thread(target=server.serve_forever, name="prime-http", daemon=True)
    thread.start()
    logger.info(
        "Prime control-plane listening on %s:%s (project=%s)",
        config.bind_host, server.server_address[1], config.project_id,
    )

    def _certification_loop() -> None:
        # Refresh once immediately (off the request-serving thread, so a
        # slow Stage 1 regression run never delays accepting HTTP traffic)
        # rather than waiting a full interval before the first real snapshot.
        _refresh_certification(certification_holder, config, repo_root=repo_root)
        while not stop_event.wait(config.certification_interval_seconds):
            _refresh_certification(certification_holder, config, repo_root=repo_root)

    certification_thread = threading.Thread(
        target=_certification_loop, name="prime-certification", daemon=True
    )
    certification_thread.start()

    try:
        while not stop_event.is_set():
            stop_event.wait(config.integrity_check_interval_seconds)
            if stop_event.is_set():
                break
            try:
                runtime.visibility._evidence_store.verify_chain()
                logger.info("Prime evidence chain integrity check passed")
            except Exception:  # noqa: BLE001 - a tampered/corrupt chain must not crash the loop
                logger.exception("Prime evidence chain integrity check FAILED")
    finally:
        logger.info("Prime control-plane shutting down")
        server.shutdown()
        thread.join(timeout=10)
        certification_thread.join(timeout=5)
        logger.info("Prime control-plane stopped")


# ── Titan / Mac worker services ─────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WorkerServiceConfig:
    natural_key: str
    role: FleetNodeRole
    prime_base_url: str
    auth_token: str
    endpoint: str
    software_version: str
    protocol_version: int
    declared_capabilities: Tuple[str, ...]
    heartbeat_interval_seconds: int
    ollama_endpoint: Optional[str]
    ollama_timeout_ms: int

    @classmethod
    def from_env(
        cls, *, natural_key: str, role: FleetNodeRole, env: Optional[Mapping[str, str]] = None
    ) -> "WorkerServiceConfig":
        env = env if env is not None else os.environ
        prefix = f"HERMES_{natural_key.upper().replace('-', '_')}_"
        capabilities_raw = env.get(f"{prefix}CAPABILITIES", "")
        capabilities = tuple(c.strip() for c in capabilities_raw.split(",") if c.strip())
        return cls(
            natural_key=natural_key,
            role=role,
            prime_base_url=_require(env, "HERMES_PRIME_BASE_URL"),
            auth_token=_require(env, "HERMES_PRIME_AUTH_TOKEN"),
            endpoint=_require(env, f"{prefix}ENDPOINT"),
            software_version=env.get(f"{prefix}SOFTWARE_VERSION", "1.0.0").strip() or "1.0.0",
            protocol_version=_int_env(env, f"{prefix}PROTOCOL_VERSION", 1),
            declared_capabilities=capabilities,
            heartbeat_interval_seconds=_int_env(env, f"{prefix}HEARTBEAT_INTERVAL_SECONDS", 30),
            ollama_endpoint=env.get(f"{prefix}OLLAMA_ENDPOINT", "").strip() or None,
            ollama_timeout_ms=_int_env(env, f"{prefix}OLLAMA_TIMEOUT_MS", 5_000),
        )


def _default_health_probe(config: WorkerServiceConfig) -> Callable[[], HeartbeatSubmission]:
    """Build a real, non-mocked health probe for a worker node.

    Liveness is always ALIVE if this process is running (the heartbeat loop
    executing at all *is* the liveness signal). Readiness and model
    inventory come from an actual Ollama ``/api/tags`` call when
    ``{PREFIX}OLLAMA_ENDPOINT`` is configured — never assumed.
    """
    inspector = None
    if config.ollama_endpoint:
        try:
            inspector = OllamaNodeInspector(
                OllamaNodeConfig(
                    natural_key=config.natural_key,
                    endpoint=config.ollama_endpoint,
                    timeout_ms=config.ollama_timeout_ms,
                )
            )
        except Exception:  # noqa: BLE001 - fall back to a liveness-only probe
            logger.exception("invalid Ollama configuration for %s; heartbeats will omit model inventory", config.natural_key)

    def probe() -> HeartbeatSubmission:
        now = int(time.time())
        if inspector is None:
            return HeartbeatSubmission(
                natural_key=config.natural_key,
                liveness=LivenessState.ALIVE,
                readiness=ReadinessState.READY,
                submitted_at=now,
            )
        models = inspector.list_models()
        ollama_reachable = len(models) > 0 or _ollama_endpoint_answered(inspector)
        return HeartbeatSubmission(
            natural_key=config.natural_key,
            liveness=LivenessState.ALIVE,
            readiness=ReadinessState.READY if ollama_reachable else ReadinessState.NOT_READY,
            dependency_health={
                "ollama": DependencyHealth.HEALTHY if ollama_reachable else DependencyHealth.UNAVAILABLE
            },
            reported_model_inventory=models,
            submitted_at=now,
        )

    return probe


def _ollama_endpoint_answered(inspector: OllamaNodeInspector) -> bool:
    """True if the endpoint answered at all, even with zero models installed."""
    try:
        response = inspector.transport.get(
            f"{inspector.config.endpoint.rstrip('/')}/api/tags",
            timeout_seconds=inspector.config.timeout_ms / 1_000,
        )
        return isinstance(response, dict)
    except Exception:  # noqa: BLE001 - unreachable is a legitimate, common outcome
        return False


def run_worker_service(
    config: WorkerServiceConfig,
    *,
    stop_event: threading.Event,
    health_probe: Optional[Callable[[], HeartbeatSubmission]] = None,
    client: Optional[PrimeHTTPClient] = None,
) -> None:
    """Run a Titan/Mac worker's registration + heartbeat loop until stopped."""
    client = client or PrimeHTTPClient(base_url=config.prime_base_url, auth_token=config.auth_token)
    probe = health_probe or _default_health_probe(config)

    registration = FleetNodeRegistrationRequest(
        request_id=f"register-{config.natural_key}-{int(time.time())}",
        natural_key=config.natural_key,
        role=config.role,
        declared_capabilities=config.declared_capabilities,
        endpoint=config.endpoint,
        software_version=config.software_version,
        protocol_version=config.protocol_version,
        requested_at=int(time.time()),
    )
    try:
        client.register_node(registration)
        logger.info("%s registered with Prime at %s", config.natural_key, config.prime_base_url)
    except PrimeClientError:
        logger.exception(
            "%s initial registration with Prime failed; will keep retrying via heartbeat loop",
            config.natural_key,
        )

    while not stop_event.is_set():
        try:
            submission = probe()
            result = client.heartbeat(submission)
            logger.info("%s heartbeat -> %s", config.natural_key, result.get("connection_state"))
        except PrimeClientError:
            logger.warning("%s heartbeat failed", config.natural_key, exc_info=True)
        stop_event.wait(config.heartbeat_interval_seconds)

    logger.info("%s worker service stopped", config.natural_key)


# ── CLI entrypoint ───────────────────────────────────────────────────────────

def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(signum: int, _frame: object) -> None:
        logger.info("received signal %s, shutting down gracefully", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=os.environ.get("HERMES_PRIME_LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(prog="hermes-prime-service")
    parser.add_argument("mode", choices=["prime", "titan", "mac"])
    args = parser.parse_args(argv)

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    try:
        if args.mode == "prime":
            run_prime_service(PrimeServiceConfig.from_env(), stop_event=stop_event)
        elif args.mode == "titan":
            run_worker_service(
                WorkerServiceConfig.from_env(natural_key="titan", role=FleetNodeRole.TITAN),
                stop_event=stop_event,
            )
        else:
            run_worker_service(
                WorkerServiceConfig.from_env(natural_key="mac", role=FleetNodeRole.MAC),
                stop_event=stop_event,
            )
    except PrimeEntrypointConfigError as error:
        logger.error("configuration error: %s", error)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
