"""Validated production research, shadow evaluation, and promotion gates."""

from .data import AlpacaProductionDataClient, ProductionDataError
from .engine import ProductionResearchService
from .models import (
    EvidenceStatus,
    MarketBar,
    MarketEvidence,
    ProductionStrategyPolicy,
    StrategyScore,
)
from .store import ProductionResearchStore

__all__ = [
    "AlpacaProductionDataClient",
    "EvidenceStatus",
    "MarketBar",
    "MarketEvidence",
    "ProductionDataError",
    "ProductionResearchService",
    "ProductionResearchStore",
    "ProductionStrategyPolicy",
    "StrategyScore",
]
