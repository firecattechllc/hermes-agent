from sigil.desktop_bridge import runner


def _result(command: str, payload: object | None = None) -> dict[str, object]:
    response = runner.handle_request(
        {
            "command": command,
            "payload": {} if payload is None else payload,
        }
    )
    assert response["ok"] is True
    result = response["result"]
    assert isinstance(result, dict)
    return result


def test_governed_news_desktop_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("SIGIL_ALPACA_NEWS_ENABLED", raising=False)

    status = _result("governed_news_status")
    advisory = _result("governed_news_advisory_summary")
    timeline = _result("governed_news_timeline", {"symbol": "MSFT"})
    collection = _result(
        "governed_alpaca_news_collect",
        {"symbols": ["MSFT"]},
    )

    assert status["execution_authority"] is False
    assert advisory["execution_authority"] is False
    assert timeline["broker_submission_attempted"] is False
    assert collection["status"] == "disabled"
    assert collection["paper_only"] is True
