import pytest

from hermes_cli.hermes_link.models import HermesLinkEnvelope, MessageType


@pytest.fixture
def envelope():
    return HermesLinkEnvelope(
        message_id="link-message-1",
        correlation_id="conversation-1",
        conversation_id="conversation-1",
        sender_node="mac-hermes",
        recipient_node="titan-hermes",
        message_type=MessageType.CHAT,
        created_at=100,
        payload={"text": "hello little sister"},
        evidence_references=("evidence://review/1",),
    )
