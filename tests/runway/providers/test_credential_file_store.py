from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from runway.flags import RunwayFeatureFlags
from runway.providers.credentials import (
    CredentialResolutionError,
    FileCredentialResolver,
    ensure_providers_env,
    import_desktop_template,
    parse_providers_env,
    render_desktop_template,
    write_desktop_template,
)

_SECRET = "sk-fake-never-a-real-key-1234567890"


def _dir_mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── ensure_providers_env: creation + permissions ─────────────────────────

def test_missing_credential_file_creates_directory_and_template(tmp_path):
    target = tmp_path / "config" / "hermes" / "runway" / "providers.env"
    assert not target.exists()

    result = ensure_providers_env(target)

    assert result == target
    assert target.exists()
    assert target.read_text().startswith("# Hermes Project Runway")
    assert _SECRET not in target.read_text()  # template never contains a real-looking value


def test_ensure_providers_env_enforces_permissions(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    assert _dir_mode(target.parent) == 0o700
    assert _dir_mode(target) == 0o600


def test_ensure_providers_env_is_idempotent_and_preserves_existing_content(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text("RUNWAY_EXAMPLE_API_KEY=" + _SECRET + "\n")
    os.chmod(target, 0o600)

    ensure_providers_env(target)  # must not overwrite an existing file
    assert _SECRET in target.read_text()


def test_ensure_providers_env_fixes_loose_permissions(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    os.chmod(target, 0o644)
    os.chmod(target.parent, 0o755)

    ensure_providers_env(target)
    assert _dir_mode(target) == 0o600
    assert _dir_mode(target.parent) == 0o700


# ── parse_providers_env ───────────────────────────────────────────────────

def test_valid_parsing():
    parsed = parse_providers_env("RUNWAY_A_API_KEY=abc123\nRUNWAY_B_API_KEY=xyz789\n")
    assert parsed == {"RUNWAY_A_API_KEY": "abc123", "RUNWAY_B_API_KEY": "xyz789"}


def test_comments_and_blank_lines_ignored():
    text = "\n# a comment\nRUNWAY_A_API_KEY=abc123\n\n   \n# another\nRUNWAY_B_API_KEY=xyz\n"
    parsed = parse_providers_env(text)
    assert parsed == {"RUNWAY_A_API_KEY": "abc123", "RUNWAY_B_API_KEY": "xyz"}


def test_malformed_entry_rejected():
    with pytest.raises(CredentialResolutionError):
        parse_providers_env("this line has no equals sign\n")


def test_invalid_variable_name_rejected():
    with pytest.raises(CredentialResolutionError):
        parse_providers_env("not a valid key=value\n")
    with pytest.raises(CredentialResolutionError):
        parse_providers_env("123STARTS_WITH_DIGIT=value\n")


def test_duplicate_variable_rejected():
    with pytest.raises(CredentialResolutionError):
        parse_providers_env("RUNWAY_A_API_KEY=one\nRUNWAY_A_API_KEY=two\n")


def test_empty_value_parses_as_empty_string():
    parsed = parse_providers_env("RUNWAY_A_API_KEY=\n")
    assert parsed == {"RUNWAY_A_API_KEY": ""}


def test_parser_never_executes_the_file_as_code():
    # A line shaped like a shell injection attempt must be treated as an
    # ordinary (and here, invalid -- no matching KEY=value) text line, never
    # evaluated/executed.
    with pytest.raises(CredentialResolutionError):
        parse_providers_env("$(rm -rf /)\n")
    # A KEY=value line whose value contains shell metacharacters is just a
    # string value, not something ever passed to a shell.
    parsed = parse_providers_env("RUNWAY_A_API_KEY=$(whoami)`echo hi`;rm -rf /\n")
    assert parsed["RUNWAY_A_API_KEY"] == "$(whoami)`echo hi`;rm -rf /"


# ── FileCredentialResolver.resolve ───────────────────────────────────────

def test_resolve_missing_credential_file_raises(tmp_path):
    resolver = FileCredentialResolver(tmp_path / "does-not-exist" / "providers.env")
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("file:RUNWAY_A_API_KEY")


def test_resolve_missing_credential_ref_raises(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    resolver = FileCredentialResolver(target)
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("file:RUNWAY_NEVER_SET_API_KEY")


def test_resolve_configured_credential_ref_returns_value(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)

    resolver = FileCredentialResolver(target)
    assert resolver.resolve("file:RUNWAY_A_API_KEY") == _SECRET


def test_resolve_rejects_wrong_scheme(tmp_path):
    resolver = FileCredentialResolver(tmp_path / "providers.env")
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("env:RUNWAY_A_API_KEY")


def test_resolve_never_exports_into_process_environment(tmp_path, monkeypatch):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)
    monkeypatch.delenv("RUNWAY_A_API_KEY", raising=False)

    FileCredentialResolver(target).resolve("file:RUNWAY_A_API_KEY")
    assert "RUNWAY_A_API_KEY" not in os.environ


def test_resolve_fails_closed_on_unsafe_file_permissions(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o644)  # world-readable -- unsafe

    resolver = FileCredentialResolver(target)
    with pytest.raises(CredentialResolutionError) as excinfo:
        resolver.resolve("file:RUNWAY_A_API_KEY")
    assert _SECRET not in str(excinfo.value)


def test_resolve_fails_closed_on_unsafe_directory_permissions(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)
    os.chmod(target.parent, 0o755)  # group/other can list the directory

    resolver = FileCredentialResolver(target)
    with pytest.raises(CredentialResolutionError) as excinfo:
        resolver.resolve("file:RUNWAY_A_API_KEY")
    assert _SECRET not in str(excinfo.value)


# ── FileCredentialResolver.status ────────────────────────────────────────

def test_status_reports_configured_and_missing_without_leaking_values(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\nRUNWAY_B_API_KEY=\n")
    os.chmod(target, 0o600)

    resolver = FileCredentialResolver(target)
    result = resolver.status(["RUNWAY_A_API_KEY", "RUNWAY_B_API_KEY", "RUNWAY_C_API_KEY"])

    assert result == {"RUNWAY_A_API_KEY": True, "RUNWAY_B_API_KEY": False, "RUNWAY_C_API_KEY": False}
    assert all(isinstance(value, bool) for value in result.values())
    assert _SECRET not in repr(result)
    assert _SECRET not in str(result)


def test_status_fails_closed_on_unsafe_permissions(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o644)

    resolver = FileCredentialResolver(target)
    with pytest.raises(CredentialResolutionError) as excinfo:
        resolver.status(["RUNWAY_A_API_KEY"])
    assert _SECRET not in str(excinfo.value)


def test_no_hardcoded_provider_names_in_template(tmp_path):
    """The template must never ship with any real-looking provider variable
    name pre-populated -- Runway does not brand any third-party provider as
    a supported credential by default.
    """
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    content = target.read_text()
    parsed = parse_providers_env("\n".join(
        line for line in content.splitlines() if not line.strip().startswith("#")
    ))
    assert parsed == {}


def test_external_execution_enabled_remains_structurally_unreachable():
    with pytest.raises(Exception):
        RunwayFeatureFlags(external_execution_enabled=True)
    assert RunwayFeatureFlags().external_execution_enabled is False


# ── Desktop template: creation ────────────────────────────────────────────

def test_desktop_template_creation_with_no_names_is_generic(tmp_path):
    target = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    result = write_desktop_template(target)

    assert result == target
    content = target.read_text()
    assert "NOT READ BY RUNWAY DIRECTLY" in content
    assert "regenerate with" in content
    # Never ships pre-populated with a real-looking variable name.
    parsed_lines = [line for line in content.splitlines() if not line.strip().startswith("#") and line.strip()]
    assert parsed_lines == []


def test_desktop_template_creation_includes_requested_names_only(tmp_path):
    target = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    write_desktop_template(target, var_names=["RUNWAY_A_API_KEY", "RUNWAY_B_API_KEY"])

    parsed = parse_providers_env(target.read_text())
    assert parsed == {"RUNWAY_A_API_KEY": "", "RUNWAY_B_API_KEY": ""}


def test_desktop_template_file_permissions_are_locked_but_directory_is_untouched(tmp_path):
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir()
    os.chmod(desktop_dir, 0o755)  # a normal user Desktop folder, not 0700
    target = desktop_dir / "Runway-Provider-Keys.env"

    write_desktop_template(target, var_names=["RUNWAY_A_API_KEY"])

    assert _dir_mode(target) == 0o600
    # Runway must never lock down the user's general-purpose Desktop folder.
    assert _dir_mode(desktop_dir) == 0o755


def test_desktop_template_refresh_preserves_already_filled_values(tmp_path):
    target = tmp_path / "Desktop" / "Runway-Provider-Keys.env"
    write_desktop_template(target, var_names=["RUNWAY_A_API_KEY", "RUNWAY_B_API_KEY"])
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\nRUNWAY_B_API_KEY=\n")

    # Refresh with an additional name -- must not wipe the value already typed in.
    write_desktop_template(target, var_names=["RUNWAY_A_API_KEY", "RUNWAY_B_API_KEY", "RUNWAY_C_API_KEY"])

    parsed = parse_providers_env(target.read_text())
    assert parsed == {"RUNWAY_A_API_KEY": _SECRET, "RUNWAY_B_API_KEY": "", "RUNWAY_C_API_KEY": ""}


def test_render_desktop_template_is_pure_and_deterministic():
    first = render_desktop_template({"RUNWAY_A_API_KEY": "x"}, ["RUNWAY_B_API_KEY"])
    second = render_desktop_template({"RUNWAY_A_API_KEY": "x"}, ["RUNWAY_B_API_KEY"])
    assert first == second


def test_no_hardcoded_provider_names_anywhere_in_desktop_template_module():
    import runway.providers.credentials as module
    source = open(module.__file__).read()
    for suspicious in (
        "CCTK", "OPENMODEL", "BLUESMINDS", "ETOK", "APIKEYFUN", "AIGOCODE", "PATEWAY",
        "PPTOKEN", "SUIXIANG", "FASTAITOKEN", "AIMZOON", "FENNO", "LANOX", "NAGORA",
    ):
        assert suspicious not in source.upper()


# ── Import: safety and semantics ─────────────────────────────────────────

def test_import_missing_source_raises(tmp_path):
    with pytest.raises(CredentialResolutionError):
        import_desktop_template(tmp_path / "does-not-exist.env", tmp_path / "cfg" / "providers.env")


def test_import_parses_as_data_never_executes(tmp_path):
    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text("RUNWAY_A_API_KEY=$(touch /tmp/should-never-run-in-tests)\n")
    target = tmp_path / "cfg" / "providers.env"

    summary = import_desktop_template(source, target)

    assert summary.added == ("RUNWAY_A_API_KEY",)
    parsed = parse_providers_env(target.read_text())
    assert parsed["RUNWAY_A_API_KEY"] == "$(touch /tmp/should-never-run-in-tests)"
    assert not (Path("/tmp") / "should-never-run-in-tests").exists()


def test_import_rejects_malformed_source(tmp_path):
    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text("this line has no equals sign\n")
    with pytest.raises(CredentialResolutionError):
        import_desktop_template(source, tmp_path / "cfg" / "providers.env")


def test_import_rejects_duplicate_keys_in_source(tmp_path):
    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text("RUNWAY_A_API_KEY=one\nRUNWAY_A_API_KEY=two\n")
    with pytest.raises(CredentialResolutionError):
        import_desktop_template(source, tmp_path / "cfg" / "providers.env")


def test_import_creates_target_with_correct_permissions_if_missing(tmp_path):
    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    target = tmp_path / "cfg" / "providers.env"

    import_desktop_template(source, target)

    assert target.exists()
    assert _dir_mode(target) == 0o600
    assert _dir_mode(target.parent) == 0o700


def test_import_adds_new_and_updates_existing_and_reports_names_only(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text("RUNWAY_A_API_KEY=old-value\nRUNWAY_UNTOUCHED_API_KEY=still-here\n")
    os.chmod(target, 0o600)

    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text(f"RUNWAY_A_API_KEY={_SECRET}\nRUNWAY_NEW_API_KEY=brand-new\n")

    summary = import_desktop_template(source, target)

    assert summary.added == ("RUNWAY_NEW_API_KEY",)
    assert summary.updated == ("RUNWAY_A_API_KEY",)
    parsed = parse_providers_env(target.read_text())
    assert parsed["RUNWAY_A_API_KEY"] == _SECRET
    assert parsed["RUNWAY_NEW_API_KEY"] == "brand-new"
    assert parsed["RUNWAY_UNTOUCHED_API_KEY"] == "still-here"  # import merges, never wipes


def test_import_skips_blank_entries_never_overwriting_with_empty(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    ensure_providers_env(target)
    target.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")
    os.chmod(target, 0o600)

    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text("RUNWAY_A_API_KEY=\nRUNWAY_B_API_KEY=\n")  # unfilled template placeholders

    summary = import_desktop_template(source, target)

    assert summary.skipped_empty == ("RUNWAY_A_API_KEY", "RUNWAY_B_API_KEY")
    assert summary.added == ()
    assert summary.updated == ()
    parsed = parse_providers_env(target.read_text())
    assert parsed["RUNWAY_A_API_KEY"] == _SECRET  # untouched, not blanked out


def test_import_result_and_exceptions_never_contain_secret_values(tmp_path):
    target = tmp_path / "cfg" / "providers.env"
    source = tmp_path / "Runway-Provider-Keys.env"
    source.write_text(f"RUNWAY_A_API_KEY={_SECRET}\n")

    summary = import_desktop_template(source, target)
    assert _SECRET not in repr(summary)
    assert _SECRET not in str(summary)

    source.write_text(f"RUNWAY_A_API_KEY={_SECRET}\nRUNWAY_A_API_KEY={_SECRET}\n")  # duplicate -> raises
    try:
        import_desktop_template(source, target)
        raise AssertionError("expected CredentialResolutionError")
    except CredentialResolutionError as exc:
        assert _SECRET not in str(exc)
