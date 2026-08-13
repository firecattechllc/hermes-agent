"""Minimal credential_ref resolution (spec section 20: "credential references
separated from provider metadata").

``ProviderRecord.credential_ref`` (runway/registry.py) is an opaque, validated
pointer string — never a secret. This module is the one place that turns a
``credential_ref`` into an actual secret value, and only at call time: the
resolved value is never stored on an adapter instance, never returned from a
method other than :meth:`CredentialResolver.resolve`, and never logged.

Only one resolver exists in this build: :class:`EnvCredentialResolver`, which
reads a named environment variable at call time. It does not read files,
keychains, or secret managers — extending to those is future work, not
needed to commission LaoZhang.
"""

from __future__ import annotations

import os
from typing import Protocol

_ENV_PREFIX = "env:"


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
