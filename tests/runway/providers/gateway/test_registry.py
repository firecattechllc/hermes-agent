from __future__ import annotations

import pytest

from runway.providers.gateway.registry import ChannelRegistry, ChannelRegistryError

from .conftest import make_channel, make_provider


def test_multiple_providers_multiple_channels_per_provider():
    p1, p2 = make_provider("acme"), make_provider("beta")
    c1 = make_channel("acme-openai", "acme")
    c2 = make_channel("acme-anthropic", "acme")
    c3 = make_channel("beta-openai", "beta")
    registry = ChannelRegistry(providers=(p1, p2), channels=(c1, c2, c3))
    assert {c.channel_id for c in registry.channels} == {"acme-openai", "acme-anthropic", "beta-openai"}
    assert len(registry.providers) == 2


def test_disabled_provider_and_channel_are_stored_but_flagged():
    provider = make_provider("acme", enabled=False)
    channel = make_channel(provider_id="acme", enabled=False)
    registry = ChannelRegistry(providers=(provider,), channels=(channel,))
    assert registry.provider("acme").enabled is False
    assert registry.channel(channel.channel_id).enabled is False


def test_incomplete_provider_cannot_be_enabled():
    with pytest.raises(ValueError):
        make_provider("mystery", incomplete=True, enabled=True)
    # incomplete + disabled is fine (a legitimate discovery-only record)
    provider = make_provider("mystery", incomplete=True, enabled=False)
    assert provider.incomplete is True


def test_duplicate_provider_id_rejected():
    p1, p2 = make_provider("acme"), make_provider("acme")
    with pytest.raises(ChannelRegistryError):
        ChannelRegistry(providers=(p1, p2))


def test_duplicate_channel_id_rejected():
    provider = make_provider("acme")
    c1 = make_channel("dup", "acme")
    c2 = make_channel("dup", "acme", base_url="https://other.example.test")
    with pytest.raises(ChannelRegistryError):
        ChannelRegistry(providers=(provider,), channels=(c1, c2))


def test_channel_referencing_unknown_provider_rejected():
    channel = make_channel(provider_id="ghost")
    with pytest.raises(ChannelRegistryError):
        ChannelRegistry(providers=(), channels=(channel,))


def test_model_channel_map_referencing_unknown_channel_rejected():
    provider, channel = make_provider("acme"), make_channel(provider_id="acme")
    with pytest.raises(ChannelRegistryError):
        ChannelRegistry(providers=(provider,), channels=(channel,), model_channel_map={"m@acme": "nonexistent"})


def test_authorize_unknown_channel_rejected():
    registry = ChannelRegistry()
    with pytest.raises(ChannelRegistryError):
        registry.authorize_channel("nope")


def test_authorize_is_explicit_and_scoped_to_one_channel():
    provider = make_provider("acme")
    c1, c2 = make_channel("c1", "acme"), make_channel("c2", "acme")
    registry = ChannelRegistry(providers=(provider,), channels=(c1, c2))
    assert registry.is_authorized("c1") is False
    registry = registry.authorize_channel("c1")
    assert registry.is_authorized("c1") is True
    assert registry.is_authorized("c2") is False  # authorizing one never authorizes another


def test_revoke_channel():
    provider, channel = make_provider("acme"), make_channel(provider_id="acme")
    registry = ChannelRegistry(providers=(provider,), channels=(channel,)).authorize_channel(channel.channel_id)
    assert registry.is_authorized(channel.channel_id) is True
    registry = registry.revoke_channel(channel.channel_id)
    assert registry.is_authorized(channel.channel_id) is False
