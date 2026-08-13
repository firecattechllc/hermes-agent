from __future__ import annotations

from runway.providers.gateway.models import ChannelConfig, GatewayConfig, GatewayProtocol, RailProvenance
from runway.providers.gateway.registry import ChannelRegistry
from runway.trust import TrustTier


def make_provider(provider_id: str = "acme", **overrides) -> GatewayConfig:
    fields = dict(
        provider_id=provider_id, display_name=provider_id.title(),
        trust_tier=TrustTier.VETTED_AGGREGATOR, enabled=True,
        provenance=RailProvenance(operator=f"{provider_id} operator"),
    )
    fields.update(overrides)
    return GatewayConfig(**fields)


def make_channel(channel_id: str = "acme-openai", provider_id: str = "acme", **overrides) -> ChannelConfig:
    fields = dict(
        channel_id=channel_id, provider_id=provider_id,
        protocol=GatewayProtocol.OPENAI_CHAT_COMPLETIONS,
        base_url="https://api.example.test/v1",
        credential_ref="env:ACME_API_KEY",
        enabled=True,
        provenance=RailProvenance(operator=f"{provider_id} operator"),
    )
    fields.update(overrides)
    return ChannelConfig(**fields)


def make_registry(*, providers=(), channels=(), model_channel_map=None, authorize=()) -> ChannelRegistry:
    registry = ChannelRegistry(providers=tuple(providers), channels=tuple(channels), model_channel_map=model_channel_map or {})
    for channel_id in authorize:
        registry = registry.authorize_channel(channel_id)
    return registry
