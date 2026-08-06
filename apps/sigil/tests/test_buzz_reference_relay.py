from __future__ import annotations

import json
from dataclasses import asdict
from http.client import HTTPConnection

import pytest

from sigil.buzz_reference_relay import (
    BuzzReferenceRelayConfig,
    BuzzReferenceRelayError,
    BuzzReferenceRelayServer,
)
from sigil.buzz_relay_adapter import (
    BuzzActorKind,
    BuzzActorRef,
    BuzzEventKind,
    BuzzRelayEvent,
    BuzzSpaceRef,
    BuzzThreadRef,
    sign_event_payload,
)
from sigil.ai.registry import canonical_digest

SIGNING_KEY = b"r" * 32
ZERO_DIGEST = "sha256:" + "0" * 64


def _space() -> BuzzSpaceRef:
    return BuzzSpaceRef(
        workspace_id="ws-1",
        project_id="proj-1",
        channel_id="chan-1",
        workspace_name="Workspace",
        project_name="Project",
        channel_name="Channel",
    )


def _actor() -> BuzzActorRef:
    return BuzzActorRef(
        actor_id="agent-1",
        display_name="Agent One",
        kind=BuzzActorKind.AGENT,
        organization_identity="org-1",
    )


def _thread() -> BuzzThreadRef:
    return BuzzThreadRef(message_id="msg-1", thread_id=None, parent_message_id=None)


def _signed_event(sequence: int = 0, event_id: str = "event-1") -> BuzzRelayEvent:
    payload = {"summary": "reference relay smoke event"}
    base = BuzzRelayEvent(
        event_id=event_id,
        sequence=sequence,
        emitted_at="2026-08-05T23:00:00Z",
        kind=BuzzEventKind.MESSAGE,
        space=_space(),
        actor=_actor(),
        thread=_thread(),
        correlation_id="corr-1",
        idempotency_key=f"idem-{event_id}",
        payload=payload,
        payload_digest=f"sha256:{canonical_digest(payload)}",
        previous_event_digest=ZERO_DIGEST,
        signature="ed25519:" + "A" * 64,  # placeholder shape; signature computed below
    )
    signature = sign_event_payload(base, SIGNING_KEY)
    from dataclasses import replace

    return replace(base, signature=signature, event_digest="")


def _post_event(port: int, event: BuzzRelayEvent) -> tuple[int, dict]:
    body = json.dumps(
        {**asdict(event), "kind": event.kind.value, "space": asdict(event.space), "actor": asdict(event.actor), "thread": asdict(event.thread)}
    ).encode("utf-8")
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/events", body=body, headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    result = json.loads(response.read())
    status = response.status
    conn.close()
    return status, result


def _get_events(port: int) -> dict:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/events")
    response = conn.getresponse()
    result = json.loads(response.read())
    conn.close()
    return result


@pytest.fixture()
def relay():
    config = BuzzReferenceRelayConfig(enabled=True, signing_key=SIGNING_KEY)
    server = BuzzReferenceRelayServer(config)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_config_rejects_non_loopback_bind_host() -> None:
    with pytest.raises(BuzzReferenceRelayError, match="loopback"):
        BuzzReferenceRelayConfig(enabled=True, bind_host="0.0.0.0", signing_key=SIGNING_KEY)


def test_config_rejects_short_signing_key() -> None:
    with pytest.raises(BuzzReferenceRelayError, match="signing_key"):
        BuzzReferenceRelayConfig(enabled=True, signing_key=b"too-short")


def test_disabled_config_cannot_start_a_server() -> None:
    with pytest.raises(BuzzReferenceRelayError, match="disabled"):
        BuzzReferenceRelayServer(BuzzReferenceRelayConfig(enabled=False, signing_key=SIGNING_KEY))


def test_real_signed_event_is_accepted_over_real_http(relay) -> None:
    event = _signed_event()

    status, result = _post_event(relay.port, event)

    assert status == 200
    assert result["accepted"] is True
    assert relay.accepted_event_count == 1


def test_forged_signature_is_rejected_over_real_http(relay) -> None:
    from dataclasses import replace

    event = _signed_event()
    forged = replace(event, signature=sign_event_payload(event, b"wrong-key-wrong-key-wrong-key!!!"), event_digest="")

    status, result = _post_event(relay.port, forged)

    assert status == 409
    assert result["accepted"] is False
    assert relay.rejected_count == 1
    assert relay.accepted_event_count == 0


def test_malformed_payload_is_rejected_not_crashed(relay) -> None:
    conn = HTTPConnection("127.0.0.1", relay.port, timeout=5)
    conn.request("POST", "/events", body=b"not json", headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    body = json.loads(response.read())
    conn.close()

    assert response.status == 400
    assert "error" in body


def test_accepted_events_are_retrievable_over_real_http(relay) -> None:
    event = _signed_event()
    _post_event(relay.port, event)

    result = _get_events(relay.port)

    assert result["events"][0]["event_id"] == "event-1"
    assert result["rejected_count"] == 0


def test_duplicate_event_is_rejected_via_the_real_replay_window(relay) -> None:
    event = _signed_event()
    _post_event(relay.port, event)
    status, result = _post_event(relay.port, event)

    assert status == 409
    assert result["state"] == "duplicate"
