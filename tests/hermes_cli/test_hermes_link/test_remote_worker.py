from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from time import sleep

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.hermes_link.remote_worker import (
    DurableRemoteTaskStore,
    GovernedRemoteWorkerService,
    SignedFleetHTTPAdapter,
    attach_remote_worker_routes,
    digest_evidence_handler,
)
from hermes_cli.hermes_link.security import (
    CredentialRegistry,
    DurableReplayStore,
    SignedRequestAuthenticator,
    SigningCredential,
)
from sigil.ai import (
    CPUClass,
    Capability,
    GovernedRemoteTask,
    MemoryClass,
    PrivacyTier,
    RemoteTaskState,
    TrustTier,
    WorkerTaskType,
)


DIGEST = "sha256:" + "a" * 64


def task(**changes):
    values = {
        "remote_task_id": "remote-task-1",
        "fleet_request_id": "fleet-request-1",
        "orchestration_id": "orchestration-1",
        "step_id": "step-1",
        "node_id": "prime-hermes",
        "task_type": WorkerTaskType.RESEARCH_PREPARATION,
        "capability": Capability.REASONING,
        "input_digests": (DIGEST,),
        "expected_output_schema": "sigil.ai.output.remote-specialist.v1",
        "timeout_ms": 500,
        "memory_class": MemoryClass.SMALL,
        "cpu_class": CPUClass.STANDARD,
        "maximum_input_chars": 1024,
        "maximum_output_chars": 1024,
        "privacy_requirement": PrivacyTier.LOCAL_ONLY,
        "trust_requirement": TrustTier.TRUSTED,
        "cancellation_token_id": "cancel-1",
        "requested_at": "2026-08-01T18:00:00+00:00",
    }
    values.update(changes)
    return GovernedRemoteTask(**values)


def service(tmp_path):
    return GovernedRemoteWorkerService(
        "prime-hermes",
        DurableRemoteTaskStore(tmp_path / "tasks.json"),
        {
            "research_preparation": digest_evidence_handler,
            "deterministic_calculation": digest_evidence_handler,
        },
        maximum_timeout_ms=1000,
        maximum_output_chars=2048,
    )


def test_deterministic_worker_is_bounded_and_has_no_authority(tmp_path):
    worker = service(tmp_path)
    result = worker.dispatch(task(), completed_at="2026-08-01T18:00:01+00:00")
    assert result.state == RemoteTaskState.SUCCEEDED
    assert result.provider_id == "openworker"
    assert result.model_id == "deterministic-digest-v1"
    assert result.paper_only and not result.broker_submission
    assert not result.execution_authorized
    assert worker.store.result(task().remote_task_id) == result
    with pytest.raises(ValueError, match="duplicate"):
        worker.dispatch(task(), completed_at="2026-08-01T18:00:01+00:00")


def test_unknown_task_target_and_resource_limits_fail_closed(tmp_path):
    worker = service(tmp_path)
    with pytest.raises(ValueError, match="target"):
        worker.dispatch(
            replace(task(), node_id="titan-hermes"),
            completed_at="2026-08-01T18:00:01+00:00",
        )
    with pytest.raises(ValueError, match="allowlisted"):
        worker.dispatch(
            replace(task(), task_type=WorkerTaskType.DOCUMENT_NORMALIZATION),
            completed_at="2026-08-01T18:00:01+00:00",
        )
    with pytest.raises(ValueError, match="timeout"):
        worker.dispatch(
            replace(task(), timeout_ms=2000),
            completed_at="2026-08-01T18:00:01+00:00",
        )


def test_exact_cancellation_and_terminal_immutability_survive_restart(tmp_path):
    store = DurableRemoteTaskStore(tmp_path / "tasks.json")
    value = task()
    store.create(value)
    with pytest.raises(ValueError, match="cancellation"):
        store.cancel(value.remote_task_id, "wrong-token")
    assert (
        store.cancel(value.remote_task_id, value.cancellation_token_id)
        == RemoteTaskState.CANCELLATION_REQUESTED
    )
    recovered = DurableRemoteTaskStore(tmp_path / "tasks.json")
    assert (
        recovered.state(value.remote_task_id) == RemoteTaskState.CANCELLATION_REQUESTED
    )
    assert recovered.result(value.remote_task_id) is None


def test_running_deterministic_probe_acknowledges_exact_cancellation(tmp_path):
    worker = GovernedRemoteWorkerService(
        "prime-hermes",
        DurableRemoteTaskStore(tmp_path / "tasks.json"),
        {"deterministic_calculation": digest_evidence_handler},
        maximum_timeout_ms=3000,
        maximum_output_chars=2048,
    )
    value = replace(
        task(),
        task_type=WorkerTaskType.DETERMINISTIC_CALCULATION,
        timeout_ms=2000,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            worker.dispatch, value, completed_at="2026-08-01T18:00:01+00:00"
        )
        for _ in range(50):
            try:
                if (
                    worker.store.state(value.remote_task_id)
                    == RemoteTaskState.ACKNOWLEDGED
                ):
                    break
            except ValueError:
                sleep(0.01)
        with pytest.raises(ValueError, match="concurrency"):
            worker.dispatch(
                replace(
                    value,
                    remote_task_id="remote-task-concurrent-2",
                    cancellation_token_id="cancel-concurrent-2",
                ),
                completed_at="2026-08-01T18:00:01+00:00",
            )
        assert (
            worker.store.cancel(value.remote_task_id, value.cancellation_token_id)
            == RemoteTaskState.CANCELLATION_REQUESTED
        )
        result = future.result(timeout=3)
    assert result.state == RemoteTaskState.CANCELLED
    assert result.cancellation_state == "acknowledged"
    assert result.output_digest is None and result.failure_classification == "cancelled"
    assert worker.store.result(value.remote_task_id) == result


class ASGITransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        return self.client.request(method, url.split("testserver", 1)[-1], **kwargs)


class LostResultTransport(ASGITransport):
    def __init__(self, client):
        super().__init__(client)
        self.lose_once = True

    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        if self.lose_once and method == "POST" and url.endswith("/fleet/tasks"):
            self.lose_once = False
            raise httpx.ConnectError("result delivery lost")
        return response


class ResetTransport:
    def __init__(self, *_):
        pass

    def request(self, method, url, **kwargs):
        raise httpx.ReadError("connection reset")


def signed_adapter(tmp_path, monkeypatch, transport_type=ASGITransport):
    now = 1_786_000_000
    monkeypatch.setattr("time.time", lambda: now)
    tmp_path.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "credential.secret"
    secret.write_text("a" * 64)
    secret.chmod(0o600)
    item = SigningCredential(
        credential_id="credential-1",
        secret_reference=f"file:{secret}",
        coordinator_node_id="mac-hermes",
        target_node_id="prime-hermes",
        not_before=now - 60,
        expires_at=now + 600,
    )
    registry = CredentialRegistry(credentials=(item,))
    app = FastAPI()
    remote = service(tmp_path)
    authenticator = SignedRequestAuthenticator(
        registry,
        DurableReplayStore(tmp_path / "replay.jsonl"),
        target_node_id="prime-hermes",
    )
    attach_remote_worker_routes(app, remote, authenticator)
    client = TestClient(app)
    return SignedFleetHTTPAdapter(
        "http://testserver",
        registry,
        coordinator_node_id="mac-hermes",
        target_node_id="prime-hermes",
        transport=transport_type(client),
    )


def test_signed_remote_dispatch_and_completion_unknown_reconciliation(
    tmp_path, monkeypatch
):
    adapter = signed_adapter(tmp_path, monkeypatch, LostResultTransport)
    with pytest.raises(ConnectionError):
        adapter.dispatch(task())
    result = adapter.query(task().remote_task_id)
    assert result is not None and result.state == RemoteTaskState.SUCCEEDED
    assert result.paper_only and not result.broker_submission
    reset = signed_adapter(tmp_path / "reset", monkeypatch, ResetTransport)
    with pytest.raises(ConnectionError, match="unreachable"):
        reset.dispatch(task(remote_task_id="remote-task-reset-1"))


def test_signed_remote_cancellation_is_authenticated_and_reconciled(
    tmp_path, monkeypatch
):
    adapter = signed_adapter(tmp_path, monkeypatch)
    value = replace(
        task(
            remote_task_id="remote-task-cancel-1", cancellation_token_id="cancel-live-1"
        ),
        task_type=WorkerTaskType.DETERMINISTIC_CALCULATION,
        timeout_ms=1000,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(adapter.dispatch, value)
        sleep(0.2)
        assert (
            adapter.cancel(value.remote_task_id, value.cancellation_token_id)
            == RemoteTaskState.CANCELLATION_REQUESTED
        )
        result = future.result(timeout=3)
    assert result.state == RemoteTaskState.CANCELLED
    assert result.cancellation_state == "acknowledged"
    assert adapter.query(value.remote_task_id) == result
