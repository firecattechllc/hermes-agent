"""Strict YAML loader for populating a :class:`~runway.providers.gateway.registry.ChannelRegistry`
without writing Python (spec section 14). "Strict" is inherited for free:
every model in this package already sets ``extra="forbid"``, so an unknown
YAML field fails validation rather than being silently ignored — no
additional strict-mode plumbing needed here.

No credentials are ever accepted here. ``credential_ref`` is required and
already validated (by ``ChannelConfig``) to be a pointer, never a raw
secret — a YAML file containing an API key fails the same
``clean_credential_ref`` check it would anywhere else.

Expected shape::

    providers:
      - provider_id: acme
        display_name: Acme Gateway
        trust_tier: vetted_aggregator
        enabled: false
        provenance: {operator: "...", vetting_notes: "..."}
    channels:
      - channel_id: acme-openai
        provider_id: acme
        protocol: openai_chat_completions
        base_url: https://api.acme.example/v1
        credential_ref: "env:ACME_API_KEY"
        provenance: {operator: "..."}
    model_channel_map:
      "gpt-4o-mini@acme": acme-openai
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml
from pydantic import ValidationError

from runway.providers.gateway.models import ChannelConfig, GatewayConfig
from runway.providers.gateway.registry import ChannelRegistry


class GatewayConfigError(ValueError):
    pass


def _load_yaml_document(text: str) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GatewayConfigError(f"invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise GatewayConfigError("top-level YAML document must be a mapping")
    return data


def load_channel_registry(path: Path) -> ChannelRegistry:
    document = _load_yaml_document(path.read_text(encoding="utf-8"))
    return parse_channel_registry(document)


def parse_channel_registry(document: Dict[str, Any]) -> ChannelRegistry:
    allowed_keys = {"providers", "channels", "model_channel_map"}
    unknown = set(document) - allowed_keys
    if unknown:
        raise GatewayConfigError(f"unknown top-level keys: {sorted(unknown)}")

    try:
        providers = tuple(GatewayConfig(**item) for item in document.get("providers", []))
        channels = tuple(ChannelConfig(**item) for item in document.get("channels", []))
    except (TypeError, ValidationError) as exc:
        raise GatewayConfigError(f"malformed provider/channel entry: {exc}") from exc

    model_channel_map = document.get("model_channel_map", {})
    if not isinstance(model_channel_map, dict):
        raise GatewayConfigError("model_channel_map must be a mapping")

    return ChannelRegistry(providers=providers, channels=channels, model_channel_map=model_channel_map)
