from __future__ import annotations

import pytest

from runway.providers.gateway.config_loader import GatewayConfigError, load_channel_registry, parse_channel_registry


def _valid_document() -> dict:
    return {
        "providers": [{
            "provider_id": "acme", "display_name": "Acme", "trust_tier": "vetted_aggregator",
            "enabled": False, "provenance": {"operator": "acme ops"},
        }],
        "channels": [{
            "channel_id": "acme-c1", "provider_id": "acme", "protocol": "openai_chat_completions",
            "base_url": "https://api.acme.example/v1", "credential_ref": "env:ACME_KEY",
            "provenance": {"operator": "acme ops"},
        }],
        "model_channel_map": {"gpt-4o-mini@acme": "acme-c1"},
    }


def test_parses_valid_document():
    registry = parse_channel_registry(_valid_document())
    assert registry.provider("acme").trust_tier.name == "VETTED_AGGREGATOR"
    assert registry.channel_for_model("gpt-4o-mini@acme").channel_id == "acme-c1"


def test_unknown_top_level_key_rejected():
    doc = _valid_document()
    doc["unexpected_field"] = "oops"
    with pytest.raises(GatewayConfigError):
        parse_channel_registry(doc)


def test_unknown_field_on_provider_rejected():
    doc = _valid_document()
    doc["providers"][0]["api_key"] = "sk-should-not-be-here"
    with pytest.raises(GatewayConfigError):
        parse_channel_registry(doc)


def test_credential_value_in_yaml_rejected():
    doc = _valid_document()
    doc["channels"][0]["credential_ref"] = "sk-live-abcdef1234567890"
    with pytest.raises(GatewayConfigError):
        parse_channel_registry(doc)


def test_invalid_yaml_syntax_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("providers: [unterminated", encoding="utf-8")
    with pytest.raises(GatewayConfigError):
        load_channel_registry(path)


def test_non_mapping_document_rejected():
    from runway.providers.gateway.config_loader import _load_yaml_document
    with pytest.raises(GatewayConfigError):
        _load_yaml_document("- just\n- a\n- list\n")


def test_round_trip_from_file(tmp_path):
    import yaml
    path = tmp_path / "gateway.yaml"
    path.write_text(yaml.safe_dump(_valid_document()), encoding="utf-8")
    registry = load_channel_registry(path)
    assert registry.channel("acme-c1").base_url == "https://api.acme.example/v1"
