from __future__ import annotations

import pytest

from sigil.desktop_bridge import governed_news_alpaca


def test_news_credentials_prefer_authoritative_sigil_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        lambda: {
            "SIGIL_ALPACA_API_KEY_ID": "canonical-key",
            "SIGIL_ALPACA_API_SECRET_KEY": "canonical-secret",
        },
    )

    monkeypatch.setenv("APCA_API_KEY_ID", "environment-key")
    monkeypatch.setenv(
        "APCA_API_SECRET_KEY",
        "environment-secret",
    )
    monkeypatch.setenv("ALPACA_API_KEY", "wrong-alias-key")
    monkeypatch.setenv(
        "ALPACA_SECRET_KEY",
        "wrong-alias-secret",
    )

    assert governed_news_alpaca.AlpacaNewsProvider._credentials() == (
        "canonical-key",
        "canonical-secret",
    )


def test_news_credentials_fall_back_to_complete_apca_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        dict,
    )

    monkeypatch.setenv("APCA_API_KEY_ID", "environment-key")
    monkeypatch.setenv(
        "APCA_API_SECRET_KEY",
        "environment-secret",
    )

    assert governed_news_alpaca.AlpacaNewsProvider._credentials() == (
        "environment-key",
        "environment-secret",
    )


def test_news_credentials_reject_partial_environment_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        dict,
    )

    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "orphan-key")
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)

    with pytest.raises(
        RuntimeError,
        match="complete key/secret pair",
    ):
        governed_news_alpaca.AlpacaNewsProvider._credentials()


def test_news_request_uses_canonical_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        governed_news_alpaca,
        "load_credentials",
        lambda: {
            "SIGIL_ALPACA_API_KEY_ID": "canonical-key",
            "SIGIL_ALPACA_API_SECRET_KEY": "canonical-secret",
        },
    )

    def fake_fetch(
        url: str,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[object, dict[str, str]]:
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return {"news": []}, {}

    provider = governed_news_alpaca.AlpacaNewsProvider(
        fetch_json=fake_fetch,
    )

    result = provider.collect(["AAPL"])

    assert result.provider == "Alpaca News"
    assert captured["headers"] == {
        "Accept": "application/json",
        "User-Agent": "Sigil-Governed-News/2.7",
        "APCA-API-KEY-ID": "canonical-key",
        "APCA-API-SECRET-KEY": "canonical-secret",
    }
