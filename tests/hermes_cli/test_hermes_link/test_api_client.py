import httpx
from fastapi.testclient import TestClient

from hermes_cli.hermes_link.api import create_app, static_token_verifier
from hermes_cli.hermes_link.client import HermesLinkClient
from hermes_cli.hermes_link.models import DeliveryState, MessageType
from hermes_cli.hermes_link.service import HermesLinkService
from hermes_cli.hermes_link.store import HermesLinkStore


def app_client(tmp_path, *, maximum_payload_bytes=65536):
    service = HermesLinkService(
        HermesLinkStore(tmp_path),
        local_node="titan-hermes",
        peer_node="mac-hermes",
        maximum_payload_bytes=maximum_payload_bytes,
    )
    return service, TestClient(
        create_app(service, token_verifier=static_token_verifier("test-token"))
    )


def auth():
    return {"Authorization": "Bearer test-token"}


def test_endpoint_authentication_failure(tmp_path):
    _, client = app_client(tmp_path)
    response = client.get("/status")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authentication_failed"


def test_status_queue_and_chat_endpoints(tmp_path, envelope):
    _, client = app_client(tmp_path)
    status = client.get("/status", headers=auth())
    assert status.status_code == 200
    assert status.json()["node_id"] == "titan-hermes"
    accepted = client.post(
        "/chat", headers=auth(), json=envelope.model_dump(mode="json")
    )
    assert accepted.status_code == 200
    assert accepted.json()["delivery_state"] == "delivered"
    queue = client.get("/queue", headers=auth())
    assert [item["message_id"] for item in queue.json()["messages"]] == [
        envelope.message_id
    ]


def test_endpoint_type_validation_and_clear_error(tmp_path, envelope):
    _, client = app_client(tmp_path)
    task = envelope.model_copy(update={"message_type": MessageType.TASK_REQUEST})
    response = client.post("/chat", headers=auth(), json=task.model_dump(mode="json"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_message_type"


def test_endpoint_request_size_limit(tmp_path, envelope):
    _, client = app_client(tmp_path, maximum_payload_bytes=256)
    response = client.post(
        "/chat",
        headers=auth(),
        json={**envelope.model_dump(mode="json"), "payload": {"text": "x" * 1000}},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"


def test_task_lesson_and_latest_report_routes(tmp_path, envelope):
    service, client = app_client(tmp_path)
    task = envelope.model_copy(
        update={"message_id": "task-1", "message_type": MessageType.TASK_REQUEST}
    )
    lesson = envelope.model_copy(
        update={"message_id": "lesson-1", "message_type": MessageType.LESSON_PACKAGE}
    )
    assert (
        client.post(
            "/task", headers=auth(), json=task.model_dump(mode="json")
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/lesson", headers=auth(), json=lesson.model_dump(mode="json")
        ).status_code
        == 200
    )
    assert client.get("/reports/latest", headers=auth()).status_code == 404
    report = envelope.model_copy(
        update={
            "message_id": "report-1",
            "sender_node": "titan-hermes",
            "recipient_node": "mac-hermes",
            "message_type": MessageType.TASK_RESULT,
        }
    )
    service.enqueue(report)
    assert (
        client.get("/reports/latest", headers=auth()).json()["message_id"] == "report-1"
    )


class ASGITransport:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        path = url.split("testserver", 1)[-1]
        return self.client.request(method, path, **kwargs)


def test_typed_client_success_and_error(tmp_path, envelope):
    _, api = app_client(tmp_path)
    client = HermesLinkClient(
        "http://testserver", token="test-token", transport=ASGITransport(api)
    )
    assert client.fetch_status().status.node_id == "titan-hermes"
    assert client.send_chat(envelope).envelope.delivery_state == DeliveryState.DELIVERED
    wrong = envelope.model_copy(
        update={"message_id": "wrong-1", "sender_node": "unknown-node"}
    )
    result = client.send_chat(wrong)
    assert not result.ok
    assert result.error.code == "invalid_node_identity"


class OfflineTransport:
    def request(self, method, url, **kwargs):
        raise httpx.ConnectError("offline")


def test_typed_client_offline_returns_structured_retryable_error():
    result = HermesLinkClient(
        "http://titan.invalid", token="test", transport=OfflineTransport()
    ).fetch_status()
    assert not result.ok
    assert result.error.code == "titan_unreachable"
    assert result.error.retryable


class InvalidTransport:
    def request(self, method, url, **kwargs):
        class Bad:
            status_code = 200

            def json(self):
                return {"unexpected": True}

        return Bad()


def test_typed_client_invalid_response_does_not_crash():
    result = HermesLinkClient(
        "http://titan.invalid", token="test", transport=InvalidTransport()
    ).fetch_status()
    assert not result.ok
    assert result.error.code == "invalid_response"
