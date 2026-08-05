from datetime import datetime, timedelta, timezone

import pytest

from sigil.desktop_bridge import runtime


NOW = datetime(2026, 7, 27, 22, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "SIGIL_DESKTOP_STATE_DIR",
        str(tmp_path / "paper-state"),
    )
    monkeypatch.setenv("SIGIL_ASSET_CATALOG_MODE", "demo")


def test_pause_blocks_future_cycles_proposals_and_executions() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    running = runtime.runtime_snapshot(now=NOW)

    assert running["automation"]["state"] == "running"
    assert running["automation"]["cycle_count"] >= 1
    assert running["proposals"]
    assert running["executions"]

    paused = runtime.control_paper_cycle(
        "pause",
        now=NOW + timedelta(seconds=1),
    )

    paused_cycle_count = paused["automation"]["cycle_count"]
    paused_proposal_ids = [
        proposal["id"]
        for proposal in paused["proposals"]
    ]
    paused_execution_ids = [
        execution["id"]
        for execution in paused["executions"]
    ]

    assert paused["automation"]["state"] == "paused"

    # Even far beyond the scheduled cycle time, Pause must remain
    # a hard execution gate.
    for offset in (5, 60, 3600):
        snapshot = runtime.runtime_snapshot(
            now=NOW + timedelta(seconds=offset),
        )

        assert snapshot["automation"]["state"] == "paused"
        assert (
            snapshot["automation"]["cycle_count"]
            == paused_cycle_count
        )
        assert [
            proposal["id"]
            for proposal in snapshot["proposals"]
        ] == paused_proposal_ids
        assert [
            execution["id"]
            for execution in snapshot["executions"]
        ] == paused_execution_ids
        assert snapshot["broker_submission_available"] is False
