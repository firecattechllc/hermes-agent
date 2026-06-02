"""Regression test: installers prefer a managed/standalone uv over a bare PATH uv.

A bare ``command -v uv`` / ``Get-Command uv`` frequently resolves to a
conda/Anaconda-shipped uv. Pointed at the Hermes venv via ``VIRTUAL_ENV`` that
uv's own environment assumptions collide and the dependency install breaks
(reported on Windows with a conda uv first on PATH). ``hermes update`` was fixed
the same way in PR #37605 (``hermes_cli/managed_uv.py``); this guards the
bootstrap installers so resolution checks the managed locations
(``~/.local/bin`` / ``~/.cargo/bin``) before falling back to PATH, and skips a
conda-managed PATH uv entirely.

These are ordering-contract assertions (managed-before-PATH), not data
snapshots, so routine edits won't churn them.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


def _body(text: str, signature: str) -> str:
    """Return the body of a shell/PowerShell function, bounded by its opening
    signature and the next top-level ``}`` (a brace alone on its own line)."""
    _, _, rest = text.partition(signature)
    assert rest, f"Could not find {signature!r}"
    body, _, _ = rest.partition("\n}\n")
    assert body, f"Could not find closing brace for {signature!r}"
    return body


# --------------------------------------------------------------------------- #
# install.sh
# --------------------------------------------------------------------------- #
def test_install_sh_has_conda_trust_helper() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    helper = _body(text, "uv_path_is_trusted() {")
    assert "conda" in helper, "uv_path_is_trusted must reject conda-managed uv"


def test_install_sh_prefers_managed_uv_over_path() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = _body(text, "install_uv() {")

    local_idx = body.find('if [ -x "$HOME/.local/bin/uv" ]')
    path_idx = body.find("if command -v uv &> /dev/null")
    assert local_idx != -1, "install_uv must probe ~/.local/bin/uv"
    assert path_idx != -1, "install_uv must still fall back to a PATH uv"
    assert local_idx < path_idx, (
        "managed ~/.local/bin/uv must be preferred over a bare PATH uv"
    )
    assert "uv_path_is_trusted" in body, (
        "install_uv must gate the PATH fallback through uv_path_is_trusted"
    )


# --------------------------------------------------------------------------- #
# install.ps1
# --------------------------------------------------------------------------- #
def test_install_ps1_has_conda_trust_helper() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    helper = _body(text, "function Test-UvUntrusted {")
    assert "conda" in helper.lower(), "Test-UvUntrusted must reject conda-managed uv"


def test_install_ps1_install_uv_prefers_managed_over_path() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    body = _body(text, "function Install-Uv {")

    managed_idx = body.find(r"$env:USERPROFILE\.local\bin\uv.exe")
    path_idx = body.find("$pathUv = Get-Command uv")
    assert managed_idx != -1, "Install-Uv must probe the managed uv locations"
    assert path_idx != -1, "Install-Uv must still fall back to a PATH uv"
    assert managed_idx < path_idx, (
        "managed uv locations must be preferred over a bare PATH uv"
    )
    assert "Test-UvUntrusted" in body, (
        "Install-Uv must gate the PATH fallback through Test-UvUntrusted"
    )


def test_install_ps1_resolve_uvcmd_prefers_managed_over_path() -> None:
    text = INSTALL_PS1.read_text(encoding="utf-8")
    body = _body(text, "function Resolve-UvCmd {")

    managed_idx = body.find(r"$env:USERPROFILE\.local\bin\uv.exe")
    refresh_idx = body.find('GetEnvironmentVariable("Path", "User")')
    assert managed_idx != -1, "Resolve-UvCmd must probe the managed uv locations"
    assert refresh_idx != -1, "Resolve-UvCmd must still refresh PATH as a fallback"
    assert managed_idx < refresh_idx, (
        "managed uv locations must be checked before the PATH-refresh fallback"
    )
    assert "Test-UvUntrusted" in body, (
        "Resolve-UvCmd must gate the PATH fallback through Test-UvUntrusted"
    )
