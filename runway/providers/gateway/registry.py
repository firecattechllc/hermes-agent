"""Channel registry: holds providers (:class:`GatewayConfig`) and channels
(:class:`ChannelConfig`), the model_key -> channel mapping, and channel
authorization state. This is the execute-layer analog of
``runway.registry.Registry`` — deliberately not the same class (see
``models.py`` module docstring on why decide/execute stay separate).

Registering a channel is not authorizing it (Principle A, carried over from
the foundation): a channel only becomes eligible for routing after an
explicit :meth:`ChannelRegistry.authorize_channel` call, checked by
:mod:`runway.providers.gateway.eligibility`.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Tuple

from runway.providers.gateway.models import BalanceObservation, ChannelConfig, GatewayConfig


class ChannelRegistryError(ValueError):
    pass


class ChannelRegistry:
    def __init__(
        self,
        *,
        providers: Tuple[GatewayConfig, ...] = (),
        channels: Tuple[ChannelConfig, ...] = (),
        model_channel_map: Optional[Dict[str, str]] = None,
        authorized_channel_ids: FrozenSet[str] = frozenset(),
        balance_observations: Optional[Dict[str, BalanceObservation]] = None,
    ) -> None:
        provider_ids = [p.provider_id for p in providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ChannelRegistryError("duplicate provider_id")
        channel_ids = [c.channel_id for c in channels]
        if len(channel_ids) != len(set(channel_ids)):
            raise ChannelRegistryError("duplicate channel_id (duplicate rail rejection)")

        provider_id_set = set(provider_ids)
        for channel in channels:
            if channel.provider_id not in provider_id_set:
                raise ChannelRegistryError(
                    f"channel {channel.channel_id!r} references unknown provider {channel.provider_id!r}"
                )

        model_channel_map = dict(model_channel_map or {})
        channel_id_set = set(channel_ids)
        for model_key, channel_id in model_channel_map.items():
            if channel_id not in channel_id_set:
                raise ChannelRegistryError(
                    f"model_key {model_key!r} maps to unknown channel_id {channel_id!r}"
                )

        for channel_id in authorized_channel_ids:
            if channel_id not in channel_id_set:
                raise ChannelRegistryError(f"cannot authorize unknown channel_id {channel_id!r}")

        self._providers: Dict[str, GatewayConfig] = {p.provider_id: p for p in providers}
        self._channels: Dict[str, ChannelConfig] = {c.channel_id: c for c in channels}
        self._model_channel_map = model_channel_map
        self._authorized_channel_ids = frozenset(authorized_channel_ids)
        self._balance_observations: Dict[str, BalanceObservation] = dict(balance_observations or {})

    # ── read ──────────────────────────────────────────────────────────

    def provider(self, provider_id: str) -> GatewayConfig:
        return self._providers[provider_id]

    def channel(self, channel_id: str) -> ChannelConfig:
        return self._channels[channel_id]

    def channel_for_model(self, model_key: str) -> Optional[ChannelConfig]:
        channel_id = self._model_channel_map.get(model_key)
        return self._channels.get(channel_id) if channel_id else None

    def is_authorized(self, channel_id: str) -> bool:
        return channel_id in self._authorized_channel_ids

    def latest_balance(self, channel_id: str) -> Optional[BalanceObservation]:
        return self._balance_observations.get(channel_id)

    @property
    def providers(self) -> Tuple[GatewayConfig, ...]:
        return tuple(self._providers.values())

    @property
    def channels(self) -> Tuple[ChannelConfig, ...]:
        return tuple(self._channels.values())

    # ── copy-on-write mutators ──────────────────────────────────────────

    def _replace(self, **overrides) -> "ChannelRegistry":
        fields = dict(
            providers=tuple(self._providers.values()), channels=tuple(self._channels.values()),
            model_channel_map=dict(self._model_channel_map),
            authorized_channel_ids=self._authorized_channel_ids,
            balance_observations=dict(self._balance_observations),
        )
        fields.update(overrides)
        return ChannelRegistry(**fields)

    def authorize_channel(self, channel_id: str) -> "ChannelRegistry":
        """The one and only path to routable — mirrors
        ``runway.qualification.QualificationService.authorize()``'s role for
        providers. Does not automatically authorize any other channel.
        """
        if channel_id not in self._channels:
            raise ChannelRegistryError(f"unknown channel_id {channel_id!r}")
        return self._replace(authorized_channel_ids=self._authorized_channel_ids | {channel_id})

    def revoke_channel(self, channel_id: str) -> "ChannelRegistry":
        return self._replace(authorized_channel_ids=self._authorized_channel_ids - {channel_id})

    def with_balance_observation(self, observation: BalanceObservation) -> "ChannelRegistry":
        if observation.channel_id not in self._channels:
            raise ChannelRegistryError(f"unknown channel_id {observation.channel_id!r}")
        updated = dict(self._balance_observations)
        updated[observation.channel_id] = observation
        return self._replace(balance_observations=updated)
