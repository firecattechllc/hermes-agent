"""Provider and model registry (spec sections 2, 4, 5, 15).

Principle B is enforced structurally here: a :class:`ModelRecord` is a
**model × provider pair**, keyed by ``model_key`` (``"<model_family>@<provider_id>"``).
The same underlying model offered by two providers is always two distinct
``ModelRecord`` rows, scored independently by :mod:`runway.scoring` — they
share ``model_family`` only for grouping (e.g. canary identity comparisons),
never for identity.

Endpoint failover (section 15): a provider may declare multiple endpoints;
failing over between them does not change provider identity or trust tier.
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runway.capabilities import ModelCapabilityProfile
from runway.identifiers import clean_credential_ref, clean_identifier, clean_label
from runway.lifecycle import LifecycleState
from runway.pricing import CostModel
from runway.trust import TrustTier


class LatencyClass(IntEnum):
    INTERACTIVE = 1
    STANDARD = 2
    BATCH = 3


class EndpointRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    REGIONAL = "regional"


class EndpointRecord(BaseModel):
    """No real network address is required (or wanted) in this phase — a
    symbolic label is enough to exercise failover logic against fixtures.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    endpoint_id: str
    role: EndpointRole
    region: str = Field(default="global", min_length=1, max_length=64)

    @field_validator("endpoint_id")
    @classmethod
    def _endpoint_id(cls, value: str) -> str:
        return clean_identifier(value)


class CanaryStatus(str, Enum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    REGRESSED = "regressed"


class ProviderRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    display_name: str = Field(..., min_length=1, max_length=128)
    trust_tier: TrustTier
    lifecycle_state: LifecycleState = LifecycleState.DISCOVERED
    endpoints: Tuple[EndpointRecord, ...] = ()
    #: Opaque pointer into an external secret store (e.g. "keychain:runway/acme",
    #: "env:LAOZHANG_API_KEY"). Never a credential value itself — enforced by
    #: clean_credential_ref, which rejects raw-secret shapes without
    #: false-positiving on idiomatic pointer names like "*_API_KEY".
    credential_ref: Optional[str] = None

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return clean_label(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return clean_credential_ref(value)

    @model_validator(mode="after")
    def _endpoints_unique(self) -> "ProviderRecord":
        ids = [item.endpoint_id for item in self.endpoints]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate endpoint identifier")
        return self


class ModelRecord(BaseModel):
    """A single model × provider pairing. ``model_family`` is the
    provider-agnostic conceptual model identity (e.g. ``"deepseek-v4-flash"``);
    ``model_key`` is the routable identity and always encodes the provider.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_key: str
    model_family: str
    provider_id: str
    display_name: str = Field(..., min_length=1, max_length=128)
    capability_profile: ModelCapabilityProfile
    task_types: Tuple[str, ...]
    cost: CostModel
    latency_class: LatencyClass
    quality_score: int = Field(..., ge=0, le=100, description="Vendor-claimed prior, not measured")
    enabled: bool = True
    available: bool = True

    last_canary_at: Optional[int] = Field(default=None, ge=0)
    canary_status: CanaryStatus = CanaryStatus.NOT_RUN
    canary_suite_version: Optional[int] = Field(default=None, ge=0)
    canary_pass_rate: Optional[int] = Field(default=None, ge=0, le=100)
    canary_failure_reason: Optional[str] = None
    suspected_identity_mismatch: bool = False

    @field_validator("model_key", "model_family", "provider_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return clean_label(value)

    @field_validator("task_types")
    @classmethod
    def _task_types(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        clean = tuple(sorted({clean_identifier(value) for value in values}))
        if not clean:
            raise ValueError("models require at least one task type")
        return clean

    @model_validator(mode="after")
    def _model_key_encodes_provider(self) -> "ModelRecord":
        expected_suffix = f"@{self.provider_id}"
        if not self.model_key.endswith(expected_suffix):
            raise ValueError(
                f"model_key {self.model_key!r} must end with {expected_suffix!r} "
                "(model x provider pairs must be structurally distinct — Principle B)"
            )
        return self

    def is_routable(self) -> bool:
        return self.enabled and self.available and self.canary_status == CanaryStatus.PASSED


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    providers: Tuple[ProviderRecord, ...] = ()
    models: Tuple[ModelRecord, ...] = ()

    @model_validator(mode="after")
    def _valid_registry(self) -> "Registry":
        provider_ids = [item.provider_id for item in self.providers]
        model_keys = [item.model_key for item in self.models]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("duplicate provider identifier")
        if len(model_keys) != len(set(model_keys)):
            raise ValueError("duplicate model_key")
        missing = sorted({item.provider_id for item in self.models} - set(provider_ids))
        if missing:
            raise ValueError(f"models reference unknown providers: {', '.join(missing)}")
        return self

    def provider(self, provider_id: str) -> ProviderRecord:
        for item in self.providers:
            if item.provider_id == provider_id:
                return item
        raise KeyError(provider_id)

    def models_for_provider(self, provider_id: str) -> Tuple[ModelRecord, ...]:
        return tuple(item for item in self.models if item.provider_id == provider_id)

    def with_provider(self, provider: ProviderRecord) -> "Registry":
        """Return a new Registry with `provider` inserted or replacing an
        existing provider of the same id. Registries are immutable; lifecycle
        transitions produce a new Registry rather than mutating in place.
        """
        others = tuple(item for item in self.providers if item.provider_id != provider.provider_id)
        return self.model_copy(update={"providers": others + (provider,)})

    def with_model(self, model: ModelRecord) -> "Registry":
        others = tuple(item for item in self.models if item.model_key != model.model_key)
        return self.model_copy(update={"models": others + (model,)})
