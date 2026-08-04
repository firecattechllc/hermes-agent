from __future__ import annotations

from sigil.ai import (
    SIGIL_CLAUDE_CREDENTIAL_ENV_VAR,
    ClaudeConfig,
    HermesClaudeProvider,
    default_claude_credential_resolver,
)


def test_sigil_credential_env_var_is_the_dedicated_name() -> None:
    assert SIGIL_CLAUDE_CREDENTIAL_ENV_VAR == "SIGIL_AI_CLAUDE_API_KEY"


def test_sigil_credential_is_preferred_over_shared_fallback() -> None:
    calls: list[str] = []

    def shared_fallback() -> str:
        calls.append("shared")
        return "shared-coding-agent-secret"

    resolver = default_claude_credential_resolver(
        strict_production_integrated=False,
        environment={SIGIL_CLAUDE_CREDENTIAL_ENV_VAR: "sigil-dedicated-secret"},
    )
    # Patch only the module-level fallback the resolver would otherwise use.
    import sigil.ai.claude as claude_module

    original = claude_module._resolve_hermes_anthropic_credential
    claude_module._resolve_hermes_anthropic_credential = shared_fallback
    try:
        assert resolver() == "sigil-dedicated-secret"
    finally:
        claude_module._resolve_hermes_anthropic_credential = original

    assert calls == []


def test_non_strict_mode_falls_back_to_shared_credential_when_unconfigured() -> None:
    import sigil.ai.claude as claude_module

    original = claude_module._resolve_hermes_anthropic_credential
    claude_module._resolve_hermes_anthropic_credential = lambda: "shared-fallback-secret"
    try:
        resolver = default_claude_credential_resolver(
            strict_production_integrated=False,
            environment={},
        )
        assert resolver() == "shared-fallback-secret"
    finally:
        claude_module._resolve_hermes_anthropic_credential = original


def test_strict_mode_never_falls_back_to_shared_credential() -> None:
    import sigil.ai.claude as claude_module

    calls: list[str] = []
    original = claude_module._resolve_hermes_anthropic_credential
    claude_module._resolve_hermes_anthropic_credential = lambda: (
        calls.append("shared") or "shared-fallback-secret"
    )
    try:
        resolver = default_claude_credential_resolver(
            strict_production_integrated=True,
            environment={},
        )
        assert resolver() is None
    finally:
        claude_module._resolve_hermes_anthropic_credential = original

    assert calls == []


def test_strict_mode_still_prefers_sigil_credential_when_configured() -> None:
    resolver = default_claude_credential_resolver(
        strict_production_integrated=True,
        environment={SIGIL_CLAUDE_CREDENTIAL_ENV_VAR: "sigil-dedicated-secret"},
    )

    assert resolver() == "sigil-dedicated-secret"


def test_blank_sigil_credential_is_treated_as_unconfigured() -> None:
    resolver = default_claude_credential_resolver(
        strict_production_integrated=True,
        environment={SIGIL_CLAUDE_CREDENTIAL_ENV_VAR: "   "},
    )

    assert resolver() is None


def test_provider_wires_strict_config_into_default_resolver() -> None:
    import sigil.ai.claude as claude_module

    calls: list[str] = []
    original = claude_module._resolve_hermes_anthropic_credential
    claude_module._resolve_hermes_anthropic_credential = lambda: (
        calls.append("shared") or "shared-fallback-secret"
    )
    try:
        provider = HermesClaudeProvider(
            ClaudeConfig(enabled=True, strict_credentials=True)
        )
        health = provider.health_probe()
    finally:
        claude_module._resolve_hermes_anthropic_credential = original

    assert calls == []
    assert health.credentials_available is False
    assert health.classification == "credentials_unavailable"


def test_provider_default_resolver_never_leaks_credential_in_repr() -> None:
    resolver = default_claude_credential_resolver(
        strict_production_integrated=False,
        environment={SIGIL_CLAUDE_CREDENTIAL_ENV_VAR: "sk-ant-super-secret"},
    )
    provider = HermesClaudeProvider(
        ClaudeConfig(enabled=True),
        credential_resolver=resolver,
    )

    health = provider.health_probe()

    assert health.credentials_available is True
    assert "sk-ant-super-secret" not in repr(health)
    assert "sk-ant-super-secret" not in repr(provider.identity)
