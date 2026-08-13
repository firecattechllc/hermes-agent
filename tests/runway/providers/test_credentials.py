from __future__ import annotations

import pytest

from runway.providers.credentials import CredentialResolutionError, EnvCredentialResolver


def test_resolves_from_env_var(monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "super-secret-value")
    resolver = EnvCredentialResolver()
    assert resolver.resolve("env:MY_TEST_KEY") == "super-secret-value"


def test_rejects_unsupported_scheme():
    resolver = EnvCredentialResolver()
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("file:/etc/secret")


def test_rejects_missing_env_var(monkeypatch):
    monkeypatch.delenv("DOES_NOT_EXIST_KEY", raising=False)
    resolver = EnvCredentialResolver()
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("env:DOES_NOT_EXIST_KEY")


def test_rejects_empty_var_name():
    resolver = EnvCredentialResolver()
    with pytest.raises(CredentialResolutionError):
        resolver.resolve("env:")


def test_fresh_lookup_every_call_no_caching(monkeypatch):
    monkeypatch.setenv("ROTATING_KEY", "first-value")
    resolver = EnvCredentialResolver()
    assert resolver.resolve("env:ROTATING_KEY") == "first-value"
    monkeypatch.setenv("ROTATING_KEY", "second-value")
    assert resolver.resolve("env:ROTATING_KEY") == "second-value"
