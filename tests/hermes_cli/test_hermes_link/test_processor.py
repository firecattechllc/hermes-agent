from concurrent.futures import ThreadPoolExecutor

import httpx

from hermes_cli.hermes_link.models import (
    DeliveryState,
    HermesLinkEnvelope,
    MessageType,
    RetryMetadata,
)
from hermes_cli.hermes_link.processor import (
    InferenceFailure,
    InferenceResult,
    OllamaChatAdapter,
    TitanMessageProcessor,
    response_message_id,
)
from hermes_cli.hermes_link.service import HermesLinkService
from hermes_cli.hermes_link.store import HermesLinkStore


class FakeInference:
    def __init__(self, result="Titan reply", failure=None):
        self.result = result
        self.failure = failure
        self.calls = 0

    def infer(self, prompt):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return InferenceResult(self.result)


def link(tmp_path, inference=None, *, now=100):
    service = HermesLinkService(
        HermesLinkStore(tmp_path / "link"),
        local_node="titan-hermes",
        peer_node="mac-hermes",
        maximum_retries=3,
    )
    adapter = inference or FakeInference()
    return (
        service,
        adapter,
        TitanMessageProcessor(
            service, adapter, claim_lease_seconds=10, clock=lambda: now
        ),
    )


def delivered(service, envelope):
    return service.receive(envelope, allowed_types={envelope.message_type})


def replies(service):
    return [
        item
        for item in service.list_queue()
        if item.sender_node == "titan-hermes" and item.recipient_node == "mac-hermes"
    ]


def test_chat_produces_one_correlated_reply_then_acknowledges(tmp_path, envelope):
    service, inference, processor = link(tmp_path)
    delivered(service, envelope)

    assert processor.process_once()
    response = replies(service)
    assert len(response) == 1
    assert response[0].payload == {"message": "Titan reply"}
    assert response[0].correlation_id == envelope.correlation_id
    assert response[0].conversation_id == envelope.conversation_id
    assert (
        service.store.get(envelope.message_id).delivery_state
        == DeliveryState.ACKNOWLEDGED
    )
    assert inference.calls == 1

    assert not processor.process_once()
    assert len(replies(service)) == 1
    assert inference.calls == 1


def test_reply_is_durable_before_acknowledgement(tmp_path, envelope, monkeypatch):
    service, _, processor = link(tmp_path)
    delivered(service, envelope)
    original_ack = service.acknowledge

    def assert_reply_first(message_id):
        assert service.store.get(response_message_id(message_id)) is not None
        return original_ack(message_id)

    monkeypatch.setattr(service, "acknowledge", assert_reply_first)
    processor.process_once()


def test_atomic_concurrent_claims_only_infer_once(tmp_path, envelope):
    service, inference, processor = link(tmp_path)
    delivered(service, envelope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: processor.process_once(), range(2)))
    assert results.count(True) == 1
    assert inference.calls == 1
    assert len(replies(service)) == 1


def test_restart_after_claim_reconciles_existing_reply_without_inference(
    tmp_path, envelope
):
    service, _, _ = link(tmp_path, now=100)
    delivered(service, envelope)
    claimed = service.store.claim_next(
        sender_node="mac-hermes",
        recipient_node="titan-hermes",
        now=100,
        lease_seconds=10,
    )
    assert claimed is not None
    service.enqueue(
        envelope.model_copy(
            update={
                "message_id": response_message_id(envelope.message_id),
                "sender_node": "titan-hermes",
                "recipient_node": "mac-hermes",
                "payload": {"message": "already durable"},
            }
        )
    )
    inference = FakeInference()
    restarted = TitanMessageProcessor(
        service, inference, claim_lease_seconds=10, clock=lambda: 111
    )
    assert restarted.process_once()
    assert inference.calls == 0
    assert (
        service.store.get(envelope.message_id).delivery_state
        == DeliveryState.ACKNOWLEDGED
    )
    assert len(replies(service)) == 1


def test_titan_originated_envelope_is_never_claimed(tmp_path, envelope):
    service, inference, processor = link(tmp_path)
    service.enqueue(
        envelope.model_copy(
            update={
                "sender_node": "titan-hermes",
                "recipient_node": "mac-hermes",
            }
        )
    )
    assert not processor.process_once()
    assert inference.calls == 0


def test_timeout_retries_then_dead_letters_with_failure_reply(tmp_path, envelope):
    failure = InferenceFailure("model_timeout", "timed out", retryable=True)
    service, inference, processor = link(
        tmp_path, FakeInference(failure=failure), now=100
    )
    delivered(
        service,
        envelope.model_copy(update={"retry": RetryMetadata(maximum_attempts=2)}),
    )
    processor.process_once()
    first = service.store.get(envelope.message_id)
    assert first.delivery_state == DeliveryState.RETRYABLE
    assert first.retry.next_attempt_at == 102
    assert not processor.process_once()

    processor.clock = lambda: 102
    processor.process_once()
    terminal = service.store.get(envelope.message_id)
    assert terminal.delivery_state == DeliveryState.DEAD_LETTERED
    assert terminal.retry.attempt_count == 2
    assert replies(service)[0].payload["failure"]["code"] == "model_timeout"
    assert inference.calls == 2


def test_permanent_schema_failure_is_rejected_with_reason(tmp_path, envelope):
    service, inference, processor = link(tmp_path)
    delivered(service, envelope.model_copy(update={"payload": {"unexpected": "value"}}))
    processor.process_once()
    rejected = service.store.get(envelope.message_id)
    assert rejected.delivery_state == DeliveryState.REJECTED
    assert rejected.retry.attempt_count == 0
    error = replies(service)[0]
    assert error.message_type == MessageType.ERROR
    assert error.payload["failure"] == {
        "code": "invalid_chat_schema",
        "retryable": False,
    }
    assert inference.calls == 0


def test_task_and_lesson_routes_do_not_invoke_inference(tmp_path, envelope):
    service, inference, processor = link(tmp_path)
    task = envelope.model_copy(
        update={
            "message_id": "task-inbound",
            "message_type": MessageType.TASK_REQUEST,
            "payload": {"instructions": "Review bounded work"},
        }
    )
    lesson = envelope.model_copy(
        update={
            "message_id": "lesson-inbound",
            "message_type": MessageType.LESSON_PACKAGE,
            "payload": {"instructions": "Learn bounded validation"},
        }
    )
    delivered(service, task)
    delivered(service, lesson)
    processor.process_once()
    processor.process_once()
    assert inference.calls == 0
    assert {item.message_type for item in replies(service)} == {
        MessageType.TASK_RESULT,
        MessageType.ACKNOWLEDGEMENT,
    }
    assert all(item.payload["execution_started"] is False for item in replies(service))


def test_ollama_adapter_bounds_output_and_disables_thinking_and_tools():
    captured = {}

    def handler(request):
        captured.update(request.read() and __import__("json").loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": "visible response is bounded"}}
        )

    adapter = OllamaChatAdapter(
        model="local-test",
        maximum_output_chars=8,
        transport=httpx.MockTransport(handler),
    )
    assert adapter.infer("hello").text == "visible "
    assert captured["think"] is False
    assert "tools" not in captured
    assert captured["stream"] is False


def test_runtime_failure_never_logs_message_or_exception_secrets(
    tmp_path, envelope, caplog
):
    class UnsafeFailure:
        def infer(self, prompt):
            raise RuntimeError(f"secret-value {prompt}")

    service, _, processor = link(tmp_path, UnsafeFailure())
    delivered(
        service,
        envelope.model_copy(update={"payload": {"message": "private-message"}}),
    )
    processor.process_once()

    rendered = caplog.text
    assert "secret-value" not in rendered
    assert "private-message" not in rendered
    assert envelope.message_id in rendered
