from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from runway.cli import main

_SECRET = "sk-fake-never-a-real-key-1234567890"


def test_credentials_edit_creates_file_with_permissions_and_never_prints_contents(tmp_path, capsys, monkeypatch):
    target = tmp_path / "cfg" / "providers.env"
    monkeypatch.setenv("EDITOR", "true")  # a no-op editor that just exits 0

    with patch("runway.cli.subprocess.run") as mock_run:
        exit_code = main(["credentials", "edit", "--path", str(target)])

    assert exit_code == 0
    assert target.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    mock_run.assert_called_once_with(["true", str(target)], check=False)

    captured = capsys.readouterr()
    assert "Hermes Project Runway" not in captured.out  # never dumps file content
    assert _SECRET not in captured.out


def test_credentials_edit_respects_editor_env_var(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "providers.env"
    monkeypatch.setenv("EDITOR", "my-custom-editor")
    with patch("runway.cli.subprocess.run") as mock_run:
        main(["credentials", "edit", "--path", str(target)])
    assert mock_run.call_args[0][0][0] == "my-custom-editor"


def test_credentials_edit_falls_back_when_no_editor_found(tmp_path, monkeypatch, capsys):
    target = tmp_path / "cfg" / "providers.env"
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    with patch("runway.cli.shutil.which", return_value=None):
        exit_code = main(["credentials", "edit", "--path", str(target)])
    assert exit_code == 1
    assert "no editor found" in capsys.readouterr().err
    # The file/directory are still created safely even though no editor ran.
    assert target.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_credentials_edit_reasserts_permissions_after_editor_runs(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "providers.env"
    monkeypatch.setenv("EDITOR", "true")

    def _simulate_editor_loosening_perms(*args, **kwargs):
        os.chmod(target, 0o644)
        return None

    with patch("runway.cli.subprocess.run", side_effect=_simulate_editor_loosening_perms):
        main(["credentials", "edit", "--path", str(target)])

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_credentials_status_with_no_names_prints_hint_and_does_not_error(tmp_path, capsys):
    target = tmp_path / "cfg" / "providers.env"
    exit_code = main(["credentials", "status", "--path", str(target)])
    assert exit_code == 0
    assert "No credential names" in capsys.readouterr().out


def test_credentials_status_reports_configured_and_missing(tmp_path, capsys):
    from runway.providers.credentials import ensure_providers_env

    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)

    exit_code = main([
        "credentials", "status", "--path", str(target),
        "--var", "RUNWAY_A_API_KEY", "--var", "RUNWAY_B_API_KEY",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "RUNWAY_A_API_KEY: CONFIGURED" in out
    assert "RUNWAY_B_API_KEY: MISSING" in out
    assert _SECRET not in out


def test_credentials_status_never_leaks_values_even_on_missing_file(tmp_path, capsys):
    target = tmp_path / "does-not-exist" / "providers.env"
    exit_code = main(["credentials", "status", "--path", str(target), "--var", "RUNWAY_A_API_KEY"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "RUNWAY_A_API_KEY: MISSING" in out


def test_credentials_status_fails_closed_on_unsafe_permissions(tmp_path, capsys):
    from runway.providers.credentials import ensure_providers_env

    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o644)

    exit_code = main(["credentials", "status", "--path", str(target), "--var", "RUNWAY_A_API_KEY"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert _SECRET not in captured.out
    assert _SECRET not in captured.err


def test_credentials_status_sources_names_from_gateway_config(tmp_path, capsys):
    import yaml

    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(yaml.safe_dump({
        "providers": [{
            "provider_id": "acme", "display_name": "Acme", "trust_tier": "vetted_aggregator",
            "enabled": False, "provenance": {"operator": "acme ops"},
        }],
        "channels": [{
            "channel_id": "acme-c1", "provider_id": "acme", "protocol": "openai_chat_completions",
            "base_url": "https://api.acme.example/v1", "credential_ref": "file:RUNWAY_ACME_API_KEY",
            "provenance": {"operator": "acme ops"},
        }],
    }))
    target = tmp_path / "cfg" / "providers.env"

    exit_code = main(["credentials", "status", "--path", str(target), "--config", str(config_path)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "RUNWAY_ACME_API_KEY: MISSING" in out


def test_no_provider_names_hardcoded_in_cli_module():
    import runway.cli as cli_module
    source = open(cli_module.__file__).read()
    for suspicious in (
        "CCTK", "OPENMODEL", "BLUESMINDS", "ETOK", "APIKEYFUN", "AIGOCODE", "PATEWAY", "PPTOKEN",
        "SUIXIANG", "FASTAITOKEN", "AIMZOON", "FENNO", "LANOX", "NAGORA", "LAOZHANG",
    ):
        assert suspicious not in source.upper()


def test_external_execution_enabled_remains_structurally_unreachable():
    from runway.flags import RunwayFeatureFlags
    with pytest.raises(Exception):
        RunwayFeatureFlags(external_execution_enabled=True)


# ── credentials desktop-template ─────────────────────────────────────────

def test_desktop_template_command_creates_generic_file_with_no_names(tmp_path, capsys):
    target = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    exit_code = main(["credentials", "desktop-template", "--path", str(target)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert target.exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "0 name(s) requested" in out
    assert _SECRET not in out


def test_desktop_template_command_with_explicit_vars(tmp_path, capsys):
    from runway.providers.credentials import parse_providers_env

    target = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    exit_code = main([
        "credentials", "desktop-template", "--path", str(target),
        "--var", "RUNWAY_A_API_KEY", "--var", "RUNWAY_B_API_KEY",
    ])
    assert exit_code == 0
    parsed = parse_providers_env(target.read_text())
    assert parsed == {"RUNWAY_A_API_KEY": "", "RUNWAY_B_API_KEY": ""}
    out = capsys.readouterr().out
    assert "2 name(s) requested" in out
    assert "credentials import" in out  # tells the user the follow-up step


def test_desktop_template_command_never_locks_down_desktop_directory(tmp_path):
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir()
    os.chmod(desktop_dir, 0o755)
    target = desktop_dir / "Runway-Provider-Keys.env"

    main(["credentials", "desktop-template", "--path", str(target)])

    assert stat.S_IMODE(desktop_dir.stat().st_mode) == 0o755


# ── credentials import ────────────────────────────────────────────────────

def test_import_command_reports_names_never_values(tmp_path, capsys):
    source = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    source.parent.mkdir(parents=True)
    source.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    target = tmp_path / "cfg" / "providers.env"

    exit_code = main(["credentials", "import", str(source), "--target", str(target)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "RUNWAY_A_API_KEY: added" in out
    assert _SECRET not in out
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_import_command_expands_user_home_in_source_path(tmp_path, monkeypatch, capsys):
    fake_home = tmp_path / "home"
    (fake_home / "Desktop").mkdir(parents=True)
    (fake_home / "Desktop" / "Runway-Provider-Keys.env").write_text("RUNWAY_A_API_KEY=abc\n")
    monkeypatch.setenv("HOME", str(fake_home))  # Path.expanduser() reads $HOME, not Path.home()
    target = tmp_path / "cfg" / "providers.env"

    exit_code = main(["credentials", "import", "~/Desktop/Runway-Provider-Keys.env", "--target", str(target)])
    assert exit_code == 0
    assert "RUNWAY_A_API_KEY: added" in capsys.readouterr().out


def test_import_command_malformed_source_errors_without_leaking(tmp_path, capsys):
    source = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    source.parent.mkdir(parents=True)
    source.write_text(f"not a valid line with {_SECRET} in it\n")
    target = tmp_path / "cfg" / "providers.env"

    exit_code = main(["credentials", "import", str(source), "--target", str(target)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert _SECRET not in captured.out
    assert _SECRET not in captured.err


def test_import_command_missing_source_errors_cleanly(tmp_path, capsys):
    exit_code = main([
        "credentials", "import", str(tmp_path / "nope.env"), "--target", str(tmp_path / "cfg" / "providers.env"),
    ])
    assert exit_code == 1
    assert "error:" in capsys.readouterr().err


def test_import_command_skips_blank_entries(tmp_path, capsys):
    from runway.providers.credentials import ensure_providers_env

    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)

    source = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    source.parent.mkdir(parents=True)
    source.write_text("RUNWAY_A_API_KEY=\n")  # unfilled placeholder

    exit_code = main(["credentials", "import", str(source), "--target", str(target)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "skipped (blank in source" in out
    assert _SECRET not in out
    assert _SECRET in target.read_text()  # original value preserved, not blanked
