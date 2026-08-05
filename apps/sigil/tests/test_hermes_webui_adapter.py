from __future__ import annotations

from dataclasses import replace

import pytest

from sigil.hermes_webui_adapter import (
    HermesNodeRole,
    HermesWebUIHealth,
    HermesWebUIProbe,
    HermesWebUITarget,
    HermesWebUIValidationError,
    build_deep_link,
    default_hermes_webui_targets,
    evaluate_webui_status,
    probe_webui_target,
)
from sigil.worker_contract import WORKER_CONTRACT_SCHEMA_VERSION

NOW = "2026-08-02T00:00:00Z"
RECENT = "2026-08-01T23:59:30Z"
STALE = "2026-08-01T23:50:00Z"


def target(**changes: object) -> HermesWebUITarget:
    values: dict[str, object] = {
        "node_id": "hermes-titan",
        "display_name": "Hermes Titan",
        "role": HermesNodeRole.PRIMARY,
        "base_url": "http://100.103.4.38",
        "approved_routes": ("/", "/chat", "/health", "/approvals"),
        "enabled": False,
    }
    values.update(changes)
    return HermesWebUITarget(**values)


def probe(**changes: object) -> HermesWebUIProbe:
    values: dict[str, object] = {
        "node_id": "hermes-titan",
        "observed_at": RECENT,
        "responding": True,
        "dashboard_version": "0.20.0",
        "worker_contract_schema": WORKER_CONTRACT_SCHEMA_VERSION,
        "component_health": "healthy",
        "sanitized_message": "Dashboard components are healthy.",
    }
    values.update(changes)
    return HermesWebUIProbe(**values)


def test_default_targets_model_titan_and_mac_and_remain_disabled() -> None:
    titan, mac = default_hermes_webui_targets()

    assert titan.node_id == "hermes-titan"
    assert titan.role == HermesNodeRole.PRIMARY
    assert titan.enabled is False

    assert mac.node_id == "hermes-mac"
    assert mac.role == HermesNodeRole.SENIOR
    assert mac.enabled is False

    for value in (titan, mac):
        assert value.can_authenticate is False
        assert value.can_dispatch is False
        assert value.can_start_service is False
        assert value.authority.broker_submission is False
        assert value.authority.execution_authorized is False
        assert value.authority.credential_access is False


def test_disabled_target_fails_closed_even_with_healthy_probe() -> None:
    status = evaluate_webui_status(target(), probe(), now=NOW)

    assert status.state == HermesWebUIHealth.DISABLED
    assert status.deep_link_available is False
    assert status.worker_contract_compatible is False


def test_enabled_healthy_target_allows_only_deep_link_projection() -> None:
    enabled = target(enabled=True)
    status = evaluate_webui_status(enabled, probe(), now=NOW)

    assert status.state == HermesWebUIHealth.HEALTHY
    assert status.deep_link_available is True
    assert status.worker_contract_compatible is True
    assert build_deep_link(
        enabled,
        "/chat",
        query={"profile": "default"},
    ) == "http://100.103.4.38/chat?profile=default"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://example.com", "private"),
        ("http://localhost:8080", "localhost"),
        ("http://user:pass@100.103.4.38", "credentials"),
        ("http://100.103.4.38/path", "private origin"),
        ("ftp://100.103.4.38", "private origin"),
    ],
)
def test_public_malformed_and_credentialed_targets_are_rejected(
    url: str,
    message: str,
) -> None:
    with pytest.raises(HermesWebUIValidationError, match=message):
        target(base_url=url)


def test_tailnet_dns_names_are_allowed() -> None:
    value = target(base_url="https://titan.example.ts.net")
    assert value.base_url == "https://titan.example.ts.net"


def test_unapproved_routes_and_query_keys_fail_closed() -> None:
    enabled = target(enabled=True)

    with pytest.raises(HermesWebUIValidationError, match="route"):
        build_deep_link(enabled, "/admin")

    with pytest.raises(HermesWebUIValidationError, match="query"):
        build_deep_link(enabled, "/chat", query={"token": "secret"})


def test_disabled_target_cannot_build_deep_link() -> None:
    with pytest.raises(HermesWebUIValidationError, match="disabled"):
        build_deep_link(target(), "/chat")


def test_stale_unavailable_degraded_and_incompatible_health_are_distinct() -> None:
    enabled = target(enabled=True)

    stale = evaluate_webui_status(
        enabled,
        probe(observed_at=STALE),
        now=NOW,
    )
    unavailable = evaluate_webui_status(
        enabled,
        probe(responding=False),
        now=NOW,
    )
    degraded = evaluate_webui_status(
        enabled,
        probe(component_health="degraded"),
        now=NOW,
    )
    incompatible = evaluate_webui_status(
        enabled,
        probe(worker_contract_schema=999),
        now=NOW,
    )

    assert stale.state == HermesWebUIHealth.STALE
    assert unavailable.state == HermesWebUIHealth.UNAVAILABLE
    assert degraded.state == HermesWebUIHealth.DEGRADED
    assert incompatible.state == HermesWebUIHealth.INCOMPATIBLE

    assert stale.deep_link_available is False
    assert unavailable.deep_link_available is False
    assert degraded.deep_link_available is True
    assert incompatible.deep_link_available is False


def test_missing_probe_is_unavailable_and_not_authority() -> None:
    status = evaluate_webui_status(
        target(enabled=True),
        None,
        now=NOW,
    )

    assert status.state == HermesWebUIHealth.UNAVAILABLE
    assert status.deep_link_available is False
    assert status.authority.activation_authorized is False
    assert status.authority.installation_authorized is False
    assert status.authority.governance_bypass is False


def test_probe_identity_mismatch_and_future_timestamp_fail_closed() -> None:
    enabled = target(enabled=True)

    with pytest.raises(HermesWebUIValidationError, match="does not match"):
        evaluate_webui_status(
            enabled,
            probe(node_id="hermes-mac"),
            now=NOW,
        )

    with pytest.raises(HermesWebUIValidationError, match="future"):
        evaluate_webui_status(
            enabled,
            probe(observed_at="2026-08-02T00:01:00Z"),
            now=NOW,
        )


def test_target_and_probe_reject_sensitive_material() -> None:
    with pytest.raises(HermesWebUIValidationError, match="credential"):
        target(display_name="password=secret")

    with pytest.raises(HermesWebUIValidationError, match="credential"):
        probe(sanitized_message="api_key=sk-secretvalue")


def test_incompatible_worker_contract_target_is_rejected_at_definition() -> None:
    with pytest.raises(HermesWebUIValidationError, match="incompatible"):
        target(expected_worker_contract_schema=999)


def test_target_digest_detects_mutation() -> None:
    value = target()

    with pytest.raises(HermesWebUIValidationError, match="digest"):
        replace(value, display_name="Changed")


def test_probe_rejects_disabled_target() -> None:
    with pytest.raises(HermesWebUIValidationError, match="disabled"):
        probe_webui_target(target(enabled=False))


def test_probe_rejects_out_of_bounds_timeout() -> None:
    with pytest.raises(HermesWebUIValidationError, match="timeout"):
        probe_webui_target(target(enabled=True), timeout_seconds=0)

    with pytest.raises(HermesWebUIValidationError, match="timeout"):
        probe_webui_target(target(enabled=True), timeout_seconds=31)


def test_probe_degrades_to_unavailable_on_connection_failure() -> None:
    # 192.0.2.0/24 is TEST-NET-1 (RFC 5737): reserved, never routable, and
    # guaranteed not to answer, so this exercises the real failure path
    # without any live network dependency or flakiness.
    unreachable = target(enabled=True, base_url="http://192.0.2.1")

    result = probe_webui_target(unreachable, timeout_seconds=1)

    assert result.responding is False
    assert result.component_health == "unavailable"
    assert result.sanitized_message == "connection failed"
    assert result.node_id == unreachable.node_id


def test_probe_result_feeds_evaluate_webui_status_consistently() -> None:
    unreachable = target(enabled=True, base_url="http://192.0.2.1")
    result = probe_webui_target(unreachable, timeout_seconds=1, now=NOW)

    status = evaluate_webui_status(unreachable, result, now=NOW)

    assert status.state == HermesWebUIHealth.UNAVAILABLE
    assert status.enabled is True
