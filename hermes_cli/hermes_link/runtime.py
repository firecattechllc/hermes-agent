"""Environment-configured Titan Hermes-link API and consumer runtime."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .api import create_app, static_token_verifier
from .processor import OllamaChatAdapter, TitanConsumerLoop, TitanMessageProcessor
from .service import HermesLinkService
from .store import HermesLinkStore


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def build_app():
    token = os.environ.get("HERMES_LINK_TOKEN", "").strip()
    if not token:
        raise ValueError("HERMES_LINK_TOKEN is not configured")
    queue_path = Path(
        os.environ.get(
            "HERMES_LINK_QUEUE_PATH",
            str(Path.home() / ".hermes" / "link-service" / "queue"),
        )
    )
    maximum_retries = _bounded_int("HERMES_LINK_MAXIMUM_RETRIES", 3, 1, 20)
    service = HermesLinkService(
        HermesLinkStore(queue_path),
        local_node="titan-hermes",
        peer_node="mac-hermes",
        maximum_payload_bytes=_bounded_int(
            "HERMES_LINK_MAXIMUM_PAYLOAD_BYTES", 65_536, 1_024, 1_048_576
        ),
        maximum_retries=maximum_retries,
        service_version="34",
    )
    adapter = OllamaChatAdapter(
        model=os.environ.get("HERMES_LINK_OLLAMA_MODEL", "qwen3:8b"),
        base_url=os.environ.get("HERMES_LINK_OLLAMA_URL", "http://127.0.0.1:11434"),
        timeout_seconds=_bounded_int("HERMES_LINK_MODEL_TIMEOUT_SECONDS", 60, 1, 600),
        maximum_input_chars=_bounded_int(
            "HERMES_LINK_MAXIMUM_INPUT_CHARS", 16_000, 256, 65_536
        ),
        maximum_output_chars=_bounded_int(
            "HERMES_LINK_MAXIMUM_OUTPUT_CHARS", 16_000, 256, 65_536
        ),
        maximum_output_tokens=_bounded_int(
            "HERMES_LINK_MAXIMUM_OUTPUT_TOKENS", 2_048, 64, 16_384
        ),
    )
    processor = TitanMessageProcessor(
        service,
        adapter,
        claim_lease_seconds=_bounded_int(
            "HERMES_LINK_CLAIM_LEASE_SECONDS", 120, 5, 3_600
        ),
    )
    consumer = TitanConsumerLoop(
        processor,
        poll_interval_seconds=_bounded_int(
            "HERMES_LINK_POLL_INTERVAL_SECONDS", 1, 1, 60
        ),
    )
    return create_app(
        service,
        token_verifier=static_token_verifier(token),
        consumer=consumer,
    )


def main() -> None:
    uvicorn.run(
        build_app(),
        host="127.0.0.1",
        port=_bounded_int("HERMES_LINK_PORT", 9_320, 1_024, 65_535),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
