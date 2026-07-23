from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class CompanyResearchRequest:
    company: str
    ticker: str | None = None
    questions: tuple[str, ...] = ()

    def normalized(self) -> "CompanyResearchRequest":
        company = self.company.strip()
        ticker = self.ticker.strip().upper() if self.ticker else None
        questions = tuple(q.strip() for q in self.questions if q.strip())
        if not company:
            raise ValueError("company is required")
        return CompanyResearchRequest(company=company, ticker=ticker, questions=questions)


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str
    kind: str
    summary: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompanyResearchReport:
    company: str
    ticker: str | None
    thesis: str
    risks: tuple[str, ...]
    catalysts: tuple[str, ...]
    confidence: str
    evidence: tuple[EvidenceReference, ...]
    generated_at: datetime = field(default_factory=utc_now)
