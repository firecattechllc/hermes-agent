"""Systemd unit validation, "where practical" per the governance
requirement -- there is no systemd on most CI/dev machines to actually
load these units into, so this parses them as INI-like files and checks
the properties that matter for the governance contract: a dedicated
unprivileged user, no unconfined root, the hardening keys present, the
right ExecStart subcommand, and timer scheduling knobs (RandomizedDelaySec,
Persistent=true).
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

SYSTEMD_DIR = Path(__file__).resolve().parents[2] / "deploy" / "titan" / "systemd"

SERVICE_UNITS = (
    "hermes-docs-evidence.service",
    "hermes-docs-daily.service",
    "hermes-docs-weekly.service",
)
TIMER_UNITS = (
    "hermes-docs-evidence.timer",
    "hermes-docs-daily.timer",
    "hermes-docs-weekly.timer",
)

REQUIRED_HARDENING_KEYS = (
    "NoNewPrivileges",
    "PrivateTmp",
    "ProtectSystem",
    "ProtectHome",
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectControlGroups",
    "RestrictSUIDSGID",
    "RestrictRealtime",
    "LockPersonality",
    "CapabilityBoundingSet",
    "AmbientCapabilities",
    "RestrictAddressFamilies",
    "UMask",
)


def _parse_unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str  # preserve case
    parser.read(path, encoding="utf-8")
    return parser


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_unit_exists_and_parses(unit: str) -> None:
    path = SYSTEMD_DIR / unit
    assert path.exists(), f"missing {path}"
    _parse_unit(path)  # must not raise


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_runs_as_dedicated_unprivileged_user(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    service = config["Service"]
    assert service.get("User") == "hermes-docs"
    assert service.get("Group") == "hermes-docs"
    assert service.get("User") != "root"


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_has_all_required_hardening_keys(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    service = config["Service"]
    missing = [key for key in REQUIRED_HARDENING_KEYS if key not in service]
    assert missing == [], f"{unit} missing hardening keys: {missing}"


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_capabilities_are_empty(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    service = config["Service"]
    assert service.get("CapabilityBoundingSet", "unset").strip() == ""
    assert service.get("AmbientCapabilities", "unset").strip() == ""


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_protects_system(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    service = config["Service"]
    assert service.get("ProtectSystem") == "strict"
    assert service.get("ProtectHome") == "true"


@pytest.mark.parametrize(
    "unit,subcommand",
    [
        ("hermes-docs-evidence.service", "collect"),
        ("hermes-docs-daily.service", "daily"),
        ("hermes-docs-weekly.service", "weekly"),
    ],
)
def test_service_exec_start_uses_the_right_subcommand(unit: str, subcommand: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    exec_start = config["Service"].get("ExecStart", "")
    assert "-m hermes_docs_worker" in exec_start
    assert exec_start.strip().endswith(subcommand)
    # A real run (no --dry-run) is what the timers should trigger --
    # dry-run is a manual/CLI-only affordance.
    assert "--dry-run" not in exec_start


@pytest.mark.parametrize("unit", SERVICE_UNITS)
def test_service_environment_file_is_the_documented_config_path(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    assert config["Service"].get("EnvironmentFile") == "/etc/hermes/docs-worker.env"


@pytest.mark.parametrize("unit", TIMER_UNITS)
def test_timer_unit_exists_and_parses(unit: str) -> None:
    path = SYSTEMD_DIR / unit
    assert path.exists(), f"missing {path}"
    _parse_unit(path)


@pytest.mark.parametrize("unit", TIMER_UNITS)
def test_timer_is_persistent_and_randomized(unit: str) -> None:
    config = _parse_unit(SYSTEMD_DIR / unit)
    timer = config["Timer"]
    assert timer.get("Persistent", "").lower() == "true"
    assert int(timer.get("RandomizedDelaySec", "0")) > 0
    assert timer.get("OnCalendar")


def test_evidence_timer_is_hourly() -> None:
    config = _parse_unit(SYSTEMD_DIR / "hermes-docs-evidence.timer")
    assert config["Timer"].get("OnCalendar") == "hourly"


def test_all_units_are_wanted_by_the_expected_target() -> None:
    for unit in SERVICE_UNITS:
        config = _parse_unit(SYSTEMD_DIR / unit)
        assert config["Install"].get("WantedBy") == "multi-user.target"
    for unit in TIMER_UNITS:
        config = _parse_unit(SYSTEMD_DIR / unit)
        assert config["Install"].get("WantedBy") == "timers.target"
