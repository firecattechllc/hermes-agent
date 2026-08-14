"""Credential_ref resolution (spec section 20: "credential references
separated from provider metadata").

``ProviderRecord.credential_ref`` / ``ChannelConfig.credential_ref`` are an
opaque, validated pointer string — never a secret. This module is the one
place that turns a ``credential_ref`` into an actual secret value, and only
at call time: the resolved value is never stored on an adapter instance,
never returned from a method other than :meth:`CredentialResolver.resolve`,
never logged, and — for :class:`FileCredentialResolver` — never exported
into the process environment.

Two resolvers exist: :class:`EnvCredentialResolver` (``"env:VAR_NAME"``,
reads the process environment) and :class:`FileCredentialResolver`
(``"file:VAR_NAME"``, reads a local, permission-locked ``KEY=value`` file —
see :func:`ensure_providers_env`). Both re-read their source on every call
rather than caching, so rotating a credential takes effect immediately and
neither resolver holds a secret in memory longer than one call needs it.

Nothing in this module maintains a list of "known" provider variable names.
The file format and resolver are fully generic — any ``KEY=value`` line
works — deliberately so that no specific provider's name ships hardcoded
into Runway's credential plumbing. See
``docs/architecture/PROJECT_RUNWAY_FOUNDATION.md`` for why.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Protocol, Tuple

_ENV_PREFIX = "env:"
_FILE_PREFIX = "file:"

DEFAULT_PROVIDERS_ENV_PATH = Path.home() / ".config" / "hermes" / "runway" / "providers.env"

_DIR_MODE = 0o700
_FILE_MODE = 0o600

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_PROVIDERS_ENV_TEMPLATE = """\
# Hermes Project Runway -- local provider credentials.
#
# One KEY=value pair per line. Comments start with '#'; blank lines are
# ignored. This file is only ever parsed as plain text -- it is never
# sourced or executed as shell code.
#
# Reference a key from provider/channel config as:
#   credential_ref = "file:KEY_NAME"
#
# Nothing is pre-populated here on purpose. Add a line only for a provider
# you have independently vetted and are prepared to move through Runway's
# normal qualification/trust process (see
# docs/architecture/PROJECT_RUNWAY_FOUNDATION.md) -- this file does not
# grant a provider any trust or authorization by itself; it only makes a
# secret resolvable once you've decided to configure that provider.
#
# Example shape (not a real entry):
# RUNWAY_EXAMPLE_PROVIDER_API_KEY=
"""


class CredentialResolutionError(Exception):
    pass


class CredentialResolver(Protocol):
    def resolve(self, credential_ref: str) -> str:
        """Return the raw secret for ``credential_ref``. Raises
        :class:`CredentialResolutionError` if it cannot be resolved. Callers
        must not persist, log, or otherwise retain the return value beyond
        the single request it authorizes.
        """
        ...


class EnvCredentialResolver:
    """Resolves refs of the form ``"env:VAR_NAME"`` against the process
    environment. Never caches — a fresh lookup happens on every call, so
    rotating the environment variable takes effect immediately and nothing
    here holds a secret in memory longer than one call needs it.
    """

    def resolve(self, credential_ref: str) -> str:
        if not credential_ref.startswith(_ENV_PREFIX):
            raise CredentialResolutionError(
                f"unsupported credential_ref scheme: {credential_ref!r} (expected {_ENV_PREFIX!r} prefix)"
            )
        var_name = credential_ref[len(_ENV_PREFIX):].strip()
        if not var_name:
            raise CredentialResolutionError("credential_ref env var name is empty")
        value = os.environ.get(var_name)
        if not value:
            raise CredentialResolutionError(f"environment variable {var_name!r} is not set")
        return value


def ensure_providers_env(path: Optional[Path] = None) -> Path:
    """Create ``path`` (default :data:`DEFAULT_PROVIDERS_ENV_PATH`) and its
    parent directory if missing, writing the empty template, and enforce
    ``0700``/``0600`` permissions either way (creation or pre-existing).
    ``os.chmod`` is called explicitly after ``mkdir``/``write_text`` because
    the umask can otherwise leave a directory or file more permissive than
    requested.
    """
    path = path or DEFAULT_PROVIDERS_ENV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, _DIR_MODE)
    if not path.exists():
        path.write_text(_PROVIDERS_ENV_TEMPLATE, encoding="utf-8")
    os.chmod(path, _FILE_MODE)
    return path


def _check_permissions(path: Path) -> None:
    """Fail closed (spec: "fail closed on unsafe file permissions") rather
    than silently reading a credentials file that group/other can access.
    Never include file contents in the raised message.
    """
    dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
    if dir_mode != _DIR_MODE:
        raise CredentialResolutionError(
            f"refusing to read {path.parent} -- expected mode {oct(_DIR_MODE)}, found {oct(dir_mode)}"
        )
    file_mode = stat.S_IMODE(path.stat().st_mode)
    if file_mode != _FILE_MODE:
        raise CredentialResolutionError(
            f"refusing to read {path} -- expected mode {oct(_FILE_MODE)}, found {oct(file_mode)}"
        )


def parse_providers_env(text: str) -> Dict[str, str]:
    """Strict ``KEY=value`` parser. Never evaluates or executes the file --
    every line is handled as plain text. Raises on anything that isn't
    exactly ``KEY=value`` (optionally blank or a ``#`` comment), on an
    invalid variable name, or on a duplicate key. Error messages carry line
    numbers and key names only, never values.
    """
    result: Dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise CredentialResolutionError(f"malformed providers.env entry at line {lineno}")
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not _KEY_RE.fullmatch(key):
            raise CredentialResolutionError(f"invalid variable name at line {lineno}: {key!r}")
        if key in result:
            raise CredentialResolutionError(f"duplicate variable {key!r} at line {lineno}")
        result[key] = value
    return result


class FileCredentialResolver:
    """Resolves refs of the form ``"file:VAR_NAME"`` against a local,
    permission-locked ``KEY=value`` file (default
    :data:`DEFAULT_PROVIDERS_ENV_PATH`). Never caches, never exports into
    ``os.environ`` — the value is returned to the caller and nowhere else.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or DEFAULT_PROVIDERS_ENV_PATH

    def _load(self) -> Dict[str, str]:
        if not self._path.exists():
            raise CredentialResolutionError(f"credentials file not found: {self._path}")
        _check_permissions(self._path)
        return parse_providers_env(self._path.read_text(encoding="utf-8"))

    def resolve(self, credential_ref: str) -> str:
        if not credential_ref.startswith(_FILE_PREFIX):
            raise CredentialResolutionError(
                f"unsupported credential_ref scheme: {credential_ref!r} (expected {_FILE_PREFIX!r} prefix)"
            )
        var_name = credential_ref[len(_FILE_PREFIX):].strip()
        if not var_name:
            raise CredentialResolutionError("credential_ref file variable name is empty")
        values = self._load()
        value = values.get(var_name)
        if not value:
            raise CredentialResolutionError(f"variable {var_name!r} is not set in {self._path}")
        return value

    def status(self, var_names: Iterable[str]) -> Dict[str, bool]:
        """CONFIGURED (``True``)/MISSING (``False``) per name. Never
        returns, logs, or otherwise exposes the underlying values --
        callers must only ever render the boolean.

        A missing file is a normal, expected state (nothing configured
        yet) and reports every name as MISSING rather than raising --
        distinct from :meth:`resolve`, which must raise because it has no
        boolean fallback. An *existing* file with unsafe permissions still
        fails closed (raises), since at that point silently reporting
        MISSING would mask a real misconfiguration.
        """
        if not self._path.exists():
            return {name: False for name in var_names}
        _check_permissions(self._path)
        values = parse_providers_env(self._path.read_text(encoding="utf-8"))
        return {name: bool(values.get(name)) for name in var_names}


# ── Desktop staging file (manual-editing convenience only) ─────────────────
#
# Runway never reads credentials from this file directly -- it is a
# fill-in-the-blanks staging area for humans, merged into the authoritative
# ~/.config/hermes/runway/providers.env store only via an explicit
# import_desktop_template() call (never automatic, never on a resolve path).

DEFAULT_DESKTOP_TEMPLATE_PATH = Path.home() / "Desktop" / "Runway-Provider-Keys.env"

_DESKTOP_TEMPLATE_HEADER = """\
# Hermes Project Runway -- provider key entry sheet (Desktop convenience copy)
#
# THIS FILE IS NOT READ BY RUNWAY DIRECTLY. It exists only so you can paste
# keys somewhere easy to find and edit. Once filled in, import it into the
# real, permission-locked credential store with:
#
#   python -m runway.cli credentials import ~/Desktop/Runway-Provider-Keys.env
#
# After importing, DELETE this file (or re-run `desktop-template` to clear
# it) -- your Desktop folder is commonly synced by iCloud/Dropbox/OneDrive,
# which is not a safe place to leave real API keys sitting in plain text
# longer than necessary.
#
# One KEY=value pair per line. Comments start with '#'; blank lines are
# ignored. This file is only ever parsed as plain text, never sourced or
# executed as shell code. A line left blank after '=' is skipped on import
# (it will not overwrite an already-configured credential with an empty
# value).
#
"""

_DESKTOP_TEMPLATE_NO_NAMES_HINT = """\
# No variable names were requested for this template. Add your own
# KEY=value lines below, or regenerate with e.g.:
#   python -m runway.cli credentials desktop-template --var RUNWAY_MY_PROVIDER_API_KEY
"""


def render_desktop_template(existing: Optional[Dict[str, str]] = None, var_names: Iterable[str] = ()) -> str:
    """Build the Desktop template's text content. ``existing`` (already-set
    KEY=value pairs from a prior version of the file) is preserved verbatim
    on refresh -- this never clobbers a value the user already typed in.
    ``var_names`` are added as blank entries only if not already present.
    Deliberately takes no default variable-name list -- see module
    docstring and ``docs/architecture/PROJECT_RUNWAY_FOUNDATION.md``.
    """
    entries = dict(existing or {})
    for name in var_names:
        entries.setdefault(name, "")

    if not entries:
        return _DESKTOP_TEMPLATE_HEADER + _DESKTOP_TEMPLATE_NO_NAMES_HINT

    body = "".join(f"{key}={value}\n" for key, value in sorted(entries.items()))
    return _DESKTOP_TEMPLATE_HEADER + body


def write_desktop_template(path: Optional[Path] = None, var_names: Iterable[str] = ()) -> Path:
    """Create or refresh the Desktop staging file. Only the *file* is
    chmod'd to ``0600`` -- unlike :func:`ensure_providers_env`, this never
    touches the parent directory's permissions, because that directory is
    the user's general-purpose Desktop folder, not one Runway owns.
    """
    path = path or DEFAULT_DESKTOP_TEMPLATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, str] = {}
    if path.exists():
        existing = parse_providers_env(path.read_text(encoding="utf-8"))

    path.write_text(render_desktop_template(existing, var_names), encoding="utf-8")
    os.chmod(path, _FILE_MODE)
    return path


@dataclass(frozen=True)
class ImportSummary:
    added: Tuple[str, ...]
    updated: Tuple[str, ...]
    skipped_empty: Tuple[str, ...]


def import_desktop_template(source_path: Path, target_path: Optional[Path] = None) -> ImportSummary:
    """Parse ``source_path`` (never executed, only parsed as data -- reuses
    :func:`parse_providers_env`) and upsert its non-empty entries into the
    authoritative store at ``target_path`` (default
    :data:`DEFAULT_PROVIDERS_ENV_PATH`), re-asserting ``0700``/``0600``
    permissions on it afterward. Blank-valued lines in the source (an
    unfilled template placeholder) are never imported -- they would
    otherwise silently blank out an already-configured credential. Keys
    already in the target but absent from the source are left untouched
    (import merges, it never wipes). Returns names only, never values.
    """
    if not source_path.exists():
        raise CredentialResolutionError(f"import source not found: {source_path}")
    source_entries = parse_providers_env(source_path.read_text(encoding="utf-8"))

    target_path = ensure_providers_env(target_path)
    existing_entries = parse_providers_env(target_path.read_text(encoding="utf-8"))

    added, updated, skipped_empty = [], [], []
    merged = dict(existing_entries)
    for key, value in source_entries.items():
        if not value:
            skipped_empty.append(key)
            continue
        if key in existing_entries:
            if existing_entries[key] != value:
                updated.append(key)
        else:
            added.append(key)
        merged[key] = value

    body = "".join(f"{key}={value}\n" for key, value in sorted(merged.items()))
    target_path.write_text(_PROVIDERS_ENV_TEMPLATE + body, encoding="utf-8")
    os.chmod(target_path.parent, _DIR_MODE)
    os.chmod(target_path, _FILE_MODE)

    return ImportSummary(added=tuple(sorted(added)), updated=tuple(sorted(updated)), skipped_empty=tuple(sorted(skipped_empty)))
