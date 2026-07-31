"""Governed real-time Alpaca news stream worker."""

from __future__ import annotations

import json
import os
import random
import signal
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Protocol, Self

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .governed_news_alpaca import (
    ALPACA_KEY_ALIASES,
    ALPACA_KEY_ENV,
    ALPACA_SECRET_ALIASES,
    ALPACA_SECRET_ENV,
    AlpacaNewsProvider,
)
from .governed_news_store import NewsEvidenceStore

ALPACA_NEWS_STREAM_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
STREAM_STATE_NAME = "governed-news-stream-state.json"
STREAM_AUDIT_NAME = "governed-news-stream-audit.jsonl"

INITIAL_RECONNECT_SECONDS = 1.0
MAX_RECONNECT_SECONDS = 30.0
PING_INTERVAL_SECONDS = 20.0
PING_TIMEOUT_SECONDS = 20.0
OPEN_TIMEOUT_SECONDS = 10.0
MAX_MESSAGE_BYTES = 4 * 1024 * 1024

TRUE_VALUES = {"1", "true", "yes", "on"}


class StreamConnection(Protocol):
    def send(self, message: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: object,
        exception: object,
        traceback: object,
    ) -> object: ...


ConnectStream = Callable[..., StreamConnection]
Sleep = Callable[[float], None]
Clock = Callable[[], datetime]
RandomUnit = Callable[[], float]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _configured_value(names: Iterable[str]) -> str:
    return next(
        (os.environ.get(name, "").strip() for name in names if os.environ.get(name, "").strip()),
        "",
    )


def stream_credentials() -> tuple[str, str]:
    key = _configured_value((ALPACA_KEY_ENV, *ALPACA_KEY_ALIASES))
    secret = _configured_value((ALPACA_SECRET_ENV, *ALPACA_SECRET_ALIASES))

    if not key or not secret:
        raise RuntimeError(
            f"{ALPACA_KEY_ENV} and {ALPACA_SECRET_ENV} must both be "
            "configured; supported aliases include ALPACA_API_KEY "
            "and ALPACA_SECRET_KEY"
        )

    return key, secret


def stream_enabled() -> bool:
    configured = (
        os.environ.get(
            "SIGIL_ALPACA_NEWS_STREAM_ENABLED",
            "",
        )
        .strip()
        .lower()
    )

    return configured in TRUE_VALUES


def _safe_error(error: BaseException) -> dict[str, str]:
    message = str(error).strip() or error.__class__.__name__

    return {
        "error_type": error.__class__.__name__,
        "message": message[:500],
    }


class NewsStreamState:
    """Persist observable stream state without trading authority."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / STREAM_STATE_NAME
        self.audit_path = directory / STREAM_AUDIT_NAME

    @staticmethod
    def initial() -> dict[str, Any]:
        return {
            "state": "stopped",
            "started_at": None,
            "last_connected_at": None,
            "last_authenticated_at": None,
            "last_subscribed_at": None,
            "last_message_at": None,
            "last_article_at": None,
            "stopped_at": None,
            "articles_received": 0,
            "articles_stored": 0,
            "duplicate_count": 0,
            "rejected_count": 0,
            "reconnect_count": 0,
            "last_error": None,
            "advisory_only": True,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

    def load(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise RuntimeError("news stream state cannot be a symlink")

        if not self.path.exists():
            return self.initial()

        payload = json.loads(self.path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            raise TypeError("news stream state must be an object")

        return {**self.initial(), **payload}

    def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            **self.initial(),
            **dict(state),
            "advisory_only": True,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

        if self.directory.is_symlink():
            raise RuntimeError("news stream directory cannot be a symlink")

        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

        return payload

    def transition(
        self,
        current_state: Mapping[str, Any],
        *,
        event: str,
        now: datetime,
        **changes: Any,
    ) -> dict[str, Any]:
        updated = self.save({**dict(current_state), **changes})

        audit = {
            "event": event,
            "observed_at": _timestamp(now),
            "state": updated["state"],
            "advisory_only": True,
            "execution_authority": False,
            "broker_submission_attempted": False,
            "paper_only": True,
        }

        with self.audit_path.open("a", encoding="utf-8") as output:
            os.chmod(self.audit_path, 0o600)
            output.write(json.dumps(audit, sort_keys=True))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())

        return updated


def _decode_messages(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    payload = json.loads(raw)

    messages = payload if isinstance(payload, list) else [payload]

    if not all(isinstance(item, dict) for item in messages):
        raise TypeError("Alpaca stream message must contain objects")

    return [dict(item) for item in messages]


def _control_message(
    messages: Iterable[Mapping[str, Any]],
    *,
    expected_type: str,
    expected_message: str | None = None,
) -> bool:
    for message in messages:
        if str(message.get("T", "")).strip() != expected_type:
            continue

        if expected_message is None:
            return True

        if str(message.get("msg", "")).strip() == expected_message:
            return True

    return False


def _subscription_confirmed(
    messages: Iterable[Mapping[str, Any]],
) -> bool:
    for message in messages:
        if message.get("T") != "subscription":
            continue

        news = message.get("news")

        if isinstance(news, list) and "*" in news:
            return True

    return False


def _news_messages(
    messages: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [dict(message) for message in messages if str(message.get("T", "")).strip() == "n"]


def _reconnect_delay(
    reconnect_count: int,
    *,
    random_unit: RandomUnit,
) -> float:
    bounded_count = max(0, reconnect_count - 1)
    base = min(
        MAX_RECONNECT_SECONDS,
        INITIAL_RECONNECT_SECONDS * (2**bounded_count),
    )
    jitter = min(1.0, base * 0.1) * random_unit()

    return min(MAX_RECONNECT_SECONDS, base + jitter)


class GovernedNewsStreamWorker:
    """Own one durable Alpaca news stream and ingest advisory evidence."""

    def __init__(
        self,
        *,
        state_directory: Path,
        stop_event: Event | None = None,
        connect_stream: ConnectStream = connect,
        sleep: Sleep = time.sleep,
        clock: Clock = _utc_now,
        random_unit: RandomUnit = random.random,
    ) -> None:
        self.state_directory = state_directory
        self.stop_event = stop_event or Event()
        self.connect_stream = connect_stream
        self.sleep = sleep
        self.clock = clock
        self.random_unit = random_unit
        self.state_store = NewsStreamState(state_directory)
        self.news_store = NewsEvidenceStore(state_directory)

    def _receive_control(
        self,
        websocket: StreamConnection,
        *,
        expected_type: str,
        expected_message: str | None = None,
    ) -> list[dict[str, Any]]:
        messages = _decode_messages(websocket.recv(timeout=10))

        for message in messages:
            if message.get("T") == "error":
                code = message.get("code")
                detail = str(message.get("msg", "stream error")).strip()
                raise RuntimeError(f"Alpaca stream error {code}: {detail}")

        if not _control_message(
            messages,
            expected_type=expected_type,
            expected_message=expected_message,
        ):
            raise RuntimeError(f"expected Alpaca stream control message: {expected_type}")

        return messages

    def _authenticate(
        self,
        websocket: StreamConnection,
        *,
        key: str,
        secret: str,
    ) -> None:
        websocket.send(
            json.dumps(
                {
                    "action": "auth",
                    "key": key,
                    "secret": secret,
                }
            )
        )
        self._receive_control(
            websocket,
            expected_type="success",
            expected_message="authenticated",
        )

    def _subscribe(self, websocket: StreamConnection) -> None:
        websocket.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "news": ["*"],
                }
            )
        )
        messages = _decode_messages(websocket.recv(timeout=10))

        for message in messages:
            if message.get("T") == "error":
                code = message.get("code")
                detail = str(message.get("msg", "stream error")).strip()
                raise RuntimeError(f"Alpaca stream error {code}: {detail}")

        if not _subscription_confirmed(messages):
            raise RuntimeError("Alpaca wildcard news subscription was not confirmed")

    def _ingest_article(
        self,
        article: Mapping[str, Any],
        *,
        received_at: datetime,
    ) -> str:
        mapped = AlpacaNewsProvider._map_article(dict(article))
        result = self.news_store.ingest(
            mapped,
            received_at=_timestamp(received_at),
        )

        return str(result["status"])

    def _consume(
        self,
        websocket: StreamConnection,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        while not self.stop_event.is_set():
            try:
                raw = websocket.recv(timeout=1)
            except TimeoutError:
                if self.stop_event.is_set():
                    break
                continue

            observed_at = self.clock()
            messages = _decode_messages(raw)

            state = self.state_store.transition(
                state,
                event="message-received",
                now=observed_at,
                last_message_at=_timestamp(observed_at),
            )

            for message in messages:
                if message.get("T") == "error":
                    code = message.get("code")
                    detail = str(message.get("msg", "stream error")).strip()
                    raise RuntimeError(f"Alpaca stream error {code}: {detail}")

            for article in _news_messages(messages):
                received_at = self.clock()
                status = self._ingest_article(
                    article,
                    received_at=received_at,
                )

                changes: dict[str, Any] = {
                    "articles_received": int(state["articles_received"]) + 1,
                    "last_article_at": _timestamp(received_at),
                }

                if status == "stored":
                    changes["articles_stored"] = int(state["articles_stored"]) + 1
                elif status == "duplicate":
                    changes["duplicate_count"] = int(state["duplicate_count"]) + 1
                else:
                    changes["rejected_count"] = int(state["rejected_count"]) + 1

                state = self.state_store.transition(
                    state,
                    event=f"article-{status}",
                    now=received_at,
                    **changes,
                )

        return state

    def run(self) -> dict[str, Any]:
        now = self.clock()
        state = self.state_store.transition(
            self.state_store.initial(),
            event="stream-started",
            now=now,
            state="starting",
            started_at=_timestamp(now),
            stopped_at=None,
            last_error=None,
        )

        if not stream_enabled():
            return self.state_store.transition(
                state,
                event="stream-disabled",
                now=self.clock(),
                state="disabled",
            )

        key, secret = stream_credentials()
        reconnect_count = 0

        while not self.stop_event.is_set():
            try:
                connecting_at = self.clock()
                state = self.state_store.transition(
                    state,
                    event="stream-connecting",
                    now=connecting_at,
                    state=("connecting" if reconnect_count == 0 else "reconnecting"),
                    reconnect_count=reconnect_count,
                    last_error=None,
                )

                with self.connect_stream(
                    ALPACA_NEWS_STREAM_URL,
                    open_timeout=OPEN_TIMEOUT_SECONDS,
                    ping_interval=PING_INTERVAL_SECONDS,
                    ping_timeout=PING_TIMEOUT_SECONDS,
                    close_timeout=5,
                    max_size=MAX_MESSAGE_BYTES,
                    max_queue=64,
                ) as websocket:
                    connected_at = self.clock()
                    self._receive_control(
                        websocket,
                        expected_type="success",
                        expected_message="connected",
                    )
                    state = self.state_store.transition(
                        state,
                        event="stream-connected",
                        now=connected_at,
                        state="connected",
                        last_connected_at=_timestamp(connected_at),
                    )

                    self._authenticate(
                        websocket,
                        key=key,
                        secret=secret,
                    )
                    authenticated_at = self.clock()
                    state = self.state_store.transition(
                        state,
                        event="stream-authenticated",
                        now=authenticated_at,
                        state="authenticated",
                        last_authenticated_at=_timestamp(authenticated_at),
                    )

                    self._subscribe(websocket)
                    subscribed_at = self.clock()
                    state = self.state_store.transition(
                        state,
                        event="stream-subscribed",
                        now=subscribed_at,
                        state="subscribed",
                        last_subscribed_at=_timestamp(subscribed_at),
                    )

                    reconnect_count = 0
                    state = self._consume(websocket, state)

            except TimeoutError:
                if self.stop_event.is_set():
                    break

                continue

            except (
                ConnectionClosed,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as error:
                reconnect_count += 1
                failed_at = self.clock()
                state = self.state_store.transition(
                    state,
                    event="stream-connection-failed",
                    now=failed_at,
                    state="reconnecting",
                    reconnect_count=reconnect_count,
                    last_error=_safe_error(error),
                )

                delay = _reconnect_delay(
                    reconnect_count,
                    random_unit=self.random_unit,
                )
                self.sleep(delay)

        stopped_at = self.clock()

        return self.state_store.transition(
            state,
            event="stream-stopped",
            now=stopped_at,
            state="stopped",
            stopped_at=_timestamp(stopped_at),
        )


def run_stream_worker(state_directory: Path) -> dict[str, Any]:
    stop_event = Event()

    def request_stop(
        _signal_number: int,
        _frame: object,
    ) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    return GovernedNewsStreamWorker(
        state_directory=state_directory,
        stop_event=stop_event,
    ).run()
