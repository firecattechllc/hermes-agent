"""Discovery feed adapters (spec section 19) and Principle A ("discovery is
not authorization").

Every adapter in this phase is fixture-based — nothing here fetches a real
pricing feed, provider catalog, or health/status endpoint. A discovered
candidate always normalizes to :attr:`~runway.lifecycle.LifecycleState.DISCOVERED`
and :attr:`~runway.trust.TrustTier.EXPERIMENTAL_UNKNOWN`, **regardless of what
the feed itself claims** about the provider's trustworthiness — trust tier
is a deliberate, separate policy decision Runway never automates.
"""

from __future__ import annotations

from typing import Protocol, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from runway.flags import RunwayFeatureFlags
from runway.identifiers import clean_identifier, clean_label
from runway.lifecycle import LifecycleState
from runway.registry import ProviderRecord
from runway.trust import TrustTier


class DiscoveryCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    source: str = Field(..., min_length=1, max_length=128)
    suggested_provider_id: str
    suggested_display_name: str = Field(..., min_length=1, max_length=128)
    discovered_at: int = Field(..., ge=0)
    notes: str = ""

    @field_validator("candidate_id", "suggested_provider_id")
    @classmethod
    def _ids(cls, value: str) -> str:
        return clean_identifier(value)

    @field_validator("suggested_display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return clean_label(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str) -> str:
        if value and not value.strip():
            return ""
        from runway.identifiers import contains_sensitive_marker
        if contains_sensitive_marker(value):
            raise ValueError("discovery notes must not contain sensitive configuration")
        return value.strip()


class DiscoveryError(Exception):
    pass


class DiscoveryAdapter(Protocol):
    def discover(self, *, now: int) -> Tuple[DiscoveryCandidate, ...]: ...


class FixtureDiscoveryAdapter:
    """Wraps a static, in-repo fixture list as a discovery source — the
    fixture-only stand-in for a public pricing JSON feed, a provider
    ``/v1/models`` endpoint, an LMSpeed-like directory, etc.
    """

    def __init__(self, *, source: str, fixtures: Tuple[DiscoveryCandidate, ...]) -> None:
        self._source = source
        self._fixtures = fixtures

    def discover(self, *, now: int) -> Tuple[DiscoveryCandidate, ...]:
        return tuple(item.model_copy(update={"discovered_at": now}) for item in self._fixtures)


def run_discovery(
    adapters: Tuple[DiscoveryAdapter, ...], *, flags: RunwayFeatureFlags, now: int
) -> Tuple[DiscoveryCandidate, ...]:
    if not flags.discovery_enabled:
        raise DiscoveryError("discovery_enabled flag is off")
    candidates: list[DiscoveryCandidate] = []
    for adapter in adapters:
        candidates.extend(adapter.discover(now=now))
    return tuple(candidates)


def candidate_to_provider(candidate: DiscoveryCandidate) -> ProviderRecord:
    """Normalize a candidate into a routable-registry object. Always
    DISCOVERED / EXPERIMENTAL_UNKNOWN — see module docstring. Nothing about
    this function's output is ever routable until it passes through
    :mod:`runway.qualification` and then an explicit ``authorize()`` call.
    """
    return ProviderRecord(
        provider_id=candidate.suggested_provider_id,
        display_name=candidate.suggested_display_name,
        trust_tier=TrustTier.EXPERIMENTAL_UNKNOWN,
        lifecycle_state=LifecycleState.DISCOVERED,
    )
