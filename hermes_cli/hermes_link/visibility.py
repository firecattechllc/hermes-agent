"""Safe Mission Control projection for link lifecycle events."""

from typing import Optional

from hermes_cli.mission_control.models import TelemetryEvent
from hermes_cli.mission_control.service import MissionControlService

from .models import DeliveryState, HermesLinkEnvelope

EVENT_FOR_STATE = {
    DeliveryState.QUEUED: "link_message_queued",
    DeliveryState.DELIVERED: "link_message_delivered",
    DeliveryState.ACKNOWLEDGED: "link_message_acknowledged",
    DeliveryState.REJECTED: "link_message_rejected",
    DeliveryState.FAILED: "link_message_failed",
    DeliveryState.RETRYABLE: "link_message_failed",
    DeliveryState.DEAD_LETTERED: "link_message_failed",
}


class HermesLinkVisibility:
    def __init__(
        self,
        mission_control: Optional[MissionControlService],
        *,
        project_id: str = "hermes-link",
    ) -> None:
        self._mission_control = mission_control
        self._project_id = project_id

    def publish(
        self,
        envelope: HermesLinkEnvelope,
        *,
        reason_code: Optional[str] = None,
        received: bool = False,
    ) -> None:
        if self._mission_control is None:
            return
        event_type = (
            "link_message_received"
            if received
            else EVENT_FOR_STATE[envelope.delivery_state]
        )
        event_id = f"{event_type}:{envelope.message_id}:{envelope.retry.attempt_count}"
        self._mission_control.append_event_once(
            TelemetryEvent(
                event_id=event_id,
                event_type=event_type,
                project_id=self._project_id,
                timestamp=envelope.created_at,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.message_id,
                severity="warning"
                if envelope.delivery_state
                in {
                    DeliveryState.REJECTED,
                    DeliveryState.FAILED,
                    DeliveryState.DEAD_LETTERED,
                }
                else "info",
                payload={
                    "source": "hermes_link",
                    "source_idempotency_key": event_id,
                    "message_id": envelope.message_id,
                    "message_type": envelope.message_type.value,
                    "sender_node": envelope.sender_node,
                    "recipient_node": envelope.recipient_node,
                    "delivery_state": envelope.delivery_state.value,
                    "reason_code": reason_code,
                },
            )
        )
