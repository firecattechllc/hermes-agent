from __future__ import annotations

import pytest

from runway.discovery import DiscoveryCandidate, DiscoveryError, FixtureDiscoveryAdapter, run_discovery
from runway.flags import RunwayFeatureFlags


def _fixture_candidate(candidate_id: str) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        candidate_id=candidate_id, source="fixture:pricing-observatory",
        suggested_provider_id=f"relay-{candidate_id}", suggested_display_name="Some Relay",
        discovered_at=0,
    )


def test_discovery_requires_flag():
    adapter = FixtureDiscoveryAdapter(source="fixture:test", fixtures=(_fixture_candidate("a"),))
    with pytest.raises(DiscoveryError):
        run_discovery((adapter,), flags=RunwayFeatureFlags(discovery_enabled=False), now=0)


def test_discovery_normalizes_and_stamps_timestamp():
    adapter = FixtureDiscoveryAdapter(source="fixture:test", fixtures=(_fixture_candidate("a"), _fixture_candidate("b")))
    candidates = run_discovery((adapter,), flags=RunwayFeatureFlags(discovery_enabled=True), now=999)
    assert len(candidates) == 2
    assert all(c.discovered_at == 999 for c in candidates)


def test_discovery_notes_reject_secret_shaped_content():
    with pytest.raises(ValueError):
        DiscoveryCandidate(
            candidate_id="a", source="fixture:test", suggested_provider_id="relay-a",
            suggested_display_name="Relay", discovered_at=0, notes="api_key=sk-abc123",
        )
