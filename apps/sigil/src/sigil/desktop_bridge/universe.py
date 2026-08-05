"""Bounded U.S.-listed paper-screening universe.

The universe is intentionally explicit and finite. Membership is not a claim
of whole-market coverage, and simulation seed prices are never presented as
provider quotes.
"""

from __future__ import annotations

US_LISTED_SCREENING_UNIVERSE = (
    {"symbol": "AAPL", "name": "Apple", "sector": "Technology"},
    {"symbol": "MSFT", "name": "Microsoft", "sector": "Technology"},
    {"symbol": "NVDA", "name": "NVIDIA", "sector": "Technology"},
    {"symbol": "AMZN", "name": "Amazon", "sector": "Consumer"},
    {"symbol": "GOOGL", "name": "Alphabet", "sector": "Communication"},
    {"symbol": "META", "name": "Meta Platforms", "sector": "Communication"},
    {"symbol": "JPM", "name": "JPMorgan Chase", "sector": "Financials"},
    {"symbol": "XOM", "name": "Exxon Mobil", "sector": "Energy"},
    {"symbol": "UNH", "name": "UnitedHealth", "sector": "Health Care"},
    {"symbol": "COST", "name": "Costco", "sector": "Consumer Staples"},
    {"symbol": "CAT", "name": "Caterpillar", "sector": "Industrials"},
    {"symbol": "NEE", "name": "NextEra Energy", "sector": "Utilities"},
)

PAPER_SIMULATION_PRICES = {
    "AAPL": "200.00",
    "MSFT": "452.80",
    "NVDA": "173.00",
    "AMZN": "225.00",
    "GOOGL": "190.00",
    "META": "680.00",
    "JPM": "290.00",
    "XOM": "115.00",
    "UNH": "320.00",
    "COST": "980.00",
    "CAT": "430.00",
    "NEE": "75.00",
}

