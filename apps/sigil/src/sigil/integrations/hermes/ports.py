from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class HermesAnalysisResult:
    thesis: str
    risks: tuple[str, ...]
    catalysts: tuple[str, ...]
    confidence: str
    evidence_payloads: tuple[Mapping[str, Any], ...]


class HermesGraphPort(Protocol):
    def company_context(self, *, company: str, ticker: str | None) -> Mapping[str, Any]: ...


class HermesMemoryPort(Protocol):
    def recall_company(self, *, company: str, ticker: str | None) -> Sequence[Mapping[str, Any]]: ...


class HermesAnalysisPort(Protocol):
    def analyze_company(
        self,
        *,
        company: str,
        ticker: str | None,
        questions: Sequence[str],
        graph_context: Mapping[str, Any],
        memory_context: Sequence[Mapping[str, Any]],
    ) -> HermesAnalysisResult: ...


class HermesEvidencePort(Protocol):
    def record(self, *, kind: str, payload: Mapping[str, Any]) -> str: ...
