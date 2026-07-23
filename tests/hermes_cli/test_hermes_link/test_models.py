import pytest
from pydantic import ValidationError

from hermes_cli.hermes_link.config import HermesLinkConfig
from hermes_cli.hermes_link.models import (
    ComponentHealth,
    HermesLinkEnvelope,
    HermesLinkStatus,
    MessageType,
    NodeRole,
    PresenceState,
)
from hermes_cli.mission_control.models import ApprovalState


def test_envelope_round_trip_is_strict_and_stable(envelope):
    restored = HermesLinkEnvelope.model_validate_json(envelope.model_dump_json())
    assert restored == envelope
    assert restored.schema_version == 1


@pytest.mark.parametrize(
    "change",
    [
        {"message_type": "remote_shell"},
        {"schema_version": 2},
        {"sender_node": "Titan Hermes"},
        {"recipient_node": "mac-hermes"},
    ],
)
def test_invalid_or_unsupported_envelope_rejected(envelope, change):
    with pytest.raises(ValidationError):
        HermesLinkEnvelope.model_validate({
            **envelope.model_dump(mode="json"),
            **change,
        })


@pytest.mark.parametrize(
    "payload",
    [
        {"shell": "rm something"},
        {"action": "sudo"},
        {"nested": {"token": "credential"}},
        {"action": "production_mutation"},
        {"operation": "deploy"},
        {"nested": {"sudo_command": "anything"}},
        {"deployment": {"target": "prod"}},
        {"publish": True},
        {"spend": 100},
        {"external_message": "send"},
        {"destructive_operation": "delete"},
    ],
)
def test_prohibited_actions_and_auth_material_rejected(envelope, payload):
    with pytest.raises(ValidationError, match="prohibited"):
        HermesLinkEnvelope.model_validate({
            **envelope.model_dump(mode="json"),
            "payload": payload,
        })


def test_approval_state_is_consistent(envelope):
    with pytest.raises(ValidationError, match="approval"):
        HermesLinkEnvelope.model_validate({
            **envelope.model_dump(mode="json"),
            "approval_required": True,
        })
    approved = HermesLinkEnvelope.model_validate({
        **envelope.model_dump(mode="json"),
        "approval_required": True,
        "approval_state": ApprovalState.APPROVED,
    })
    assert approved.approval_required


def test_status_does_not_fabricate_unconfigured_health():
    status = HermesLinkStatus(
        node_id="titan-hermes",
        node_role=NodeRole.LITTLE_SISTER,
        presence=PresenceState.ONLINE,
        service_version="1",
    )
    assert status.ollama_health == ComponentHealth.UNKNOWN
    assert status.finbert_health == ComponentHealth.UNKNOWN
    assert status.memory_index_health == ComponentHealth.UNKNOWN
    assert status.nursery_state == ComponentHealth.UNKNOWN


def test_enabled_config_requires_secret_reference_and_loopback():
    with pytest.raises(ValidationError, match="authentication"):
        HermesLinkConfig(enabled=True, authentication_token_reference=None)
    with pytest.raises(ValidationError, match="loopback"):
        HermesLinkConfig(bind_host="0.0.0.0")
    with pytest.raises(ValidationError, match="never inline"):
        HermesLinkConfig(authentication_token_reference="actual-token")
