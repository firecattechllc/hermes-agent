#!/usr/bin/env python3
"""Generate a read-only, credential-free governed fleet certification report."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from sigil.ai import (
    Capability,
    CPUClass,
    DeviceClass,
    FleetModelInventory,
    FleetNodeHealth,
    FleetNodeIdentity,
    FleetNodeRegistration,
    FleetNodeRole,
    FleetNodeState,
    MemoryClass,
    PrivacyTier,
    ProviderHealth,
    TrustTier,
    WorkerTaskType,
)
from sigil.ai.models import ExecutionLocation
from sigil.ai.registry import canonical_digest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Validate one authenticated fleet-node declaration without contacting it."
    )
    value.add_argument("--node-id", required=True)
    value.add_argument("--node-role", required=True, choices=("titan", "mac", "prime"))
    value.add_argument("--authenticated-identity-ref", required=True)
    value.add_argument("--transport-identity", required=True)
    value.add_argument("--provider-id", required=True)
    value.add_argument("--model-id", required=True)
    value.add_argument("--observed-at", required=True)
    value.add_argument("--node-timestamp", required=True)
    return value


def main() -> int:
    arguments = parser().parse_args()
    role = FleetNodeRole(arguments.node_role)
    identity = FleetNodeIdentity(
        arguments.node_id,
        role.value,
        role,
        DeviceClass.WORKSTATION if role == FleetNodeRole.MAC else DeviceClass.SERVER,
        "declared-platform",
        "declared-architecture",
        "declared-operating-system",
        TrustTier.TRUSTED,
        PrivacyTier.LOCAL_ONLY,
        ExecutionLocation.FLEET,
        arguments.transport_identity,
        arguments.authenticated_identity_ref,
        arguments.observed_at,
        arguments.observed_at,
        True,
        True,
    )
    model = FleetModelInventory(
        arguments.provider_id,
        arguments.model_id,
        None,
        frozenset({Capability.REASONING}),
    )
    registration = FleetNodeRegistration(
        identity,
        (model,),
        frozenset({WorkerTaskType.RESEARCH_PREPARATION}),
        MemoryClass.MEDIUM,
        CPUClass.STANDARD,
        None,
        1,
        30_000,
        4_096,
        4_096,
        resource_enforcement_verified=False,
        enabled=True,
        health=ProviderHealth.HEALTHY,
    )
    health = FleetNodeHealth(
        identity.node_id,
        identity.authenticated_identity_ref,
        arguments.observed_at,
        arguments.node_timestamp,
        FleetNodeState.HEALTHY,
        registration.capabilities,
        (model.model_id,),
        0,
        0,
        0,
        "unknown",
        "unknown",
        "unknown",
        ProviderHealth.HEALTHY,
        False,
        False,
    )
    report = {
        "schema_version": 1,
        "node_id": identity.node_id,
        "node_role": role.value,
        "identity_reference_digest": f"sha256:{canonical_digest(identity.authenticated_identity_ref)}",
        "transport_identity_digest": f"sha256:{canonical_digest(identity.transport_identity)}",
        "model_id": model.model_id,
        "provider_id": model.provider_id,
        "freshness_at_observation": health.freshness(coordinator_time=arguments.observed_at),
        "resource_enforcement_verified": registration.resource_enforcement_verified,
        "network_contact_attempted": False,
        "system_configuration_modified": False,
        "credentials_exposed": False,
        "paper_only": True,
        "execution_authorized": False,
        "broker_submission": False,
        "report_digest": f"sha256:{canonical_digest(asdict(health))}",
        "limitations": [
            "This preflight validates a declared identity and inventory only.",
            "Transport connectivity and real-device task/cancellation/failover remain operator-run checks.",
        ],
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
