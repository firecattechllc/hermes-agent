import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from hermes_cli.hermes_link.api import create_app
from hermes_cli.hermes_link.client import HermesLinkClient
from hermes_cli.hermes_link.coordinator import CoordinatorTarget, MacCoordinatorConfig
from hermes_cli.hermes_link.security import (
    CredentialRegistry,
    CredentialEvidenceStore,
    CredentialStatus,
    DurableReplayStore,
    HermesLinkAuthenticationError,
    SignedRequestAuthenticator,
    SigningCredential,
    build_signed_request,
    payload_digest,
    resolve_secret,
)
from hermes_cli.hermes_link.runtime import SignedServiceConfig, build_app
from hermes_cli.hermes_link.service import HermesLinkService
from hermes_cli.hermes_link.store import HermesLinkStore


NOW = 1_786_000_000


def credential(tmp_path, **changes):
    secret = tmp_path / "credential.secret"
    secret.write_text("a" * 64)
    secret.chmod(0o600)
    values = {
        "credential_id": "credential-1",
        "secret_reference": f"file:{secret}",
        "coordinator_node_id": "mac-hermes",
        "target_node_id": "titan-hermes",
        "status": CredentialStatus.ACTIVE,
        "not_before": NOW - 60,
        "expires_at": NOW + 600,
    }
    values.update(changes)
    return SigningCredential(**values)


def authenticator(tmp_path, item=None):
    item = item or credential(tmp_path)
    return SignedRequestAuthenticator(
        CredentialRegistry(credentials=(item,)),
        DurableReplayStore(tmp_path / "replay.jsonl"),
        target_node_id=item.target_node_id,
    )


def signed(item, *, body=b"", method="GET", path="/status", **changes):
    request = build_signed_request(
        method,
        path,
        body,
        item,
        now=changes.pop("timestamp", NOW),
        request_id=changes.pop("request_id", "request-1"),
        nonce=changes.pop("nonce", "A" * 24),
    )
    request = request.model_copy(update=changes)
    return request, request.headers(resolve_secret(item.secret_reference))


def test_valid_signature_and_restart_safe_replay_rejection(tmp_path):
    item = credential(tmp_path)
    request, headers = signed(item)
    authenticator(tmp_path, item).verify("GET", "/status", headers, b"", now=NOW)
    with pytest.raises(HermesLinkAuthenticationError, match="already accepted"):
        authenticator(tmp_path, item).verify("GET", "/status", headers, b"", now=NOW)
    assert request.credential_id == "credential-1"
    evidence = (tmp_path / "replay.jsonl").read_text()
    assert "signature_accepted" in evidence
    assert "a" * 32 not in evidence
    assert headers["X-Hermes-Link-Signature"] not in evidence


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"coordinator_node_id": "other-node"}, "coordinator_identity_mismatch"),
        ({"target_node_id": "prime-hermes"}, "target_identity_mismatch"),
        ({"timestamp": NOW - 121}, "expired_request"),
        ({"timestamp": NOW + 121}, "future_request"),
    ],
)
def test_identity_and_time_fail_closed(tmp_path, change, code):
    item = credential(tmp_path)
    _, headers = signed(item, **change)
    with pytest.raises(HermesLinkAuthenticationError) as error:
        authenticator(tmp_path, item).verify("GET", "/status", headers, b"", now=NOW)
    assert error.value.code == code


def test_clock_boundary_nonce_duplicate_signature_and_payload(tmp_path):
    item = credential(tmp_path)
    auth = authenticator(tmp_path, item)
    _, first = signed(item, timestamp=NOW - 120)
    auth.verify("GET", "/status", first, b"", now=NOW)
    _, reused_nonce = signed(item, request_id="request-2")
    with pytest.raises(HermesLinkAuthenticationError) as error:
        auth.verify("GET", "/status", reused_nonce, b"", now=NOW)
    assert error.value.code == "replayed_nonce"
    body = json.dumps({"safe": True}).encode()
    _, invalid = signed(item, body=body, method="POST", path="/task", nonce="B" * 24)
    invalid["X-Hermes-Link-Signature"] = "0" * 64
    with pytest.raises(HermesLinkAuthenticationError) as error:
        auth.verify("POST", "/task", invalid, body, now=NOW)
    assert error.value.code == "invalid_signature"
    _, mismatch = signed(
        item,
        body=body,
        method="POST",
        path="/task",
        nonce="C" * 24,
        request_id="request-3",
    )
    with pytest.raises(HermesLinkAuthenticationError) as error:
        auth.verify("POST", "/task", mismatch, b'{"safe":false}', now=NOW)
    assert error.value.code == "payload_digest_mismatch"


def test_revoked_retiring_rotation_and_registry_permissions(tmp_path):
    active = credential(tmp_path)
    retiring = active.model_copy(
        update={"credential_id": "credential-old", "status": CredentialStatus.RETIRING}
    )
    assert retiring.usable(now=NOW)
    assert not active.model_copy(update={"status": CredentialStatus.REVOKED}).usable(
        now=NOW
    )
    registry = CredentialRegistry(credentials=(active, retiring))
    path = tmp_path / "credentials.json"
    path.write_text(registry.model_dump_json())
    path.chmod(0o600)
    assert CredentialRegistry.load(path) == registry
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        CredentialRegistry.load(path)


def test_credential_lifecycle_evidence_is_sanitized_and_hash_chained(tmp_path):
    item = credential(tmp_path)
    store = CredentialEvidenceStore(tmp_path / "credential-evidence.jsonl")
    store.append(
        "credential_enrolled",
        item.credential_id,
        item.coordinator_node_id,
        item.target_node_id,
        recorded_at=NOW,
    )
    store.append(
        "credential_revoked",
        item.credential_id,
        item.coordinator_node_id,
        item.target_node_id,
        recorded_at=NOW + 1,
    )
    records = store.read()
    assert [value["event"] for value in records] == [
        "credential_enrolled",
        "credential_revoked",
    ]
    evidence = store.path.read_text()
    assert "secret_reference" not in evidence and "a" * 32 not in evidence
    assert records[1]["previous_entry_hash"] == records[0]["entry_hash"]


class ASGITransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        return self.client.request(method, url.split("testserver", 1)[-1], **kwargs)


def test_signed_api_and_client_require_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: NOW)
    item = credential(tmp_path)
    registry = CredentialRegistry(credentials=(item,))
    service = HermesLinkService(
        HermesLinkStore(tmp_path / "queue"),
        local_node="titan-hermes",
        peer_node="mac-hermes",
    )
    api = TestClient(
        create_app(service, signed_authenticator=authenticator(tmp_path / "auth", item))
    )
    response = api.get("/status")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_failed"
    client = HermesLinkClient(
        "http://testserver",
        credential_registry=registry,
        coordinator_node_id="mac-hermes",
        target_node_id="titan-hermes",
        transport=ASGITransport(api),
    )
    result = client.fetch_status()
    assert result.ok and result.status.node_id == "titan-hermes"


def test_malformed_payload_digest_is_rejected():
    with pytest.raises(HermesLinkAuthenticationError) as error:
        payload_digest(b"not-json")
    assert error.value.code == "malformed_payload"


def test_titan_and_prime_service_configs_are_restricted(tmp_path):
    item = credential(tmp_path)
    registry_path = tmp_path / "credentials.json"
    registry_path.write_text(CredentialRegistry(credentials=(item,)).model_dump_json())
    registry_path.chmod(0o600)
    for node in ("titan-hermes", "prime-hermes"):
        bound = item.model_copy(update={"target_node_id": node})
        registry_path.write_text(
            CredentialRegistry(credentials=(bound,)).model_dump_json()
        )
        config = SignedServiceConfig(
            local_node_id=node,
            coordinator_node_id="mac-hermes",
            state_root=tmp_path / node,
            credential_registry_path=registry_path,
        )
        assert build_app(config).docs_url is None
        assert not config.shell_allowed and not config.network_allowed
        assert not config.broker_available and not config.portfolio_available
    with pytest.raises(ValueError, match="prohibited authority"):
        SignedServiceConfig(
            local_node_id="prime-hermes",
            coordinator_node_id="mac-hermes",
            state_root=tmp_path / "prime",
            credential_registry_path=registry_path,
            shell_allowed=True,
        ).verify_no_authority()


def test_startup_without_credentials_or_remote_nodes_fails_closed(tmp_path):
    config = SignedServiceConfig(
        local_node_id="prime-hermes",
        coordinator_node_id="mac-hermes",
        state_root=tmp_path / "prime",
        credential_registry_path=tmp_path / "missing.json",
    )
    with pytest.raises(ValueError, match="regular file"):
        build_app(config)


def test_systemd_service_enforces_restricted_runtime():
    unit = (
        Path(__file__).parents[3] / "deploy/hermes-link/hermes-link.service"
    ).read_text()
    for required in (
        "User=hermes",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "PrivateDevices=true",
        "MemoryDenyWriteExecute=true",
        "MemoryMax=4G",
        "CPUQuota=200%",
        "TasksMax=64",
        "CapabilityBoundingSet=",
        "ReadWritePaths=/var/lib/hermes-link",
        "UMask=0077",
    ):
        assert required in unit
    assert "sudo" not in unit and "bash" not in unit and "sh -c" not in unit


def test_mac_coordinator_is_backend_only_sanitized_and_optional(tmp_path):
    target = CoordinatorTarget(
        node_id="titan-hermes",
        authenticated_identity_ref="tailnet-node:titan-reference",
        tailnet_dns_identity="titan.example.ts.net",
        base_url="http://127.0.0.1:19320",
    )
    disabled = MacCoordinatorConfig(
        credential_registry_path=tmp_path / "credentials.json", targets=(target,)
    )
    assert disabled.clients() == {}
    status = disabled.sanitized_status()
    assert status["credential_material"] == "not_displayed"
    assert status["paper_only"] and not status["broker_submission"]
    assert "base_url" not in status["targets"][0]
    with pytest.raises(ValueError, match="local tunnel"):
        CoordinatorTarget.model_validate({
            **target.model_dump(),
            "base_url": "http://renderer.invalid:9320",
        })
