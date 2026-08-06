from __future__ import annotations

from unittest.mock import patch

import pytest

from sigil.supabase_readiness import (
    SupabaseConfig,
    SupabaseCredentials,
    SupabaseReadinessError,
    check_authenticated_readiness,
    check_public_health,
    readiness_status,
    resolve_supabase_credentials,
)

VALID_URL = "https://abcdefghijklmnopqrst.supabase.co"


def test_config_rejects_malformed_project_url_when_enabled() -> None:
    with pytest.raises(SupabaseReadinessError, match="project_url"):
        SupabaseConfig(enabled=True, project_url="https://example.com")


def test_config_allows_disabled_with_blank_url() -> None:
    config = SupabaseConfig(enabled=False)
    assert config.project_url == ""


def test_config_rejects_out_of_bounds_timeout() -> None:
    with pytest.raises(SupabaseReadinessError, match="timeout"):
        SupabaseConfig(timeout_seconds=0)


def test_credentials_reject_blank_anon_key() -> None:
    with pytest.raises(SupabaseReadinessError, match="blank"):
        SupabaseCredentials(anon_key="")


def test_credentials_repr_never_leaks_keys() -> None:
    creds = SupabaseCredentials(anon_key="secret-anon-key", service_role_key="secret-service-key")

    assert "secret-anon-key" not in repr(creds)
    assert "secret-service-key" not in repr(creds)
    assert "redacted" in repr(creds)


def test_resolve_credentials_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    assert resolve_supabase_credentials() is None


def test_resolve_credentials_reads_real_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-value")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-value")

    creds = resolve_supabase_credentials()

    assert creds is not None
    assert creds.anon_key == "anon-value"
    assert creds.service_role_key == "service-value"


def test_disabled_config_rejects_health_check() -> None:
    config = SupabaseConfig(enabled=False)

    with pytest.raises(SupabaseReadinessError, match="disabled"):
        check_public_health(config)


def test_public_health_check_real_round_trip_wire_format() -> None:
    """Exercises the real request-construction/response-parsing path with the
    socket layer mocked (no real Supabase project exists for CI), proving
    the module builds a correct GET to /auth/v1/health and parses a 200."""

    config = SupabaseConfig(enabled=True, project_url=VALID_URL)

    with patch("sigil.supabase_readiness._get", return_value=(200, b'{"date":"2026-08-05"}')) as mock_get:
        result = check_public_health(config)

    assert result["healthy"] is True
    assert mock_get.call_args[0][1] == "/auth/v1/health"


def test_authenticated_readiness_sends_apikey_and_bearer_headers() -> None:
    config = SupabaseConfig(enabled=True, project_url=VALID_URL)
    creds = SupabaseCredentials(anon_key="test-anon-key")

    with patch("sigil.supabase_readiness._get", return_value=(200, b"[]")) as mock_get:
        result = check_authenticated_readiness(config, creds)

    assert result["authenticated"] is True
    headers = mock_get.call_args[1]["headers"]
    assert headers["apikey"] == "test-anon-key"
    assert headers["Authorization"] == "Bearer test-anon-key"


def test_readiness_status_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SIGIL_SUPABASE_ENABLED", raising=False)

    result = readiness_status()

    assert result["configured"] is False
    assert result["healthy"] is False
    assert "disabled" in result["reason"]


def test_readiness_status_reports_missing_credential_precisely(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_PROJECT_URL", VALID_URL)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    with patch("sigil.supabase_readiness.check_public_health", return_value={"healthy": True, "status": 200, "raw": ""}):
        result = readiness_status()

    assert result["configured"] is False
    assert result["credential_present"] is False
    assert "SUPABASE_ANON_KEY" in result["reason"]


def test_readiness_status_never_leaks_credentials(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_PROJECT_URL", VALID_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "super-secret-anon-key-value")

    with patch("sigil.supabase_readiness.check_public_health", return_value={"healthy": True, "status": 200, "raw": ""}), \
         patch("sigil.supabase_readiness.check_authenticated_readiness", return_value={"authenticated": True, "status": 200, "raw": ""}):
        result = readiness_status()

    assert "super-secret-anon-key-value" not in str(result)


def test_readiness_status_reports_full_healthy_state(monkeypatch) -> None:
    monkeypatch.setenv("SIGIL_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("SUPABASE_PROJECT_URL", VALID_URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    with patch("sigil.supabase_readiness.check_public_health", return_value={"healthy": True, "status": 200, "raw": ""}), \
         patch("sigil.supabase_readiness.check_authenticated_readiness", return_value={"authenticated": True, "status": 200, "raw": ""}):
        result = readiness_status()

    assert result["configured"] is True
    assert result["healthy"] is True
    assert result["authenticated"] is True
