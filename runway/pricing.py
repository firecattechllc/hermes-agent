"""Provider-independent cost representation (spec section 9) and cache
economics (section 16).

Every monetary field is an integer number of **micros per million units**
(1 USD = 1_000_000 micros; "per million" mirrors token-pricing convention so
a $3/M-token price is stored as ``3_000_000``). Fixed fees and per-item
prices (image/video/audio, platform fee, free quota, promo credit) are plain
integer micros (not per-million). No floats anywhere in this module —
billing math must be exact and reproducible.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_PER_MILLION = 1_000_000


class CostModel(BaseModel):
    """A pricing snapshot for one model×provider pair at a point in time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    currency: str = Field(default="USD", min_length=3, max_length=3)
    snapshot_at: int = Field(..., ge=0)

    input_micros_per_million: int = Field(default=0, ge=0)
    output_micros_per_million: int = Field(default=0, ge=0)
    cached_input_micros_per_million: int = Field(default=0, ge=0)
    cache_write_micros_per_million: int = Field(default=0, ge=0)

    fixed_request_fee_micros: int = Field(default=0, ge=0)
    image_request_micros: int = Field(default=0, ge=0)
    video_second_micros: int = Field(default=0, ge=0)
    audio_second_micros: int = Field(default=0, ge=0)

    free_quota_micros: int = Field(default=0, ge=0)
    promotional_credit_micros: int = Field(default=0, ge=0)
    platform_fee_micros: int = Field(default=0, ge=0)

    @property
    def free_credit_micros(self) -> int:
        return self.free_quota_micros + self.promotional_credit_micros


class UsageEstimate(BaseModel):
    """What a candidate task is expected to consume. All counts, no cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    image_requests: int = Field(default=0, ge=0)
    video_seconds: int = Field(default=0, ge=0)
    audio_seconds: int = Field(default=0, ge=0)


def _scale_per_million(count: int, price_micros_per_million: int) -> int:
    """Integer-exact ``count * price / 1_000_000`` via floor division on the
    product, never via float division — avoids billing drift entirely.
    """
    return (count * price_micros_per_million) // _PER_MILLION


def estimate_cost_micros(cost: CostModel, usage: UsageEstimate) -> int:
    """Deterministic total cost estimate, gross of fixed fee and platform fee,
    net of free/promotional credit (floored at zero — a route is never
    reported as earning money).
    """
    uncached_input = max(0, usage.input_tokens - usage.cached_input_tokens)
    gross = (
        _scale_per_million(uncached_input, cost.input_micros_per_million)
        + _scale_per_million(usage.cached_input_tokens, cost.cached_input_micros_per_million)
        + _scale_per_million(usage.cache_write_tokens, cost.cache_write_micros_per_million)
        + _scale_per_million(usage.output_tokens, cost.output_micros_per_million)
        + usage.image_requests * cost.image_request_micros
        + usage.video_seconds * cost.video_second_micros
        + usage.audio_seconds * cost.audio_second_micros
        + cost.fixed_request_fee_micros
        + cost.platform_fee_micros
    )
    return max(0, gross - cost.free_credit_micros)


def cache_savings_micros(cost: CostModel, usage: UsageEstimate) -> int:
    """How much cheaper this usage is *because* of cached tokens, versus the
    same usage with zero cache reuse — the cache-advantage signal used by
    :mod:`runway.scoring`.
    """
    uncached_baseline = usage.model_copy(
        update={
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
        }
    )
    with_cache_cost = estimate_cost_micros(cost, usage)
    without_cache_cost = estimate_cost_micros(cost, uncached_baseline)
    return max(0, without_cache_cost - with_cache_cost)
