import json

import pytest

from hermes_cli.hermes_link.models import (
    ComponentHealth,
    DeliveryState,
    MessageType,
    RetryMetadata,
)
from hermes_cli.mission_control.models import ApprovalState
from hermes_cli.hermes_link.service import HermesLinkService, LinkPolicyError
from hermes_cli.hermes_link.store import HermesLinkStore
from hermes_cli.hermes_link.visibility import HermesLinkVisibility
from hermes_cli.mission_control.service import MissionControlService
from hermes_cli.mission_control.store import MissionControlStore


def service(tmp_path, **kwargs):
    return HermesLinkService(
        HermesLinkStore(tmp_path / "link"),
        local_node="titan-hermes",
        peer_node="mac-hermes",
        **kwargs,
    )


def test_duplicate_delivery_is_idempotent_and_restart_persists(tmp_path, envelope):
    first = service(tmp_path).receive(envelope, allowed_types={MessageType.CHAT})
    second_service = service(tmp_path)
    second = second_service.receive(envelope, allowed_types={MessageType.CHAT})
    assert first == second
    assert first.delivery_state == DeliveryState.DELIVERED
    assert len(second_service.store.records()) == 1


def test_identity_collision_fails_closed(tmp_path, envelope):
    link = service(tmp_path)
    link.receive(envelope)
    changed = envelope.model_copy(update={"payload": {"text": "different"}})
    with pytest.raises(LinkPolicyError, match="different content"):
        link.receive(changed)


def test_wrong_identity_and_unsupported_type_are_rejected_and_audited(
    tmp_path, envelope
):
    link = service(tmp_path)
    wrong = envelope.model_copy(update={"sender_node": "other-mac"})
    with pytest.raises(LinkPolicyError) as exc:
        link.receive(wrong)
    assert exc.value.code == "invalid_node_identity"
    assert link.store.get(wrong.message_id).delivery_state == DeliveryState.REJECTED
    other = envelope.model_copy(
        update={
            "message_id": "link-message-2",
            "message_type": MessageType.TASK_REQUEST,
        }
    )
    with pytest.raises(LinkPolicyError) as exc:
        link.receive(other, allowed_types={MessageType.CHAT})
    assert exc.value.code == "unsupported_message_type"
    assert len(link.store.records()) == 2


def test_pending_approval_is_rejected(tmp_path, envelope):
    pending = envelope.model_copy(
        update={"approval_required": True, "approval_state": ApprovalState.PENDING}
    )
    with pytest.raises(LinkPolicyError) as exc:
        service(tmp_path).receive(pending)
    assert exc.value.code == "approval_required"


def test_queue_transitions_retry_backoff_and_dead_letter(tmp_path, envelope):
    outbound = envelope.model_copy(
        update={
            "sender_node": "titan-hermes",
            "recipient_node": "mac-hermes",
            "retry": RetryMetadata(maximum_attempts=2),
        }
    )
    link = service(tmp_path, maximum_retries=2)
    queued = link.enqueue(outbound)
    assert queued.delivery_state == DeliveryState.QUEUED
    retry = link.fail_delivery(outbound.message_id, error_code="offline", now=100)
    assert retry.delivery_state == DeliveryState.RETRYABLE
    assert retry.retry.next_attempt_at == 102
    dead = link.fail_delivery(outbound.message_id, error_code="offline", now=102)
    assert dead.delivery_state == DeliveryState.DEAD_LETTERED
    assert dead.retry.next_attempt_at is None
    assert service(tmp_path).store.get(outbound.message_id) == dead


def test_acknowledgement_transition(tmp_path, envelope):
    link = service(tmp_path)
    delivered = link.receive(envelope)
    acknowledged = link.acknowledge(delivered.message_id)
    assert acknowledged.delivery_state == DeliveryState.ACKNOWLEDGED
    with pytest.raises(ValueError, match="state transition"):
        link.store.append(acknowledged, state=DeliveryState.QUEUED)


def test_torn_tail_recovers_without_silent_loss(tmp_path, envelope):
    link = service(tmp_path)
    link.receive(envelope)
    with link.store.path.open("ab") as handle:
        handle.write(b'{"partial":')
    assert len(service(tmp_path).store.records()) == 1
    assert service(tmp_path).store.path.read_bytes().endswith(b"\n")


def test_checksum_corruption_fails_closed(tmp_path, envelope):
    link = service(tmp_path)
    link.receive(envelope)
    row = json.loads(link.store.path.read_text().strip())
    row["reason_code"] = "tampered"
    link.store.path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="corrupt"):
        link.store.records()


def test_payload_size_is_bounded_and_rejection_persisted(tmp_path, envelope):
    link = service(tmp_path, maximum_payload_bytes=256)
    oversized = envelope.model_copy(update={"payload": {"text": "x" * 500}})
    with pytest.raises(LinkPolicyError) as exc:
        link.receive(oversized)
    assert exc.value.code == "payload_too_large"
    assert link.store.get(oversized.message_id).delivery_state == DeliveryState.REJECTED


def test_status_health_and_unknown_values_are_honest(tmp_path):
    link = service(
        tmp_path,
        health_provider=lambda: {
            "ollama": ComponentHealth.HEALTHY,
            "finbert": ComponentHealth.UNAVAILABLE,
        },
    )
    status = link.status()
    assert status.ollama_health == ComponentHealth.HEALTHY
    assert status.finbert_health == ComponentHealth.UNAVAILABLE
    assert status.memory_index_health == ComponentHealth.UNKNOWN
    assert status.degraded_components == ("finbert",)


def test_mission_control_events_have_safe_metadata_only(tmp_path, envelope):
    mission = MissionControlService(MissionControlStore(tmp_path / "mission"))
    visibility = HermesLinkVisibility(mission)
    link = service(tmp_path, visibility=visibility)
    link.receive(envelope)
    events = mission.get_events("hermes-link")
    assert [event.event_type for event in events] == [
        "link_message_received",
        "link_message_delivered",
    ]
    assert all("text" not in json.dumps(event.payload) for event in events)
    assert all(event.correlation_id == envelope.correlation_id for event in events)
