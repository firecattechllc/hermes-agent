"""Governed Titan-side processing for Step 32 Hermes-link envelopes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlparse

import httpx

from .models import (
    DeliveryState,
    HermesLinkEnvelope,
    MessageType,
    utc_now,
)
from .service import HermesLinkService, LinkPolicyError

LOGGER = logging.getLogger(__name__)
SUPPORTED_INBOUND_TYPES = frozenset({
    MessageType.CHAT,
    MessageType.TASK_REQUEST,
    MessageType.LESSON_PACKAGE,
})


class InferenceFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class InferenceResult:
    text: str
    evidence_references: tuple[str, ...] = ()


class LocalInferenceAdapter(Protocol):
    def infer(self, prompt: str) -> InferenceResult: ...


class OllamaChatAdapter:
    """Narrow, local-only Ollama chat adapter without tool authority."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 60.0,
        maximum_input_chars: int = 16_000,
        maximum_output_chars: int = 16_000,
        maximum_output_tokens: int = 2_048,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Ollama inference endpoint must be local loopback HTTP")
        if not model.strip():
            raise ValueError("local Ollama model must not be empty")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.maximum_input_chars = maximum_input_chars
        self.maximum_output_chars = maximum_output_chars
        self.maximum_output_tokens = maximum_output_tokens
        self.transport = transport

    def infer(self, prompt: str) -> InferenceResult:
        prompt = prompt.strip()
        if not prompt:
            raise InferenceFailure(
                "empty_chat_message", "chat message must not be empty", retryable=False
            )
        if len(prompt) > self.maximum_input_chars:
            raise InferenceFailure(
                "chat_input_too_large",
                "chat message exceeds the configured inference limit",
                retryable=False,
            )
        request = {
            "model": self.model,
            "stream": False,
            "think": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Titan Hermes. Return only the concise assistant-visible "
                        "answer. Do not reveal hidden reasoning. Do not invoke tools, shell, "
                        "credentials, spending, deployment, or privileged actions."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "options": {"num_predict": self.maximum_output_tokens},
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds),
                transport=self.transport,
                trust_env=False,
            ) as client:
                response = client.post(f"{self.base_url}/api/chat", json=request)
                response.raise_for_status()
                if len(response.content) > self.maximum_output_chars * 4 + 65_536:
                    raise InferenceFailure(
                        "model_output_too_large",
                        "local inference response exceeds the configured limit",
                        retryable=False,
                    )
                body = response.json()
        except InferenceFailure:
            raise
        except httpx.TimeoutException as exc:
            raise InferenceFailure(
                "model_timeout", "local inference timed out", retryable=True
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise InferenceFailure(
                "model_unavailable", "local inference failed", retryable=True
            ) from exc
        message = body.get("message") if isinstance(body, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise InferenceFailure(
                "invalid_model_response",
                "local inference returned no assistant-visible text",
                retryable=True,
            )
        return InferenceResult(text=text.strip()[: self.maximum_output_chars])


def response_message_id(message_id: str) -> str:
    digest = hashlib.sha256(f"titan-reply:{message_id}".encode()).hexdigest()[:32]
    return f"link-reply-{digest}"


class TitanMessageProcessor:
    def __init__(
        self,
        service: HermesLinkService,
        inference: LocalInferenceAdapter,
        *,
        claim_lease_seconds: int = 120,
        clock: Callable[[], int] = utc_now,
    ) -> None:
        self.service = service
        self.inference = inference
        self.claim_lease_seconds = claim_lease_seconds
        self.clock = clock

    def process_once(self) -> bool:
        claimed = self.service.store.claim_next(
            sender_node=self.service.peer_node,
            recipient_node=self.service.local_node,
            now=self.clock(),
            lease_seconds=self.claim_lease_seconds,
        )
        if claimed is None:
            return False
        reply_id = response_message_id(claimed.message_id)
        if self.service.store.get(reply_id) is not None:
            self.service.acknowledge(claimed.message_id)
            return True
        try:
            response = self._route(claimed)
            self.service.enqueue(response)
            self.service.acknowledge(claimed.message_id)
        except InferenceFailure as exc:
            if exc.retryable:
                failed = self.service.fail_delivery(
                    claimed.message_id, error_code=exc.code, now=self.clock()
                )
                if failed.delivery_state == DeliveryState.DEAD_LETTERED:
                    self._persist_failure_response(claimed, exc.code, retryable=False)
            else:
                self._persist_failure_response(claimed, exc.code, retryable=False)
                self.service.reject(claimed.message_id, error_code=exc.code)
        except LinkPolicyError as exc:
            self._persist_failure_response(claimed, exc.code, retryable=False)
            self.service.reject(claimed.message_id, error_code=exc.code)
        except Exception:
            LOGGER.error(
                "Titan message processing failed message_id=%s",
                claimed.message_id,
            )
            failed = self.service.fail_delivery(
                claimed.message_id,
                error_code="processor_runtime_failure",
                now=self.clock(),
            )
            if failed.delivery_state == DeliveryState.DEAD_LETTERED:
                self._persist_failure_response(
                    claimed, "processor_runtime_failure", retryable=False
                )
        return True

    def _route(self, envelope: HermesLinkEnvelope) -> HermesLinkEnvelope:
        if envelope.message_type not in SUPPORTED_INBOUND_TYPES:
            raise LinkPolicyError(
                "unsupported_message_type", "message type is not processable"
            )
        if envelope.message_type == MessageType.CHAT:
            text = envelope.payload.get("message", envelope.payload.get("text"))
            if not isinstance(text, str):
                raise LinkPolicyError(
                    "invalid_chat_schema", "chat payload requires a message string"
                )
            result = self.inference.infer(text)
            return self._response(
                envelope,
                message_type=MessageType.CHAT,
                payload={"message": result.text},
                evidence_references=result.evidence_references,
            )
        if envelope.message_type == MessageType.TASK_REQUEST:
            if not isinstance(envelope.payload.get("instructions"), str):
                raise LinkPolicyError(
                    "invalid_task_schema", "task payload requires instructions"
                )
            return self._response(
                envelope,
                message_type=MessageType.TASK_RESULT,
                payload={
                    "status": "accepted_for_governed_review",
                    "summary": (
                        "Task received. No privileged or automatic execution was started."
                    ),
                    "execution_started": False,
                },
            )
        if not isinstance(envelope.payload.get("instructions"), str):
            raise LinkPolicyError(
                "invalid_lesson_schema", "lesson payload requires instructions"
            )
        return self._response(
            envelope,
            message_type=MessageType.ACKNOWLEDGEMENT,
            payload={
                "status": "validated_receipt",
                "summary": (
                    "Lesson received for governed validation. No privileged work was started."
                ),
                "execution_started": False,
            },
        )

    def _response(
        self,
        request: HermesLinkEnvelope,
        *,
        message_type: MessageType,
        payload: dict[str, Any],
        evidence_references: tuple[str, ...] = (),
    ) -> HermesLinkEnvelope:
        return HermesLinkEnvelope(
            message_id=response_message_id(request.message_id),
            correlation_id=request.correlation_id,
            conversation_id=request.conversation_id,
            sender_node=self.service.local_node,
            recipient_node=self.service.peer_node,
            message_type=message_type,
            payload=payload,
            evidence_references=evidence_references,
        )

    def _persist_failure_response(
        self, request: HermesLinkEnvelope, code: str, *, retryable: bool
    ) -> None:
        self.service.enqueue(
            self._response(
                request,
                message_type=MessageType.ERROR,
                payload={
                    "message": "Titan could not complete the governed request.",
                    "failure": {"code": code, "retryable": retryable},
                },
            )
        )


class TitanConsumerLoop:
    def __init__(
        self, processor: TitanMessageProcessor, *, poll_interval_seconds: float = 1.0
    ) -> None:
        self.processor = processor
        self.poll_interval_seconds = poll_interval_seconds

    async def run(self) -> None:
        while True:
            processed = await asyncio.to_thread(self.processor.process_once)
            if not processed:
                await asyncio.sleep(self.poll_interval_seconds)
