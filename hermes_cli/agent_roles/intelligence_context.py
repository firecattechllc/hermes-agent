"""Provider-neutral planning over sanitized context and memory references."""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextAction(str, Enum):
    REUSE = "reuse"
    RETRIEVE_MEMORY = "retrieve_memory"
    SUMMARIZE = "summarize"
    COMPRESS = "compress"
    SPLIT = "split"
    EXCLUDE_STALE = "exclude_stale"
    EXCLUDE_LOW_CONFIDENCE = "exclude_low_confidence"
    PRESERVE_GOVERNANCE = "preserve_governance"


class ContextItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reference: str
    units: int = Field(..., ge=0)
    confidence: int = Field(..., ge=0, le=1000)
    authoritative: bool = False
    governance_required: bool = False
    stale: bool = False
    memory_access_permitted: bool = True

    @field_validator("reference")
    @classmethod
    def _reference(cls, value: str) -> str:
        value = value.strip()
        forbidden = ("prompt", "secret", "token", "password", "authorization", "api_key", "private key")
        if not value.startswith(("artifact://", "context://", "memory://")) or any(x in value.lower() for x in forbidden):
            raise ValueError("context must use a sanitized reference")
        return value


class ContextDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reference: str
    action: ContextAction
    reason: str
    original_units: int = Field(..., ge=0)
    planned_units: int = Field(..., ge=0)
    estimated_units_saved: int = Field(..., ge=0)


class ContextPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decisions: Tuple[ContextDecision, ...]
    included_references: Tuple[str, ...]
    excluded_references: Tuple[str, ...]
    total_input_units: int = Field(..., ge=0)
    planned_units: int = Field(..., ge=0)
    estimated_units_saved: int = Field(..., ge=0)
    valid: bool
    reason_codes: Tuple[str, ...]


def plan_context(items: Iterable[ContextItem], *, maximum_units: int, minimum_confidence: int = 500) -> ContextPlan:
    if maximum_units < 1 or not 0 <= minimum_confidence <= 1000:
        raise ValueError("invalid context policy")
    ordered = sorted(items, key=lambda x: (not x.governance_required, not x.authoritative, -x.confidence, x.reference))
    decisions = []
    included = []
    excluded = []
    used = 0
    for item in ordered:
        if item.governance_required:
            action, reason, planned = ContextAction.PRESERVE_GOVERNANCE, "required_governance_context", item.units
        elif not item.memory_access_permitted:
            action, reason, planned = ContextAction.EXCLUDE_LOW_CONFIDENCE, "memory_access_denied", 0
        elif item.stale:
            action, reason, planned = ContextAction.EXCLUDE_STALE, "stale_context", 0
        elif item.confidence < minimum_confidence:
            action, reason, planned = ContextAction.EXCLUDE_LOW_CONFIDENCE, "confidence_below_policy", 0
        elif used + item.units <= maximum_units:
            action, reason, planned = ContextAction.REUSE, "valid_existing_context", item.units
        else:
            remaining = max(0, maximum_units - used)
            action, reason, planned = ContextAction.COMPRESS, "context_limit", remaining
        if planned:
            included.append(item.reference)
            used += planned
        else:
            excluded.append(item.reference)
        decisions.append(ContextDecision(reference=item.reference, action=action, reason=reason, original_units=item.units, planned_units=planned, estimated_units_saved=item.units - planned))
    required_units = sum(i.units for i in ordered if i.governance_required)
    valid = required_units <= maximum_units
    return ContextPlan(decisions=tuple(decisions), included_references=tuple(included), excluded_references=tuple(excluded), total_input_units=sum(i.units for i in ordered), planned_units=used, estimated_units_saved=sum(d.estimated_units_saved for d in decisions), valid=valid, reason_codes=("context_plan_valid",) if valid else ("required_governance_context_exceeds_limit",))
