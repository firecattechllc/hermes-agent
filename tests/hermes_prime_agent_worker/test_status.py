from __future__ import annotations

from hermes_prime_agent_worker.status import (
    DaemonHealth,
    daemon_is_running,
    most_conservative,
    parse_status,
)


def test_parse_status_empty_list_means_no_daemon():
    assert parse_status([]) == ()
    assert daemon_is_running([]) is False


def test_parse_status_running_daemon():
    payload = [
        {
            "socketPath": "/tmp/prime-agent-996/daemon.sock",
            "pid": 12345,
            "uptimeSeconds": 142,
            "version": "0.7.0",
            "buildId": "be9e2fa-dirty",
            "sessionCount": 0,
        }
    ]
    snapshots = parse_status(payload)
    assert len(snapshots) == 1
    assert snapshots[0].health == DaemonHealth.RUNNING
    assert snapshots[0].pid == 12345
    assert snapshots[0].version == "0.7.0"
    assert daemon_is_running(payload) is True


def test_parse_status_handles_malformed_payload_conservatively():
    snapshots = parse_status("not a list")
    assert len(snapshots) == 1
    assert snapshots[0].health == DaemonHealth.UNKNOWN


def test_parse_status_handles_malformed_entry_conservatively():
    snapshots = parse_status([{"unexpected": "shape"}, "not a dict"])
    assert len(snapshots) == 2
    assert all(s.health == DaemonHealth.UNKNOWN for s in snapshots)


def test_most_conservative_prefers_least_favorable():
    assert (
        most_conservative([DaemonHealth.RUNNING, DaemonHealth.NOT_RUNNING])
        == DaemonHealth.NOT_RUNNING
    )
    assert (
        most_conservative([DaemonHealth.RUNNING, DaemonHealth.UNKNOWN])
        == DaemonHealth.UNKNOWN
    )
    assert most_conservative([]) == DaemonHealth.UNKNOWN
