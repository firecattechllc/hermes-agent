"""Production entry point for Titan's local OmniRoute service.

Mirrors :mod:`hermes_cli.prime.entrypoints`'s run-mode pattern (environment-
only configuration, ``threading.Event``-based graceful shutdown on
SIGTERM/SIGINT) without modifying that module — OmniRoute is a distinct
Titan-local service (see ``deploy/titan/omniroute.service``), not another
mode of the existing Prime/Titan-heartbeat entrypoint.

Startup order: :meth:`TitanRoutingConfig.from_env` (which runs Mac-dependency
and every other configuration invariant check) completes — and therefore
either raises or fully succeeds — *before* :func:`build_omniroute_http_server`
ever opens a socket. There is no window in which a partially-validated or
Mac-dependent configuration can begin serving requests.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from typing import Callable, Dict, Optional

from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.ollama_node import OllamaNodeConfig
from hermes_cli.prime.omniroute_config import (
    TitanRoutingConfig,
    TitanRoutingConfigError,
)
from hermes_cli.prime.omniroute_health import build_health_snapshot
from hermes_cli.prime.omniroute_server import build_omniroute_http_server
from hermes_cli.prime.omniroute_upstreams import (
    FreeLLMAPIUpstreamAdapter,
    TitanOllamaUpstreamAdapter,
)

logger = logging.getLogger("hermes.prime.omniroute.entrypoint")


def build_upstreams(config: TitanRoutingConfig) -> Dict[str, object]:
    """Build only the upstream adapters this configuration actually enables.

    A disabled provider gets no adapter at all (not a disabled-but-present
    one) — :mod:`hermes_cli.prime.omniroute_server` already treats a missing
    upstream as ``upstream_not_configured``, so there is no ambiguity
    between "disabled" and "misconfigured."
    """
    upstreams: Dict[str, object] = {}

    if config.titan_ollama_enabled:
        model_aliases = {
            alias: model
            for alias, (provider_id, model) in config.alias_routes.items()
            if provider_id == "titan_ollama"
        }
        node_config = OllamaNodeConfig(
            natural_key="titan",
            endpoint=config.titan_ollama_endpoint,
            model_aliases=model_aliases,
            timeout_ms=config.provider_timeout_ms.get("titan_ollama", 30_000),
        )
        upstreams["titan_ollama"] = TitanOllamaUpstreamAdapter(
            node_config=node_config,
            retry_limit=config.provider_retry_limit.get("titan_ollama", 1),
        )

    if config.freellmapi_enabled and not config.offline_local_only:
        upstreams["freellmapi"] = FreeLLMAPIUpstreamAdapter(
            base_url=config.freellmapi_base_url,
            api_key=config.freellmapi_api_key,
            timeout_ms=config.provider_timeout_ms.get("freellmapi", 20_000),
            retry_limit=config.provider_retry_limit.get("freellmapi", 2),
        )

    return upstreams


def run_omniroute_service(
    config: TitanRoutingConfig,
    *,
    stop_event: threading.Event,
    evidence_store: Optional[PrimeEvidenceStore] = None,
    upstreams: Optional[Dict[str, object]] = None,
    hermes_router_reachable: Optional[Callable[[], bool]] = None,
) -> None:
    """Run the OmniRoute HTTP service until ``stop_event`` is set."""
    if not config.omniroute_enabled:
        logger.info("HERMES_OMNIROUTE_ENABLED=false; OmniRoute service will not start")
        return

    resolved_upstreams = upstreams if upstreams is not None else build_upstreams(config)
    store = evidence_store if evidence_store is not None else PrimeEvidenceStore()

    server = build_omniroute_http_server(
        config=config,
        upstreams=resolved_upstreams,
        evidence_store=store,
        hermes_router_reachable=hermes_router_reachable,
    )
    thread = threading.Thread(
        target=server.serve_forever, name="omniroute-http", daemon=True
    )
    thread.start()
    logger.info(
        "OmniRoute listening on %s:%s (providers=%s, offline_local_only=%s)",
        config.bind_host,
        server.server_address[1],
        sorted(resolved_upstreams),
        config.offline_local_only,
    )

    try:
        while not stop_event.is_set():
            stop_event.wait(config.health_check_interval_seconds)
            if stop_event.is_set():
                break
            snapshot = build_health_snapshot(
                omniroute_enabled=config.omniroute_enabled,
                titan_ollama_enabled=config.titan_ollama_enabled,
                freellmapi_enabled=config.freellmapi_enabled,
                offline_local_only=config.offline_local_only,
                titan_ollama_circuit=getattr(
                    resolved_upstreams.get("titan_ollama"), "circuit_breaker", None
                ),
                freellmapi_circuit=getattr(
                    resolved_upstreams.get("freellmapi"), "circuit_breaker", None
                ),
                hermes_router_reachable=(hermes_router_reachable or (lambda: True))(),
                now=int(time.time()),
            )
            logger.info(
                "OmniRoute health: status=%s titan_ollama=%s freellmapi=%s internet_degraded=%s",
                snapshot.operational_status.value,
                snapshot.titan_ollama.value,
                snapshot.freellmapi.value,
                snapshot.internet_dependent_degradation,
            )
    finally:
        logger.info("OmniRoute shutting down")
        server.shutdown()
        thread.join(timeout=10)
        logger.info("OmniRoute stopped")


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(signum: int, _frame: object) -> None:
        logger.info("received signal %s, shutting down gracefully", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main(argv: Optional[list] = None) -> int:
    del argv
    import os

    logging.basicConfig(level=os.environ.get("HERMES_OMNIROUTE_LOG_LEVEL", "INFO"))
    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    try:
        config = TitanRoutingConfig.from_env()
    except TitanRoutingConfigError as error:
        logger.error("OmniRoute configuration rejected: %s", error)
        return 2

    run_omniroute_service(config, stop_event=stop_event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
