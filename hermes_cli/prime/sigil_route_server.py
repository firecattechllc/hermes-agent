"""Live HTTP-reachable completion of Sigil governed routing.

Fleet Unification live-runtime work. :mod:`hermes_cli.prime.sigil_routing`
already implements :class:`SigilRoutingService` — but it needs real
:class:`~hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter`
instances constructed from the fleet's actual registered node endpoints and
a real :class:`~hermes_cli.prime.admission.AdmissionDecision` pair (caller
+ service), and none of that existed anywhere Sigil (running on the Mac,
reaching Prime only over HTTP through :mod:`hermes_cli.prime.server`) could
call. This module is that missing assembly: it builds governed adapters on
demand from live :class:`~hermes_cli.prime.fleet_runtime.FleetRuntime`
state plus an operator-supplied, per-node Ollama model-alias configuration,
and evaluates the same two-gate contract
(:func:`hermes_cli.prime.sigil_contract.evaluate_sigil_contract_request`
followed by :class:`hermes_cli.prime.dispatch_gate.PrimeGovernedProviderAdapter.execute`'s
own independent re-check) that :mod:`hermes_cli.prime.sigil_routing` already
enforces — this module adds no new bypass of either gate.

The caller is always treated as the Mac fleet node (``natural_key="mac"``),
since Sigil runs on the Mac and that is the node whose ``desktop_use``
registration is Sigil's own admission fact — never a caller-supplied
identity, which would let an unadmitted caller assert its own admission.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.dispatch_gate import (
    CertificationSnapshot,
    InMemoryReferenceStore,
    PrimeGovernedProviderAdapter,
)
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import is_usable_for_admission
from hermes_cli.prime.ollama_node import OllamaNodeConfig, OllamaNodeProviderAdapter
from hermes_cli.prime.sigil_contract import (
    SigilContractRequest,
    SigilRejectionCode,
)
from hermes_cli.prime.sigil_routing import DEFAULT_OPERATION_ROUTES, SigilRoutingService

SIGIL_CALLER_NATURAL_KEY = "mac"


class SigilRouteConfigurationError(ValueError):
    """The operator-supplied node/model-alias configuration is invalid."""


@dataclass(frozen=True, slots=True)
class NodeModelAliasConfig:
    """Per-node governed model alias mappings, keyed by fleet natural_key."""

    aliases_by_node: Mapping[str, Mapping[str, str]]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "NodeModelAliasConfig":
        """Parse ``HERMES_PRIME_NODE_MODEL_ALIASES`` (a JSON object of
        ``{natural_key: {alias: concrete_model}}``). Absent or blank means no
        node has any alias configured yet — every route attempt then fails
        closed with ``SERVICE_NOT_ADMITTED`` rather than guessing a model.
        """
        env = env if env is not None else os.environ
        raw = env.get("HERMES_PRIME_NODE_MODEL_ALIASES", "").strip()
        if not raw:
            return cls(aliases_by_node={})
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SigilRouteConfigurationError(
                "HERMES_PRIME_NODE_MODEL_ALIASES must be valid JSON"
            ) from error
        if not isinstance(parsed, dict):
            raise SigilRouteConfigurationError(
                "HERMES_PRIME_NODE_MODEL_ALIASES must be a JSON object"
            )
        for node_key, aliases in parsed.items():
            if not isinstance(aliases, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()
            ):
                raise SigilRouteConfigurationError(
                    f"HERMES_PRIME_NODE_MODEL_ALIASES[{node_key!r}] must map alias->model strings"
                )
        return cls(aliases_by_node=parsed)

    def for_node(self, natural_key: str) -> Dict[str, str]:
        return dict(self.aliases_by_node.get(natural_key, {}))


def _build_adapter(
    *,
    fleet_runtime: FleetRuntime,
    natural_key: str,
    node_aliases: NodeModelAliasConfig,
    certification_provider: Callable[[], CertificationSnapshot],
    input_store: InMemoryReferenceStore,
) -> Optional[PrimeGovernedProviderAdapter]:
    node = fleet_runtime.registry.get(natural_key)
    if node is None:
        return None
    aliases = node_aliases.for_node(natural_key)
    if not aliases:
        return None
    try:
        ollama_config = OllamaNodeConfig(
            natural_key=natural_key, endpoint=node.endpoint, model_aliases=aliases
        )
    except ValueError:
        return None
    underlying = OllamaNodeProviderAdapter(ollama_config)

    return PrimeGovernedProviderAdapter(
        provider_id=f"{natural_key}-ollama",
        natural_key=natural_key,
        fleet_runtime=fleet_runtime,
        underlying=underlying,
        certification_provider=certification_provider,
        # Must be the *same* store SigilRoutingService.route() writes the
        # request input into (below) — a separately constructed store would
        # never resolve, silently failing every dispatch as INVALID_REQUEST.
        input_resolver=input_store.resolve,
    )


def build_sigil_routing_service(
    *,
    fleet_runtime: FleetRuntime,
    node_aliases: NodeModelAliasConfig,
    certification_provider: Callable[[], CertificationSnapshot],
) -> SigilRoutingService:
    """Assemble a :class:`SigilRoutingService` from live registered nodes.

    Only nodes that are both currently registered *and* have at least one
    configured model alias get an adapter at all — an unregistered or
    unconfigured node is simply absent from the routing table, which is
    exactly what makes :meth:`SigilRoutingService.route` reject it with
    ``SERVICE_NOT_ADMITTED`` rather than dispatching to it. Every adapter
    shares the *same* real ``certification_provider`` the caller used to
    evaluate caller/service admission above — an adapter that silently used
    a different (or hardcoded) certification snapshot could dispatch a node
    the admission check above never actually certified.
    """
    input_store = InMemoryReferenceStore()
    adapters = {}
    for natural_key in {route.natural_key for route in DEFAULT_OPERATION_ROUTES.values()}:
        adapter = _build_adapter(
            fleet_runtime=fleet_runtime,
            natural_key=natural_key,
            node_aliases=node_aliases,
            certification_provider=certification_provider,
            input_store=input_store,
        )
        if adapter is not None:
            adapters[natural_key] = adapter
    return SigilRoutingService(adapters=adapters, input_store=input_store)


def handle_sigil_route_request(
    *,
    fleet_runtime: FleetRuntime,
    node_aliases: NodeModelAliasConfig,
    certification_provider: Callable[[], CertificationSnapshot],
    body: Dict[str, Any],
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Handle one ``POST /v1/sigil/route`` request body end to end.

    Returns a plain JSON-serializable dict — either the routed
    ``SigilContractResponse`` shape, or ``{"ok": False, "error": ...}`` for a
    malformed request (never a stack trace, never a fabricated success).
    """
    now = now if now is not None else int(time.time())
    operation = body.get("operation")
    if not isinstance(operation, str) or not operation:
        return {"ok": False, "error": "invalid_request", "message": "operation is required"}

    route = DEFAULT_OPERATION_ROUTES.get(operation)
    if route is None:
        return {
            "ok": False,
            "error": "unsupported_operation",
            "message": f"no governed route is configured for operation {operation!r}",
        }

    caller_node = fleet_runtime.registry.get(SIGIL_CALLER_NATURAL_KEY)
    service_node = fleet_runtime.registry.get(route.natural_key)
    if caller_node is None or service_node is None:
        return {
            "ok": False,
            "error": SigilRejectionCode.CALLER_NOT_ADMITTED.value
            if caller_node is None
            else SigilRejectionCode.SERVICE_NOT_ADMITTED.value,
            "message": "caller or service fleet node is not registered",
        }

    certification = certification_provider()

    raw_input_payload = body.get("input_payload")
    input_payload: Dict[str, Any] = raw_input_payload if isinstance(raw_input_payload, dict) else {}

    try:
        request = SigilContractRequest(
            request_id=f"sigil_route_{operation}_{now}",
            correlation_id=f"sigil_route_corr_{now}",
            caller_identity_id=caller_node.identity_id,
            service_identity_id=service_node.identity_id,
            operation=operation,
            requested_at=now,
            timeout_seconds=int(body.get("timeout_seconds", 30)),
            input_payload=input_payload,
        )
    except ValueError as error:
        return {"ok": False, "error": "invalid_request", "message": str(error)}

    caller_admission = fleet_runtime.evaluate_admission(
        SIGIL_CALLER_NATURAL_KEY,
        now=now,
        certification_status=certification.status,
        certification_evidence_ref=certification.evidence_ref,
    )
    service_admission = fleet_runtime.evaluate_admission(
        route.natural_key,
        now=now,
        certification_status=certification.status,
        certification_evidence_ref=certification.evidence_ref,
    )
    caller_health = fleet_runtime.latest_health(SIGIL_CALLER_NATURAL_KEY)
    service_health = fleet_runtime.latest_health(route.natural_key)

    routing_service = build_sigil_routing_service(
        fleet_runtime=fleet_runtime,
        node_aliases=node_aliases,
        certification_provider=certification_provider,
    )
    response = routing_service.route(
        request,
        caller_admission=caller_admission,
        service_admission=service_admission,
        caller_health=caller_health,
        service_health=service_health,
        now=now,
    )
    result = response.model_dump(mode="json")
    result["ok"] = response.outcome.value == "accepted"
    result["caller_admitted"] = caller_admission.outcome.value == "admitted"
    result["service_admitted"] = service_admission.outcome.value == "admitted"
    result["caller_health_usable"] = is_usable_for_admission(caller_health, now=now)
    result["service_health_usable"] = is_usable_for_admission(service_health, now=now)
    return result
