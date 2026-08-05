from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request

import pytest

from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore
from hermes_cli.prime.admission import CertificationStatus
from hermes_cli.prime.client import PrimeClientError, PrimeHTTPClient
from hermes_cli.prime.evidence import PrimeEvidenceStore
from hermes_cli.prime.fleet_registry import FleetNodeRegistrationRequest, FleetNodeRole
from hermes_cli.prime.fleet_runtime import FleetRuntime
from hermes_cli.prime.health import LivenessState, ReadinessState
from hermes_cli.prime.heartbeat import HeartbeatSubmission
from hermes_cli.prime.server import PrimeServerConfigError, build_prime_http_server

AUTH_TOKEN = "test-token-0123456789abcdef"


def _now() -> int:
    return int(time.time())


@pytest.fixture()
def live_server(tmp_path):
    runtime = FleetRuntime(
        state_root=tmp_path / "prime",
        project_id="server-test",
        mission_control=MissionControlService(store=MissionControlStore(root=tmp_path / "mc")),
        evidence_store=PrimeEvidenceStore(state_root=tmp_path / "prime-evidence"),
    )
    server = build_prime_http_server(
        host="127.0.0.1", port=0, fleet_runtime=runtime, auth_token=AUTH_TOKEN
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", runtime
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_wildcard_host_is_rejected(tmp_path) -> None:
    runtime = FleetRuntime(state_root=tmp_path / "prime")
    with pytest.raises(PrimeServerConfigError):
        build_prime_http_server(host="0.0.0.0", port=0, fleet_runtime=runtime, auth_token=AUTH_TOKEN)


def test_blank_host_is_rejected(tmp_path) -> None:
    runtime = FleetRuntime(state_root=tmp_path / "prime")
    with pytest.raises(PrimeServerConfigError):
        build_prime_http_server(host="", port=0, fleet_runtime=runtime, auth_token=AUTH_TOKEN)


def test_weak_auth_token_is_rejected(tmp_path) -> None:
    runtime = FleetRuntime(state_root=tmp_path / "prime")
    with pytest.raises(PrimeServerConfigError):
        build_prime_http_server(host="127.0.0.1", port=0, fleet_runtime=runtime, auth_token="short")


def test_health_check_requires_no_auth(live_server) -> None:
    base_url, _ = live_server
    client = PrimeHTTPClient(base_url=base_url, auth_token="wrong-but-unused-for-health")
    assert client.health() == {"status": "ok"}


def test_register_and_heartbeat_round_trip_over_real_http(live_server) -> None:
    base_url, runtime = live_server
    client = PrimeHTTPClient(base_url=base_url, auth_token=AUTH_TOKEN)
    now = _now()

    decision = client.register_node(
        FleetNodeRegistrationRequest(
            request_id="req-titan", natural_key="titan", role=FleetNodeRole.TITAN,
            declared_capabilities=("worker_heartbeat",),
            endpoint="http://titan.tailnet.internal:11434",
            software_version="1.0.0", protocol_version=1, requested_at=now,
        )
    )
    assert decision["outcome"] == "registered"

    result = client.heartbeat(
        HeartbeatSubmission(
            natural_key="titan", liveness=LivenessState.ALIVE, readiness=ReadinessState.READY,
            submitted_at=now,
        )
    )
    assert result["outcome"] == "accepted"
    assert result["connection_state"] == "connected"

    # The runtime backing the server actually observed the state — this is
    # real dispatch through FleetRuntime, not a mocked response.
    assert runtime.get_node("titan") is not None
    assert runtime.is_dispatchable(
        "titan", now=now,
        certification_status=CertificationStatus.CERTIFIED,
        certification_evidence_ref="evidence://cert",
    ) is True


def test_unauthorized_request_is_rejected(live_server) -> None:
    base_url, _ = live_server
    client = PrimeHTTPClient(base_url=base_url, auth_token="totally-wrong-token-value")
    with pytest.raises(PrimeClientError):
        client.register_node(
            FleetNodeRegistrationRequest(
                request_id="req-1", natural_key="titan", role=FleetNodeRole.TITAN,
                endpoint="http://titan.tailnet.internal:11434",
                software_version="1.0.0", protocol_version=1, requested_at=_now(),
            )
        )


def test_unknown_node_registration_returns_conflict_over_http(live_server) -> None:
    base_url, _ = live_server
    client = PrimeHTTPClient(base_url=base_url, auth_token=AUTH_TOKEN)
    with pytest.raises(PrimeClientError):
        client.register_node(
            FleetNodeRegistrationRequest(
                request_id="req-bad", natural_key="attacker-node", role=FleetNodeRole.TITAN,
                endpoint="http://x.tailnet.internal",
                software_version="1.0.0", protocol_version=1, requested_at=_now(),
            )
        )


def test_list_nodes_requires_authorization(live_server) -> None:
    base_url, runtime = live_server
    request = urllib.request.Request(f"{base_url}/v1/fleet/nodes", method="GET")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310
    assert excinfo.value.code == 401
