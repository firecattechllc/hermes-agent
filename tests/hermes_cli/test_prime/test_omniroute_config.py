from __future__ import annotations

import pytest

from hermes_cli.prime.omniroute_config import (
    DEFAULT_FORBIDDEN_MAC_ADDRESSES,
    TitanRoutingConfig,
    TitanRoutingConfigError,
    validate_no_mac_dependency,
)


def _env(**overrides) -> dict:
    base = {
        "HERMES_OMNIROUTE_AUTH_TOKEN": "a" * 20,
        "HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES": "embedding,lightweight,large",
        "HERMES_OMNIROUTE_ALIAS_ROUTES": (
            "embedding=titan_ollama@embeddinggemma:latest,"
            "lightweight=titan_ollama@hermes-llama3.2:3b-64k,"
            "large=freellmapi@gpt-4o-mini"
        ),
    }
    base.update(overrides)
    return base


# ── basic parsing ────────────────────────────────────────────────────────────


def test_from_env_parses_defaults() -> None:
    config = TitanRoutingConfig.from_env(_env())
    assert config.omniroute_enabled is True
    assert config.titan_ollama_enabled is True
    assert config.freellmapi_enabled is True
    assert config.bind_host == "127.0.0.1"
    assert config.provider_priority == ("titan_ollama", "freellmapi")
    assert config.alias_routes["embedding"] == ("titan_ollama", "embeddinggemma:latest")


def test_from_env_requires_auth_token() -> None:
    env = _env()
    del env["HERMES_OMNIROUTE_AUTH_TOKEN"]
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(env)


def test_auth_token_must_be_at_least_16_chars() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_OMNIROUTE_AUTH_TOKEN="short"))


# ── Mac dependency rejection (validation rule) ──────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "HERMES_TITAN_OLLAMA_ENDPOINT": f"http://{DEFAULT_FORBIDDEN_MAC_ADDRESSES[0]}:11434"
        },
        {"HERMES_FREELLMAPI_BASE_URL": "http://matthews-macbook-air:3002"},
        {"HERMES_FREELLMAPI_BASE_URL": "http://some-macbook-pro.local:3002"},
        {"HERMES_FREELLMAPI_BASE_URL": "http://host.docker.internal:3002"},
        {"HERMES_OMNIROUTE_DENIED_PROVIDERS": "mac_fallback"},
        {"HERMES_TITAN_OLLAMA_ENDPOINT": "http:///Users/someone/ollama"},
    ],
)
def test_rejects_mac_dependency(overrides) -> None:
    with pytest.raises(TitanRoutingConfigError, match="Mac dependency"):
        TitanRoutingConfig.from_env(_env(**overrides))


def test_rejects_mac_dependency_via_extended_forbidden_list() -> None:
    env = _env(
        HERMES_TITAN_OLLAMA_ENDPOINT="http://my-other-mac-node:11434",
        HERMES_TITAN_FORBIDDEN_MAC_ADDRESSES="my-other-mac-node",
    )
    with pytest.raises(TitanRoutingConfigError, match="Mac dependency"):
        TitanRoutingConfig.from_env(env)


def test_validate_no_mac_dependency_reports_every_violation_not_just_first() -> None:
    violations = validate_no_mac_dependency({
        "a": "/Users/someone/x",
        "b": "http://host.docker.internal",
    })
    assert len(violations) == 2


def test_validate_no_mac_dependency_empty_for_clean_titan_only_values() -> None:
    violations = validate_no_mac_dependency({
        "endpoint": "http://127.0.0.1:11434",
        "base_url": "http://10.0.0.7:3002",
    })
    assert violations == ()


def test_auth_token_and_api_key_never_scanned_or_echoed() -> None:
    # A secret that happens to *contain* a forbidden marker must never cause
    # a rejection based on secret content, and must never be echoed back in
    # any raised message.
    env = _env(HERMES_OMNIROUTE_AUTH_TOKEN="tok-" + "x" * 20 + "-Users-marker")
    config = TitanRoutingConfig.from_env(env)
    assert "Users" in config.omniroute_auth_token  # constructed fine, not scanned


# ── bounded retry / timeout invariants ──────────────────────────────────────


def test_retry_limit_bounded_between_0_and_5() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_TITAN_OLLAMA_RETRY_LIMIT="99"))


def test_timeout_ms_bounded() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_FREELLMAPI_TIMEOUT_MS="1"))


def test_health_check_interval_bounded() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(
            _env(HERMES_OMNIROUTE_HEALTH_CHECK_INTERVAL_SECONDS="0")
        )


def test_audit_verbosity_must_be_known_level() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_OMNIROUTE_AUDIT_VERBOSITY="chatty"))


# ── bind host must be private/localhost ─────────────────────────────────────


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "8.8.8.8"])
def test_bind_host_rejects_wildcard_or_public(host) -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_OMNIROUTE_BIND_HOST=host))


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "10.0.0.5", "192.168.1.20", "172.20.0.4", "::1"]
)
def test_bind_host_accepts_private_addresses(host) -> None:
    config = TitanRoutingConfig.from_env(_env(HERMES_OMNIROUTE_BIND_HOST=host))
    assert config.bind_host == host


# ── offline-local-only mode ──────────────────────────────────────────────────


def test_offline_local_only_requires_titan_ollama_enabled() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(
            _env(
                HERMES_OMNIROUTE_OFFLINE_LOCAL_ONLY="true",
                HERMES_TITAN_OLLAMA_ENABLED="false",
            )
        )


def test_offline_local_only_blocks_freellmapi_alias_resolution() -> None:
    config = TitanRoutingConfig.from_env(
        _env(HERMES_OMNIROUTE_OFFLINE_LOCAL_ONLY="true")
    )
    resolution = config.resolve_alias_detailed("large")
    assert resolution.permitted is False
    assert resolution.reason == "offline_local_only_blocks_remote"

    local_resolution = config.resolve_alias_detailed("lightweight")
    assert local_resolution.permitted is True
    assert local_resolution.provider_id == "titan_ollama"


# ── alias resolution reasons ─────────────────────────────────────────────────


def test_resolve_alias_detailed_unknown_alias() -> None:
    config = TitanRoutingConfig.from_env(_env())
    resolution = config.resolve_alias_detailed("does-not-exist")
    assert resolution.permitted is False
    assert resolution.reason == "unknown_alias"


def test_resolve_alias_detailed_blank_alias() -> None:
    config = TitanRoutingConfig.from_env(_env())
    assert config.resolve_alias_detailed("").reason == "blank_alias"
    assert config.resolve_alias_detailed(None).reason == "blank_alias"


def test_resolve_alias_detailed_provider_disabled() -> None:
    config = TitanRoutingConfig.from_env(_env(HERMES_FREELLMAPI_ENABLED="false"))
    resolution = config.resolve_alias_detailed("large")
    assert resolution.permitted is False
    assert resolution.reason == "provider_disabled"


def test_resolve_alias_returns_none_for_unpermitted_and_tuple_for_resolved() -> None:
    config = TitanRoutingConfig.from_env(_env())
    assert config.resolve_alias("does-not-exist") is None
    assert config.resolve_alias("embedding") == (
        "titan_ollama",
        "embeddinggemma:latest",
    )


# ── malformed alias_routes / provider config ────────────────────────────────


def test_alias_routes_rejects_malformed_entry() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(
            _env(HERMES_OMNIROUTE_ALIAS_ROUTES="lightweight-titan_ollama-model")
        )


def test_alias_routes_rejects_unknown_provider() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(
            _env(
                HERMES_OMNIROUTE_ALIAS_ROUTES="lightweight=some_other_provider@model-x",
                HERMES_OMNIROUTE_ALLOWED_MODEL_ALIASES="lightweight",
            )
        )


def test_denying_a_provider_works_as_a_live_kill_switch_without_editing_priority_or_aliases() -> (
    None
):
    # denied_providers must not require provider_priority or alias_routes to
    # be edited in lockstep -- it is enforced at resolve time regardless of
    # what those other fields still say.
    config = TitanRoutingConfig.from_env(
        _env(
            HERMES_OMNIROUTE_PROVIDER_PRIORITY="titan_ollama,freellmapi",
            HERMES_OMNIROUTE_DENIED_PROVIDERS="freellmapi",
        )
    )
    assert config.is_provider_permitted("freellmapi") is False
    resolution = config.resolve_alias_detailed("large")
    assert resolution.permitted is False
    assert resolution.reason == "provider_denied"


def test_omniroute_disabled_with_providers_still_enabled_is_rejected() -> None:
    with pytest.raises(TitanRoutingConfigError):
        TitanRoutingConfig.from_env(_env(HERMES_OMNIROUTE_ENABLED="false"))


def test_is_provider_permitted_false_for_unknown_provider() -> None:
    config = TitanRoutingConfig.from_env(_env())
    assert config.is_provider_permitted("some_paid_provider") is False
