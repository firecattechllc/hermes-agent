"""Parses the real deployed unit files with configparser (not just code
review) and asserts hardening keys, matching the convention established by
``tests/hermes_docs_worker/test_systemd_units.py``.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

_DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "titan" / "systemd"

_REQUIRED_HARDENING = {
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "ProtectSystem": "strict",
    "ProtectHome": "true",
    "ProtectKernelTunables": "true",
    "ProtectKernelModules": "true",
    "ProtectControlGroups": "true",
    "RestrictSUIDSGID": "true",
    "RestrictRealtime": "true",
    "LockPersonality": "true",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "UMask": "0077",
}


def _load(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str  # preserve case
    parser.read(path)
    return parser


@pytest.fixture
def service_unit() -> configparser.ConfigParser:
    return _load(_DEPLOY_DIR / "hermes-prime-agent-worker-doctor.service")


@pytest.fixture
def timer_unit() -> configparser.ConfigParser:
    return _load(_DEPLOY_DIR / "hermes-prime-agent-worker-doctor.timer")


def test_service_file_exists():
    assert (_DEPLOY_DIR / "hermes-prime-agent-worker-doctor.service").is_file()


def test_service_runs_as_dedicated_unprivileged_account(service_unit):
    section = service_unit["Service"]
    assert section["User"] == "hermes-prime-agent"
    assert section["Group"] == "hermes-prime-agent"
    assert section["User"] != "root"


def test_service_declares_full_hardening_block(service_unit):
    section = service_unit["Service"]
    for key, expected in _REQUIRED_HARDENING.items():
        assert key in section, f"missing hardening directive {key}"
        assert section[key] == expected, (
            f"{key} should be {expected!r}, got {section[key]!r}"
        )


def test_service_restricts_address_families(service_unit):
    section = service_unit["Service"]
    families = set(section["RestrictAddressFamilies"].split())
    assert families == {"AF_UNIX", "AF_INET", "AF_INET6"}


def test_service_scopes_writable_paths_to_its_own_state_dir(service_unit):
    section = service_unit["Service"]
    rw_paths = section["ReadWritePaths"].split()
    assert rw_paths == ["/var/lib/hermes-prime-agent"]
    for forbidden in ("/opt/hermes", "/etc", "/home", "/root"):
        assert forbidden not in rw_paths


def test_service_declares_resource_limits(service_unit):
    section = service_unit["Service"]
    assert "MemoryMax" in section
    assert "CPUQuota" in section
    assert "TasksMax" in section


def test_service_is_oneshot_not_a_persistent_daemon(service_unit):
    # Prime Agent's own daemon self-forks and manages its own lifecycle
    # (see the module docstring in the .service file); this unit only
    # performs periodic read-only health checks, never supervises the
    # actual worker process directly.
    assert service_unit["Service"]["Type"] == "oneshot"
    assert service_unit["Service"]["Restart"] == "no"


def test_service_never_invokes_doctor_with_fix_flag(service_unit):
    exec_start = service_unit["Service"]["ExecStart"]
    assert "--fix" not in exec_start


def test_service_runs_the_worker_cli_doctor_subcommand(service_unit):
    exec_start = service_unit["Service"]["ExecStart"]
    assert "hermes_prime_agent_worker" in exec_start
    assert exec_start.strip().endswith("doctor")


def test_timer_file_exists():
    assert (_DEPLOY_DIR / "hermes-prime-agent-worker-doctor.timer").is_file()


def test_timer_has_randomized_delay_to_avoid_thundering_herd(timer_unit):
    assert "RandomizedDelaySec" in timer_unit["Timer"]


def test_no_persistent_daemon_supervisor_unit_exists():
    """Deliberately absent: a Type=simple unit that supervises the Prime
    Agent daemon process directly. Prime Agent's own daemon self-forks and
    is sufficient on its own -- see the .service file's module docstring
    for the reasoning. If this test starts failing because such a unit was
    added, that is a deliberate architecture change requiring its own
    review, not something to happen by accident."""
    unit_names = {
        p.name for p in _DEPLOY_DIR.glob("hermes-prime-agent-worker*.service")
    }
    assert unit_names == {"hermes-prime-agent-worker-doctor.service"}
