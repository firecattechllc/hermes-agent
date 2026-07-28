from __future__ import annotations

import json
from pathlib import Path

from sigil.desktop_bridge import runtime


def test_invalid_runtime_state_is_quarantined_and_recovered(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "paper-runtime"
    state_directory.mkdir()

    state_path = state_directory / "runtime-state.json"
    state_path.write_text(
        json.dumps(
            {
                "payload": {
                    "schema_version": runtime.SCHEMA_VERSION,
                    "revision": 999,
                },
                "sha256": "invalid",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(state_directory),
    )

    snapshot = runtime.runtime_snapshot()

    assert snapshot["runtime_health"] == "healthy"
    assert snapshot["environment"] == "paper"
    assert snapshot["broker_submission_available"] is False
    assert snapshot["audit"][0]["status"] == "paper_runtime_recovered"

    quarantined = list(
        state_directory.glob("runtime-state.invalid-*.json")
    )
    assert len(quarantined) == 1

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["sha256"] == runtime._digest(persisted["payload"])


def test_malformed_runtime_json_is_quarantined_and_recovered(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_directory = tmp_path / "paper-runtime"
    state_directory.mkdir()

    state_path = state_directory / "runtime-state.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(state_directory),
    )

    snapshot = runtime.runtime_snapshot()

    assert snapshot["runtime_health"] == "healthy"
    assert snapshot["audit"][0]["status"] == "paper_runtime_recovered"
    assert len(
        list(state_directory.glob("runtime-state.invalid-*.json"))
    ) == 1
