"""Validated immutable registry for governed Sigil model metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum

from .models import AI_CONTRACT_VERSION, ModelRegistration, ProviderIdentity


class RegistryValidationError(ValueError):
    """Registry data failed closed before routing."""


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (frozenset, set)):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in sorted(value.items())}
    return value


def canonical_digest(value: object) -> str:
    encoded = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernedModelRegistry:
    providers: tuple[ProviderIdentity, ...]
    models: tuple[ModelRegistration, ...]
    schema_version: int = AI_CONTRACT_VERSION

    def __post_init__(self) -> None:
        try:
            if self.schema_version != AI_CONTRACT_VERSION:
                raise RegistryValidationError("unsupported registry schema version")
            provider_ids = [provider.provider_id for provider in self.providers]
            model_ids = [model.model_id for model in self.models]
            if len(provider_ids) != len(set(provider_ids)):
                raise RegistryValidationError("duplicate provider identity")
            if len(model_ids) != len(set(model_ids)):
                raise RegistryValidationError("duplicate model identity")
            missing = sorted({model.provider_id for model in self.models} - set(provider_ids))
            if missing:
                raise RegistryValidationError(f"unknown providers: {', '.join(missing)}")
            providers = {provider.provider_id: provider for provider in self.providers}
            for model in self.models:
                if model.execution_location != providers[model.provider_id].execution_location:
                    raise RegistryValidationError(
                        f"execution location mismatch for model {model.model_id}"
                    )
        except RegistryValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise RegistryValidationError(str(error)) from error

    @property
    def revision(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "providers": [
                asdict(item) for item in sorted(self.providers, key=lambda x: x.provider_id)
            ],
            "models": [asdict(item) for item in sorted(self.models, key=lambda x: x.model_id)],
        }
        return f"sha256:{canonical_digest(payload)}"
