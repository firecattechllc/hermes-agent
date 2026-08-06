from __future__ import annotations

from sigil.integrations.providers import (
    MappingCredentialResolver,
    build_default_financial_data_registry,
)


def test_default_registry_contains_both_providers() -> None:
    registry = build_default_financial_data_registry(
        identity_resolver=MappingCredentialResolver({"sec_edgar": "Example test@example.invalid"})
    )

    metadata_ids = {item.provider_id for item in registry.list_metadata()}
    assert metadata_ids == {"sec_edgar", "fred"}


def test_default_registry_providers_are_independently_resolvable() -> None:
    registry = build_default_financial_data_registry()

    sec = registry.resolve("sec_edgar")
    fred = registry.resolve("fred")

    assert sec.metadata.provider_id == "sec_edgar"
    assert fred.metadata.provider_id == "fred"


def test_default_registry_uses_environment_resolver_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SIGIL_FRED_API_KEY", raising=False)
    registry = build_default_financial_data_registry()

    health = registry.resolve("fred").health()

    assert health.configured is False  # no fabricated key: unconfigured until operator sets one
