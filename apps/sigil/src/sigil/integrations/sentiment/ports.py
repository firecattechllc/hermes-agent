from __future__ import annotations

from typing import Protocol

from sigil.financial.models import FinancialDocument, SentimentResult


class FinancialSentimentPort(Protocol):
    """Boundary implemented by FinBERT or governed fallback analyzers."""

    def analyze(self, document: FinancialDocument) -> SentimentResult:
        """Analyze one financial document without performing side effects."""
