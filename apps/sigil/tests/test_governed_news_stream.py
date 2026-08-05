from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Self

import pytest

from sigil.desktop_bridge.governed_news_stream import (
    GovernedNewsStreamWorker,
    NewsStreamState,
    _decode_messages,
    _reconnect_delay,
)

NOW = datetime(2026, 7, 30, 20, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)

        return current


class FakeSocket:
    def __init__(
        self,
        messages: list[object],
        *,
        stop_event: Event,
    ) -> None:
        self.messages = list(messages)
        self.sent: list[dict[str, Any]] = []
        self.stop_event = stop_event

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exception_type: object,
        _exception: object,
        _traceback: object,
    ) -> None:
        return None

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def recv(self, timeout: float | None = None) -> str:
        del timeout

        if not self.messages:
            self.stop_event.set()
            raise TimeoutError

        message = self.messages.pop(0)

        if isinstance(message, BaseException):
            raise message

        return json.dumps(message)


def article() -> dict[str, Any]:
    return {
        "T": "n",
        "id": 123,
        "headline": "Example governed stream headline",
        "summary": "Example summary",
        "author": "News Desk",
        "created_at": "2026-07-30T19:59:58Z",
        "updated_at": "2026-07-30T19:59:59Z",
        "url": "https://example.com/news/123",
        "symbols": ["MSFT"],
        "source": "example",
    }


def test_decode_accepts_list_and_single_message() -> None:
    assert _decode_messages(json.dumps({"T": "success", "msg": "connected"})) == [
        {"T": "success", "msg": "connected"}
    ]

    assert _decode_messages(json.dumps([{"T": "subscription", "news": ["*"]}])) == [
        {"T": "subscription", "news": ["*"]}
    ]


def test_reconnect_delay_is_bounded() -> None:
    assert _reconnect_delay(1, random_unit=lambda: 0) == 1
    assert _reconnect_delay(2, random_unit=lambda: 0) == 2
    assert _reconnect_delay(3, random_unit=lambda: 0) == 4
    assert _reconnect_delay(20, random_unit=lambda: 1) == 30


def test_state_is_advisory_only(tmp_path: Path) -> None:
    result = NewsStreamState(tmp_path).save(
        {
            "state": "subscribed",
            "execution_authority": True,
            "broker_submission_attempted": True,
            "paper_only": False,
        }
    )

    assert result["advisory_only"] is True
    assert result["execution_authority"] is False
    assert result["broker_submission_attempted"] is False
    assert result["paper_only"] is True


def test_stream_requires_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(
        "SIGIL_ALPACA_NEWS_STREAM_ENABLED",
        raising=False,
    )

    result = GovernedNewsStreamWorker(
        state_directory=tmp_path,
        clock=FakeClock(),
    ).run()

    assert result["state"] == "disabled"
    assert result["broker_submission_attempted"] is False


def test_stream_authenticates_subscribes_and_ingests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "SIGIL_ALPACA_NEWS_STREAM_ENABLED",
        "true",
    )
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    stop_event = Event()
    socket = FakeSocket(
        [
            [{"T": "success", "msg": "connected"}],
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "news": ["*"]}],
            [article()],
        ],
        stop_event=stop_event,
    )

    worker = GovernedNewsStreamWorker(
        state_directory=tmp_path,
        stop_event=stop_event,
        connect_stream=lambda *_args, **_kwargs: socket,
        sleep=lambda _delay: None,
        clock=FakeClock(),
        random_unit=lambda: 0,
    )

    result = worker.run()

    assert socket.sent == [
        {
            "action": "auth",
            "key": "test-key",
            "secret": "test-secret",
        },
        {
            "action": "subscribe",
            "news": ["*"],
        },
    ]
    assert result["state"] == "stopped"
    assert result["articles_received"] == 1
    assert result["articles_stored"] == 1
    assert result["duplicate_count"] == 0
    assert result["execution_authority"] is False
    assert result["broker_submission_attempted"] is False

    records = (tmp_path / "governed-news-evidence.jsonl").read_text(encoding="utf-8")

    assert "Example governed stream headline" in records


def test_duplicate_stream_article_is_not_stored_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "SIGIL_ALPACA_NEWS_STREAM_ENABLED",
        "true",
    )
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")

    stop_event = Event()
    socket = FakeSocket(
        [
            [{"T": "success", "msg": "connected"}],
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "news": ["*"]}],
            [article()],
            [article()],
        ],
        stop_event=stop_event,
    )

    result = GovernedNewsStreamWorker(
        state_directory=tmp_path,
        stop_event=stop_event,
        connect_stream=lambda *_args, **_kwargs: socket,
        sleep=lambda _delay: None,
        clock=FakeClock(),
        random_unit=lambda: 0,
    ).run()

    assert result["articles_received"] == 2
    assert result["articles_stored"] == 1
    assert result["duplicate_count"] == 1

    records = NewsStreamState(tmp_path).load()
    assert records["articles_received"] == 2
    assert records["articles_stored"] == 1
    assert records["duplicate_count"] == 1
