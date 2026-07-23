"""Financial sentiment integration contracts and governed implementations."""

from .analyzers import DeterministicFinancialSentimentAnalyzer
from .hermes_link import (
    HermesLinkTitanFinBERTTransport,
    HermesTaskClient,
    UrlLibHermesTaskClient,
)
from .ports import FinancialSentimentPort
from .titan import (
    GovernedSentimentRouter,
    TitanFinBERTAnalyzer,
    TitanFinBERTError,
    TitanFinBERTTransport,
)

__all__ = [
    "DeterministicFinancialSentimentAnalyzer",
    "FinancialSentimentPort",
    "GovernedSentimentRouter",
    "GovernedTitanFinBERTTaskExecutor",
    "HermesLinkTitanFinBERTTransport",
    "HermesTaskClient",
    "LocalFinBERTInference",
    "TitanFinBERTAnalyzer",
    "TitanFinBERTError",
    "TitanFinBERTTaskError",
    "TitanFinBERTTransport",
    "UrlLibHermesTaskClient",
]

from sigil.integrations.sentiment.titan_executor import (
    GovernedTitanFinBERTTaskExecutor,
    LocalFinBERTInference,
    TitanFinBERTTaskError,
)
