from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from sigil.desktop_bridge import runtime


NOW = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)


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


def test_repeated_start_preserves_existing_schedule() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    first = runtime.runtime_snapshot(now=NOW)

    scheduled = first["automation"]["next_cycle_at"]
    executions = len(first["executions"])
    proposals = len(first["proposals"])

    repeated = runtime.control_paper_cycle(
        "start",
        now=NOW + timedelta(seconds=1),
    )

    assert repeated["automation"]["state"] == "running"
    assert repeated["automation"]["next_cycle_at"] == scheduled
    assert repeated["automation"]["cycle_count"] == 1
    assert repeated["audit"][0]["status"] == ("start_ignored_already_running")

    before_due = runtime.runtime_snapshot(
        now=NOW + timedelta(seconds=1),
    )

    assert before_due["automation"]["cycle_count"] == 1
    assert len(before_due["executions"]) == executions
    assert len(before_due["proposals"]) == proposals


def test_recent_persisted_cycle_claim_remains_active() -> None:
    with runtime._locked_state() as (state_path, state):
        state["automation"].update(
            {
                "state": "running",
                "next_cycle_at": runtime._timestamp(NOW),
                "cycle_execution_id": ("PAPER-CYCLE-000001-20260728T160000Z"),
                "cycle_started_at": runtime._timestamp(NOW),
                "cycle_status": "running",
            }
        )
        runtime._persist(state_path, state)

    recovered = runtime.runtime_snapshot(
        now=NOW + timedelta(seconds=30),
    )

    automation = recovered["automation"]

    assert automation["state"] == "running"
    assert automation["cycle_execution_id"] == ("PAPER-CYCLE-000001-20260728T160000Z")
    assert automation["cycle_started_at"] == runtime._timestamp(NOW)
    assert automation["cycle_status"] == "running"
    assert automation["last_cycle_status"] != "interrupted_recovered"
    assert all(item["status"] != "paper_cycle_interrupted_recovered" for item in recovered["audit"])


def test_handled_cycle_failure_cleans_up_and_pauses_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.control_paper_cycle("start", now=NOW)

    def fail_after_claim(*args, **kwargs):
        raise RuntimeError("simulated cycle failure")

    monkeypatch.setattr(runtime, "_cycle_order", fail_after_claim)

    # A handled Python exception cleans up immediately. A true process crash or
    # power loss leaves the persisted claim for timeout-based recovery instead.
    with pytest.raises(RuntimeError, match="simulated cycle failure"):
        runtime.runtime_snapshot(now=NOW)

    state_path = runtime._state_directory() / "runtime-state.json"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))["payload"]
    automation = persisted["automation"]

    assert automation["state"] == "paused"
    assert automation["next_cycle_at"] is None
    assert automation["pause_cause"] == "safety"
    assert automation["cycle_execution_id"] is None
    assert automation["cycle_started_at"] is None
    assert automation["cycle_status"] == "idle"
    assert automation["last_cycle_status"] == "failed"
    assert automation["cycle_count"] == 0
    assert persisted["proposals"] == []
    assert persisted["executions"] == []

    audit = persisted["audit"][0]
    assert audit["status"] == "paper_cycle_failed"
    assert audit["details"]["cycle_execution_id"].startswith("PAPER-CYCLE-000001-")
    assert audit["details"]["exception_type"] == "RuntimeError"
    assert audit["details"]["exception_message"] == "simulated cycle failure"
    assert audit["details"]["broker_submission_attempted"] is False
    assert audit["details"]["requires_manual_resume"] is True

def test_completed_cycle_clears_single_flight_claim() -> None:
    runtime.control_paper_cycle("start", now=NOW)
    completed = runtime.runtime_snapshot(now=NOW)

    automation = completed["automation"]

    assert automation["cycle_count"] == 1
    assert automation["cycle_execution_id"] is None
    assert automation["cycle_started_at"] is None
    assert automation["cycle_status"] == "idle"
    assert automation["last_cycle_status"] == "completed"

    executed = next(event for event in completed["audit"] if event["status"] == "paper_executed")
    assert executed["details"]["cycle_execution_id"]


def test_stale_persisted_cycle_claim_recovers_fail_closed() -> None:
    with runtime._locked_state() as (state_path, state):
        state["automation"].update(
            {
                "state": "running",
                "next_cycle_at": runtime._timestamp(NOW),
                "cycle_execution_id": ("PAPER-CYCLE-000001-20260728T160000Z"),
                "cycle_started_at": runtime._timestamp(NOW),
                "cycle_status": "running",
            }
        )
        runtime._persist(state_path, state)

    recovered = runtime.runtime_snapshot(
        now=NOW
        + timedelta(
            seconds=runtime.CYCLE_CLAIM_TIMEOUT_SECONDS,
        ),
    )

    automation = recovered["automation"]

    assert automation["state"] == "paused"
    assert automation["cycle_execution_id"] is None
    assert automation["cycle_started_at"] is None
    assert automation["cycle_status"] == "idle"
    assert automation["last_cycle_status"] == "interrupted_recovered"
    assert automation["pause_cause"] == "safety"

    audit = recovered["audit"][0]

    assert audit["status"] == "paper_cycle_interrupted_recovered"
    assert audit["details"]["claim_age_seconds"] == (runtime.CYCLE_CLAIM_TIMEOUT_SECONDS)
    assert audit["details"]["claim_timeout_seconds"] == (runtime.CYCLE_CLAIM_TIMEOUT_SECONDS)
    assert audit["details"]["cycle_started_at_valid"] is True
    assert audit["details"]["broker_submission_attempted"] is False
