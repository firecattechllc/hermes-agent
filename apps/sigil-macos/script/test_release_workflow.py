"""Tests for script/release.sh's fail-closed guard logic.

These tests never invoke real Xcode, codesign, or Apple's notary service —
every external tool the script shells out to is replaced with a stub in an
isolated PATH, and the script itself is copied into a throwaway git repo so
git-state checks run against a fixture, not this real checkout. The goal is
to prove the *decision logic* (refuse on dirty tree, refuse on missing
identity, refuse on missing notary profile, stop cleanly before any network
call when --skip-notarize is passed) is correct, independent of whether a
real Apple toolchain is available on the machine running the tests.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASE_SCRIPT = REPO_ROOT / "apps" / "sigil-macos" / "script" / "release.sh"
EXPORT_OPTIONS = REPO_ROOT / "apps" / "sigil-macos" / "script" / "ExportOptions.plist"


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\nset -e\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A throwaway git repo shaped like apps/sigil-macos, containing a real
    copy of release.sh so BASH_SOURCE-derived paths point inside the fixture."""
    app_dir = tmp_path / "apps" / "sigil-macos"
    script_dir = app_dir / "script"
    script_dir.mkdir(parents=True)
    shutil.copy2(RELEASE_SCRIPT, script_dir / "release.sh")
    shutil.copy2(EXPORT_OPTIONS, script_dir / "ExportOptions.plist")
    (script_dir / "release.sh").chmod(0o755)

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (app_dir / "README.md").write_text("fixture\n")
    (app_dir / "Sigil" / "Sigil.xcodeproj").mkdir(parents=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    return tmp_path


def _base_stub_bin(tmp_path: Path, *, identity_present: bool = True) -> Path:
    """A stub PATH with security/xcrun/xcodebuild present but every build
    step wired to fail loudly and distinctively if reached, so tests can
    assert exactly how far the pipeline got before stopping."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)

    if identity_present:
        security_body = textwrap.dedent(
            """
            if [ "$1" = "find-identity" ]; then
              echo '  1) AAAA1111 "Developer ID Application: Matthew Callaham (VJ4ADMST5U)"'
              exit 0
            fi
            exit 1
            """
        )
    else:
        security_body = textwrap.dedent(
            """
            if [ "$1" = "find-identity" ]; then
              echo '  0 valid identities found'
              exit 0
            fi
            exit 1
            """
        )
    _write_stub(bin_dir, "security", security_body)

    _write_stub(
        bin_dir,
        "xcodebuild",
        textwrap.dedent(
            """
            echo "REACHED_XCODEBUILD $*" >&2
            exit 1
            """
        ),
    )
    return bin_dir


def _run(repo: Path, bin_dir: Path, args: list[str], extra_path: str = "") -> subprocess.CompletedProcess:
    import os

    env = dict(**os.environ)
    env["PATH"] = f"{bin_dir}:{extra_path}:{env['PATH']}" if extra_path else f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        [str(repo / "apps" / "sigil-macos" / "script" / "release.sh"), *args],
        cwd=repo / "apps" / "sigil-macos",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_refuses_when_signing_identity_missing(fixture_repo: Path) -> None:
    bin_dir = _base_stub_bin(fixture_repo, identity_present=False)
    result = _run(fixture_repo, bin_dir, ["--skip-notarize"])
    assert result.returncode != 0
    assert "signing identity" in result.stderr
    assert "REACHED_XCODEBUILD" not in result.stderr, "must fail before ever invoking xcodebuild"


def test_refuses_dirty_tree_without_allow_dirty(fixture_repo: Path) -> None:
    (fixture_repo / "apps" / "sigil-macos" / "dirty.txt").write_text("uncommitted\n")
    bin_dir = _base_stub_bin(fixture_repo)
    result = _run(fixture_repo, bin_dir, ["--skip-notarize"])
    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr
    assert "REACHED_XCODEBUILD" not in result.stderr


def test_allow_dirty_bypasses_git_check(fixture_repo: Path) -> None:
    (fixture_repo / "apps" / "sigil-macos" / "dirty.txt").write_text("uncommitted\n")
    bin_dir = _base_stub_bin(fixture_repo)
    result = _run(fixture_repo, bin_dir, ["--skip-notarize", "--allow-dirty"])
    # Should get past the git-state and identity checks and reach version
    # resolution (the first xcodebuild call, whose stderr the real script
    # intentionally suppresses — so we look for its own explicit failure
    # message rather than the stub's stderr banner).
    assert "uncommitted changes" not in result.stdout + result.stderr
    assert "signing identity" not in result.stderr
    assert "showBuildSettings failed" in result.stderr
    assert result.returncode != 0  # the stub xcodebuild always fails; that's expected here


def test_refuses_missing_notary_profile_before_any_build_step(fixture_repo: Path) -> None:
    bin_dir = _base_stub_bin(fixture_repo)
    _write_stub(
        bin_dir,
        "xcrun",
        textwrap.dedent(
            """
            if [ "$1" = "notarytool" ] && [ "$2" = "history" ]; then
              echo "Error: No Keychain password item found for profile" >&2
              exit 1
            fi
            echo "unexpected xcrun invocation: $*" >&2
            exit 1
            """
        ),
    )
    result = _run(fixture_repo, bin_dir, [])  # no --skip-notarize: notarization is attempted
    assert result.returncode != 0
    assert "notarytool keychain profile" in result.stderr
    assert "REACHED_XCODEBUILD" not in result.stderr, "must fail before archiving if notarization can't possibly succeed"


def test_skip_notarize_never_touches_xcrun_notarytool(fixture_repo: Path) -> None:
    """With --skip-notarize, the notary-profile check must not run at all —
    proving the pipeline can validate build+sign without any Apple contact."""
    bin_dir = _base_stub_bin(fixture_repo)
    _write_stub(
        bin_dir,
        "xcrun",
        'echo "unexpected xcrun invocation: $*" >&2\nexit 1\n',
    )
    result = _run(fixture_repo, bin_dir, ["--skip-notarize"])
    assert "unexpected xcrun invocation" not in result.stderr
    assert "showBuildSettings failed" in result.stderr


def test_full_skip_notarize_pipeline_writes_manifest(fixture_repo: Path, tmp_path: Path) -> None:
    """End-to-end happy path with every external tool stubbed, through to a
    written manifest.json — proves the parsing/plumbing between steps is
    correct, not just the early guard clauses."""
    bin_dir = _base_stub_bin(fixture_repo)

    # Real `xcodebuild -showBuildSettings` output indents every setting name
    # with leading whitespace; release.sh's awk patterns (` NAME `) rely on
    # that leading space as a word boundary, so the fixture must match it.
    build_settings = (
        "    MARKETING_VERSION = 4.0.0\n"
        "    CURRENT_PROJECT_VERSION = 7\n"
        "    PRODUCT_BUNDLE_IDENTIFIER = com.firecattechnology.sigil.macos\n"
    )
    export_marker = tmp_path / "export_dir_used.txt"
    _write_stub(
        bin_dir,
        "xcodebuild",
        textwrap.dedent(
            f"""
            if [ "$1" = "-project" ] && [[ "$*" == *-showBuildSettings* ]]; then
              cat <<'EOF'
{build_settings}
EOF
              exit 0
            fi
            if [ "$1" = "archive" ]; then
              for a in "$@"; do :; done
              # find -archivePath value
              prev=""
              for a in "$@"; do
                if [ "$prev" = "-archivePath" ]; then mkdir -p "$a"; fi
                prev="$a"
              done
              exit 0
            fi
            if [ "$1" = "-exportArchive" ]; then
              prev=""
              for a in "$@"; do
                if [ "$prev" = "-exportPath" ]; then
                  mkdir -p "$a/Sigil.app/Contents/MacOS"
                  mkdir -p "$a/Sigil.app/Contents/XPCServices/HermesBridgeService.xpc"
                  cat > "$a/Sigil.app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.firecattechnology.sigil.macos</string>
</dict></plist>
PLIST
                  echo "$a" > {export_marker}
                fi
                prev="$a"
              done
              exit 0
            fi
            echo "unhandled xcodebuild invocation: $*" >&2
            exit 1
            """
        ),
    )
    _write_stub(
        bin_dir,
        "codesign",
        'if [ "$1" = "--verify" ]; then exit 0; fi\n'
        "printf 'Authority=Developer ID Application: Matthew Callaham (VJ4ADMST5U)\\nTeamIdentifier=VJ4ADMST5U\\n'\n"
        "exit 0\n",
    )
    _write_stub(bin_dir, "defaults", 'echo "com.firecattechnology.sigil.macos"\nexit 0\n')
    _write_stub(bin_dir, "spctl", 'echo "rejected" >&2\nexit 3\n')

    result = _run(fixture_repo, bin_dir, ["--skip-notarize"])
    assert result.returncode == 0, result.stderr
    assert export_marker.exists()

    release_dir = fixture_repo / "apps" / "sigil-macos" / ".release"
    manifests = list(release_dir.rglob("manifest.json"))
    assert len(manifests) == 1, f"expected exactly one manifest, found {manifests}"
    manifest = json.loads(manifests[0].read_text())
    assert manifest["marketing_version"] == "4.0.0"
    assert manifest["build_number"] == "7"
    assert manifest["bundle_id"] == "com.firecattechnology.sigil.macos"
    assert manifest["notarization"]["attempted"] is False
    assert manifest["notarization"]["succeeded"] is False
    assert manifest["signing"]["team_id"] == "VJ4ADMST5U"
