"""Titan-side governed application service; no execution capability exists here."""

from __future__ import annotations

import time
from typing import Callable, Optional, Tuple

from hermes_cli.mission_control.models import ApprovalState

from .models import (
    ComponentHealth,
    DeliveryState,
    HermesLinkEnvelope,
    HermesLinkStatus,
    MessageType,
    NodeRole,
    PresenceState,
    QueueCounts,
    RetryMetadata,
    utc_now,
)
from .store import HermesLinkStore
from .visibility import HermesLinkVisibility


class LinkPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HermesLinkService:
    def __init__(
        self,
        store: HermesLinkStore,
        *,
        local_node: str,
        peer_node: str,
        node_role: NodeRole = NodeRole.LITTLE_SISTER,
        maximum_payload_bytes: int = 65536,
        maximum_retries: int = 3,
        visibility: Optional[HermesLinkVisibility] = None,
        health_provider: Optional[Callable[[], dict[str, ComponentHealth]]] = None,
        service_version: str = "1",
    ) -> None:
        self.store = store
        self.local_node = local_node
        self.peer_node = peer_node
        self.node_role = node_role
        self.maximum_payload_bytes = maximum_payload_bytes
        self.maximum_retries = maximum_retries
        self.visibility = visibility or HermesLinkVisibility(None)
        self.health_provider = health_provider
        self.service_version = service_version
        self.started_at = time.monotonic()
        self.last_sync_at: Optional[int] = None

    def receive(
        self,
        envelope: HermesLinkEnvelope,
        *,
        allowed_types: Optional[set[MessageType]] = None,
    ) -> HermesLinkEnvelope:
        existing = self.store.get(envelope.message_id)
        if existing is not None:
            if existing.model_dump(
                exclude={"delivery_state", "retry"}
            ) != envelope.model_dump(exclude={"delivery_state", "retry"}):
                raise LinkPolicyError(
                    "identity_collision",
                    "message id was already used for different content",
                )
            return existing
        reason = None
        try:
            if (
                envelope.sender_node != self.peer_node
                or envelope.recipient_node != self.local_node
            ):
                raise LinkPolicyError(
                    "invalid_node_identity",
                    "sender or recipient identity is not authorized",
                )
            if allowed_types is not None and envelope.message_type not in allowed_types:
                raise LinkPolicyError(
                    "unsupported_message_type",
                    "message type is not supported by this operation",
                )
            if envelope.serialized_size() > self.maximum_payload_bytes:
                raise LinkPolicyError(
                    "payload_too_large", "message exceeds configured payload size"
                )
            if (
                envelope.approval_required
                and envelope.approval_state != ApprovalState.APPROVED
            ):
                raise LinkPolicyError(
                    "approval_required",
                    "message requires an existing governed approval",
                )
            accepted = self.store.append(envelope, state=DeliveryState.DELIVERED)
            self.visibility.publish(accepted, received=True)
            self.visibility.publish(accepted)
            return accepted
        except LinkPolicyError as exc:
            reason = exc.code
            rejected = self.store.append(
                envelope, state=DeliveryState.REJECTED, reason_code=reason
            )
            self.visibility.publish(rejected, reason_code=reason)
            raise

    def enqueue(self, envelope: HermesLinkEnvelope) -> HermesLinkEnvelope:
        if (
            envelope.sender_node != self.local_node
            or envelope.recipient_node != self.peer_node
        ):
            raise LinkPolicyError(
                "invalid_node_identity", "outbound node identity is not authorized"
            )
        queued = self.store.append(envelope, state=DeliveryState.QUEUED)
        self.visibility.publish(queued)
        return queued

    def acknowledge(self, message_id: str) -> HermesLinkEnvelope:
        current = self.store.get(message_id)
        if current is None:
            raise LinkPolicyError("message_not_found", "message does not exist")
        acknowledged = self.store.append(current, state=DeliveryState.ACKNOWLEDGED)
        self.visibility.publish(acknowledged)
        return acknowledged

    def fail_delivery(
        self, message_id: str, *, error_code: str, now: Optional[int] = None
    ) -> HermesLinkEnvelope:
        current = self.store.get(message_id)
        if current is None:
            raise LinkPolicyError("message_not_found", "message does not exist")
        timestamp = now or utc_now()
        attempts = current.retry.attempt_count + 1
        terminal = attempts >= min(current.retry.maximum_attempts, self.maximum_retries)
        retry = RetryMetadata(
            attempt_count=attempts,
            maximum_attempts=min(current.retry.maximum_attempts, self.maximum_retries),
            last_attempt_at=timestamp,
            next_attempt_at=None if terminal else timestamp + min(300, 2**attempts),
            last_error_code=error_code,
        )
        state = DeliveryState.DEAD_LETTERED if terminal else DeliveryState.RETRYABLE
        failed = self.store.append(
            current.model_copy(update={"retry": retry}),
            state=state,
            reason_code=error_code,
            recorded_at=timestamp,
        )
        self.visibility.publish(failed, reason_code=error_code)
        return failed

    def list_queue(self) -> Tuple[HermesLinkEnvelope, ...]:
        return self.store.list()

    def latest_report(self) -> Optional[HermesLinkEnvelope]:
        reports = [
            item
            for item in self.store.list()
            if item.message_type
            in {MessageType.TASK_RESULT, MessageType.STATUS, MessageType.ERROR}
            and item.sender_node == self.local_node
        ]
        return reports[-1] if reports else None

    def status(self) -> HermesLinkStatus:
        counts = {state.value: 0 for state in DeliveryState}
        items = self.store.list()
        for item in items:
            counts[item.delivery_state.value] += 1
        health = self.health_provider() if self.health_provider is not None else {}
        components = {
            name: health.get(name, ComponentHealth.UNKNOWN)
            for name in ("nursery", "ollama", "finbert", "memory_index")
        }
        degraded = tuple(
            sorted(
                name
                for name, state in components.items()
                if state in {ComponentHealth.DEGRADED, ComponentHealth.UNAVAILABLE}
            )
        )
        return HermesLinkStatus(
            node_id=self.local_node,
            node_role=self.node_role,
            presence=PresenceState.ONLINE,
            service_version=self.service_version,
            uptime_seconds=int(time.monotonic() - self.started_at),
            queue_counts=QueueCounts(**counts),
            nursery_state=components["nursery"],
            ollama_health=components["ollama"],
            finbert_health=components["finbert"],
            memory_index_health=components["memory_index"],
            last_synchronization_at=self.last_sync_at,
            pending_escalations=sum(
                item.message_type == MessageType.ESCALATION
                and item.delivery_state != DeliveryState.ACKNOWLEDGED
                for item in items
            ),
            degraded_components=degraded,
        )
