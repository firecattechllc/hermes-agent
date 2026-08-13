"""Data model for the multi-gateway exchange layer.

Identity chain (spec: "provider -> channel -> model -> protocol ->
provenance -> restriction"):

* :class:`GatewayConfig` is the **provider** — a business entity with a
  trust tier and provenance, matching ``runway.registry.ProviderRecord``'s
  role but on the execute side. Deliberately a separate class, not a shared
  base: decide-layer (``runway.registry``/``runway.scoring``) and
  execute-layer (this package) stay correlated only by matching
  ``provider_id`` strings, never by shared code, so neither can accidentally
  import the other's internals.
* :class:`ChannelConfig` is the **channel/rail** — a concrete, protocol-bound
  HTTP surface a provider exposes. A provider may expose several materially
  different channels (different protocol, different restriction set,
  different pricing) that must never be collapsed into one trust score —
  each is scored and gated independently by
  :mod:`runway.providers.gateway.eligibility`.
* **model** identity is ``ExecutionRequest.model_key`` (unchanged,
  Principle B: encodes the provider).
* **protocol** is :class:`~runway.providers.gateway.protocol.GatewayProtocol`.
* **provenance** is :class:`RailProvenance`.
* **restriction** is :class:`RailRestriction` plus ``allowed_client_classes``.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from urllib.parse import urlparse

from runway.identifiers import clean_credential_ref, clean_identifier, clean_label
from runway.pricing import CostModel
from runway.trust import TrustTier


def _coerce_trust_tier(value: object) -> object:
    """Accept a readable name (``"vetted_aggregator"``, from e.g. YAML
    config -- see config_loader.py) in addition to the raw TrustTier
    value/instance. TrustTier is an IntEnum, so without this a
    human-authored config file would have to spell out a raw integer for
    trust tier -- exactly the kind of thing a config author gets backwards.
    """
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        try:
            return TrustTier[value.strip().upper()]
        except KeyError:
            valid = ", ".join(item.name.lower() for item in TrustTier)
            raise ValueError(f"unknown trust_tier {value!r}; expected one of: {valid}") from None
    return value


def validate_https_base_url(value: str) -> str:
    """Shared endpoint-safety validator (spec section 10: "reject URLs
    containing embedded credentials"; section 1: "no invented URLs" is a
    process rule for us, this is the code-level safety rule). Rejects
    non-https schemes, missing hosts, embedded userinfo credentials
    (``https://user:pass@host``), and query/fragment components (which is
    also where a credential could hide, e.g. ``?api_key=...``).
    """
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError(f"base_url must be https, got {value!r}")
    if not parsed.hostname:
        raise ValueError(f"base_url is missing a host: {value!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"base_url must not embed credentials: {value!r}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"base_url must not include a query or fragment: {value!r}")
    return value.rstrip("/")


class GatewayProtocol(str, Enum):
    """Reusable protocol adapters (spec section 2) — not one per company.
    ``RESPONSES`` is OpenAI's Responses API (``POST /v1/responses``),
    distinct from Chat Completions.
    """

    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"


class RailRestriction(str, Enum):
    """Machine-readable restriction tags (spec section 1). Deliberately a
    general-purpose vocabulary, not a category-specific one: nothing here
    encodes a particular real service's marketing claims (e.g. no
    "subscription_derived" / "reverse_engineered" / "codex_only" values —
    those describe a specific ecosystem of subscription-pooling relays this
    build does not integrate; see docs/architecture for the reasoning).
    Operators needing a narrower, deployment-specific restriction can still
    express it via ``ChannelConfig.allowed_client_classes``, which is
    free-form.
    """

    NO_IMAGES = "no_images"
    NO_TOOLS = "no_tools"
    NO_STREAMING = "no_streaming"
    CONTEXT_LIMITED = "context_limited"
    MODEL_ALIASING_POSSIBLE = "model_aliasing_possible"
    MODEL_SUBSTITUTION_POSSIBLE = "model_substitution_possible"
    PROMOTIONAL_PRICING = "promotional_pricing"
    THIRD_PARTY_RESOLD = "third_party_resold"
    CLIENT_CLASS_RESTRICTED = "client_class_restricted"


class RailProvenance(BaseModel):
    """Who operates this channel and how it was vetted — human-entered,
    audited metadata, never inferred from the channel's own claims.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operator: str = Field(..., min_length=1, max_length=256)
    official_vendor_relationship: bool = Field(
        default=False,
        description="True only if contractually confirmed direct/official access; false by default.",
    )
    vetted_at: Optional[int] = Field(default=None, ge=0)
    vetting_notes: str = Field(default="", max_length=2000)

    @field_validator("operator", "vetting_notes")
    @classmethod
    def _no_secrets(cls, value: str) -> str:
        return clean_label(value) if value else value


class GatewayConfig(BaseModel):
    """The **provider** identity — see module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    display_name: str = Field(..., min_length=1, max_length=128)
    trust_tier: TrustTier
    enabled: bool = False
    provenance: RailProvenance
    #: Free-text pointers to where pricing/balance/health data comes from —
    #: advisory bookkeeping only, not executable config.
    pricing_source: str = Field(default="", max_length=256)
    balance_source: str = Field(default="", max_length=256)
    health_source: str = Field(default="", max_length=256)
    metadata: Dict[str, str] = Field(default_factory=dict)
    #: True for a candidate whose endpoint/protocol facts are not yet known
    #: (spec section 3: "create the provider record as incomplete/
    #: discovery-only rather than guessing"). An incomplete provider can
    #: never be enabled=True — enforced below.
    incomplete: bool = False

    @field_validator("provider_id")
    @classmethod
    def _provider_id(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return clean_label(value)

    @field_validator("trust_tier", mode="before")
    @classmethod
    def _trust_tier(cls, value: object) -> object:
        return _coerce_trust_tier(value)

    @model_validator(mode="after")
    def _enabled_requires_complete(self) -> "GatewayConfig":
        if self.enabled and self.incomplete:
            raise ValueError("an incomplete (discovery-only) provider cannot be enabled")
        return self


class ChannelConfig(BaseModel):
    """The **channel/rail** identity — see module docstring."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    provider_id: str
    protocol: GatewayProtocol
    base_url: str
    credential_ref: str
    enabled: bool = False
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    advertised_models: Tuple[str, ...] = Field(default_factory=tuple)
    allowed_client_classes: Tuple[str, ...] = Field(
        default_factory=tuple,
        description="Empty = unrestricted. Non-empty = only requests tagged with one of these classes may route here.",
    )
    restrictions: Tuple[RailRestriction, ...] = Field(default_factory=tuple)
    provenance: RailProvenance
    #: Runway model_key -> upstream model id, for the normal case the
    #: upstream id isn't just "strip the @provider_id suffix".
    model_overrides: Dict[str, str] = Field(default_factory=dict)
    #: True for a channel priced at a temporary/promotional rate — see
    #: runway.providers.gateway.models.PriceObservation.promotional and
    #: runway.pricing.CostModel.promotional. Never learned as a permanent
    #: baseline (spec section 8/12).
    promotional_pricing: bool = False

    @field_validator("channel_id", "provider_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return validate_https_base_url(value)

    @field_validator("credential_ref")
    @classmethod
    def _credential_ref(cls, value: str) -> str:
        return clean_credential_ref(value)

    @field_validator("advertised_models", "allowed_client_classes")
    @classmethod
    def _identifier_tuples(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(sorted({clean_identifier(value) for value in values}))


class PriceConfidence(str, Enum):
    DOCUMENTED = "documented"       # from the vendor's own published pricing docs
    OBSERVED = "observed"           # inferred from real billing/usage evidence
    ESTIMATED = "estimated"         # best-effort guess, lowest confidence


class PriceObservation(BaseModel):
    """A pricing snapshot with source/timestamp/confidence (spec section
    12). Distinct from :class:`runway.pricing.CostModel`: this is the
    *evidence*, CostModel is what scoring actually consumes. Converting one
    to the other is :func:`to_cost_model` — deliberately not automatic, so a
    promotional or low-confidence observation is never silently absorbed
    into permanent pricing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    channel_id: str
    model_key: str
    input_price_micros_per_million: int = Field(default=0, ge=0)
    output_price_micros_per_million: int = Field(default=0, ge=0)
    cache_read_price_micros_per_million: int = Field(default=0, ge=0)
    cache_write_price_micros_per_million: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    multiplier: int = Field(default=1000, ge=0, description="Applied as multiplier/1000 (integer-exact).")
    source: str = Field(..., min_length=1, max_length=256)
    observed_at: int = Field(..., ge=0)
    promotional: bool = False
    confidence: PriceConfidence = PriceConfidence.ESTIMATED

    def to_cost_model(self) -> CostModel:
        """Deliberately explicit, never automatic — a caller refreshing a
        ModelRecord's pricing must call this themselves, so a promotional
        or low-confidence observation is never silently absorbed as the
        permanent baseline (spec: "do not permanently learn a promotional
        rate as baseline truth").
        """
        def scale(micros: int) -> int:
            return (micros * self.multiplier) // 1000

        return CostModel(
            currency=self.currency, snapshot_at=self.observed_at,
            input_micros_per_million=scale(self.input_price_micros_per_million),
            output_micros_per_million=scale(self.output_price_micros_per_million),
            cached_input_micros_per_million=scale(self.cache_read_price_micros_per_million),
            cache_write_micros_per_million=scale(self.cache_write_price_micros_per_million),
            promotional=self.promotional,
        )


class BalanceObservationState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class BalanceQuotaType(str, Enum):
    ACCOUNT_BALANCE = "account_balance"
    PROMOTIONAL_CREDIT = "promotional_credit"
    SUBSCRIPTION_QUOTA = "subscription_quota"
    DAILY_QUOTA = "daily_quota"
    WEEKLY_QUOTA = "weekly_quota"
    RATE_LIMIT_RPM = "rate_limit_rpm"
    RATE_LIMIT_TPM = "rate_limit_tpm"


class BalanceObservation(BaseModel):
    """Upstream balance/quota evidence (spec section 6) — strictly separate
    from :mod:`runway.budget` (Runway's own ledger). Never fed into
    ``BudgetLedger.spend()``; see
    ``docs/architecture`` for the "provider credit is not Runway-owned
    cash" rule this type exists to support.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    channel_id: str
    quota_type: BalanceQuotaType
    state: BalanceObservationState
    remaining_micros: Optional[int] = Field(default=None, ge=0)
    resets_at: Optional[int] = Field(default=None, ge=0)
    observed_at: int = Field(..., ge=0)
    source: str = Field(default="", max_length=256)


class IdentityConfidence(str, Enum):
    UNVERIFIED = "unverified"
    CONSISTENT = "consistent"
    MISMATCH = "mismatch"


class ModelIdentityEvidence(BaseModel):
    """Preserves requested/advertised/reported identity separately (spec
    section 11) instead of silently normalizing across them. Built by
    :func:`evaluate_identity` from evidence already on hand — it never
    infers authenticity from ``reported_model`` alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_model_key: str
    model_family: str
    advertised_model: Optional[str] = None
    reported_model: Optional[str] = None
    identity_confidence: IdentityConfidence


def evaluate_identity(
    *, requested_model_key: str, model_family: str, advertised_model: Optional[str], reported_model: Optional[str],
) -> ModelIdentityEvidence:
    if reported_model is None:
        confidence = IdentityConfidence.UNVERIFIED
    elif reported_model == model_family or reported_model == advertised_model:
        confidence = IdentityConfidence.CONSISTENT
    else:
        confidence = IdentityConfidence.MISMATCH
    return ModelIdentityEvidence(
        requested_model_key=requested_model_key, model_family=model_family,
        advertised_model=advertised_model, reported_model=reported_model,
        identity_confidence=confidence,
    )
