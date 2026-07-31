from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sigil.desktop_bridge import production_research

NOW = datetime(2026, 7, 31, 1, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self, results):
        self._results = list(results)
        self.load_calls = 0

    def load(self, *, now):
        self.load_calls += 1
        if not self._results:
            raise AssertionError("unexpected additional catalog load")
        return self._results.pop(0)


class FakeCatalogService:
    def __init__(self, results, *, refresh_error=None):
        self.store = FakeStore(results)
        self.refresh_error = refresh_error
        self.refresh_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        return {"status": "fresh"}


def install_service(monkeypatch, service):
    monkeypatch.setattr(
        production_research,
        "AssetCatalogService",
        lambda _state_directory: service,
    )
    monkeypatch.setattr(
        production_research,
        "_state_directory",
        lambda: "/tmp/sigil-test-state",
    )


def test_fresh_catalog_is_used_without_refresh(monkeypatch):
    snapshot = SimpleNamespace(normalized_assets=())
    service = FakeCatalogService([("fresh", snapshot, {"age_seconds": 10})])
    install_service(monkeypatch, service)

    result = production_research._fresh_catalog_snapshot(NOW)

    assert result is snapshot
    assert service.refresh_calls == 0
    assert service.store.load_calls == 1


def test_stale_usable_catalog_is_refreshed_before_research(monkeypatch):
    stale_snapshot = SimpleNamespace(normalized_assets=())
    fresh_snapshot = SimpleNamespace(normalized_assets=())

    service = FakeCatalogService(
        [
            (
                "stale_usable",
                stale_snapshot,
                {"age_seconds": 122888},
            ),
            (
                "fresh",
                fresh_snapshot,
                {"age_seconds": 0},
            ),
        ]
    )
    install_service(monkeypatch, service)

    result = production_research._fresh_catalog_snapshot(NOW)

    assert result is fresh_snapshot
    assert service.refresh_calls == 1
    assert service.store.load_calls == 2


def test_catalog_refresh_failure_remains_fail_closed(monkeypatch):
    stale_snapshot = SimpleNamespace(normalized_assets=())
    service = FakeCatalogService(
        [
            (
                "stale_usable",
                stale_snapshot,
                {"age_seconds": 122888},
            )
        ],
        refresh_error=RuntimeError("provider unavailable"),
    )
    install_service(monkeypatch, service)

    with pytest.raises(
        RuntimeError,
        match="production research catalog refresh failed",
    ) as captured:
        production_research._fresh_catalog_snapshot(NOW)

    assert str(captured.value.__cause__) == "provider unavailable"
    assert service.refresh_calls == 1


def test_refresh_must_produce_a_fresh_catalog(monkeypatch):
    stale_snapshot = SimpleNamespace(normalized_assets=())
    service = FakeCatalogService(
        [
            (
                "stale_usable",
                stale_snapshot,
                {"age_seconds": 122888},
            ),
            (
                "stale_usable",
                stale_snapshot,
                {"age_seconds": 122889},
            ),
        ]
    )
    install_service(monkeypatch, service)

    with pytest.raises(
        RuntimeError,
        match="production research requires a fresh governed catalog",
    ):
        production_research._fresh_catalog_snapshot(NOW)

    assert service.refresh_calls == 1
    assert service.store.load_calls == 2
