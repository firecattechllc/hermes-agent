"""Read-only audit helpers and sanitized provider event recording."""

from __future__ import annotations

import re
from typing import Any, Callable

from sigil.accounting.models import canonical_digest

from .models import GovernedMarketDataPackage


def verify_package_identity(package: GovernedMarketDataPackage) -> bool:
    material = {
        field: getattr(package, field)
        for field in package.__dataclass_fields__
        if field != "package_identity"
    }
    return canonical_digest(material) == package.package_identity


def list_observations(package: GovernedMarketDataPackage):
    return package.observations


def list_sources(package: GovernedMarketDataPackage) -> tuple[str, ...]:
    return package.provenance.source_ids


def list_quality_reasons(package: GovernedMarketDataPackage) -> tuple[str, ...]:
    return package.quality_reasons


def list_readiness_blockers(package: GovernedMarketDataPackage) -> tuple[str, ...]:
    return package.readiness_blockers


def inspect_provenance(package: GovernedMarketDataPackage):
    return package.provenance


SECRET = re.compile(r"(secret|token|credential|api[_-]?key)", re.IGNORECASE)


def sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if SECRET.search(str(key)) else sanitize(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return value


class MarketDataAudit:
    def __init__(self, sink: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._sink = sink or (lambda _event: None)

    def record(self, event: str, details: dict[str, Any] | None = None) -> None:
        self._sink({"event": event, "details": sanitize(details or {})})
