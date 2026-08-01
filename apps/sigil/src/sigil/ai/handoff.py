"""Narrow governed request boundary for future Hermes orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Capability, CostClass, ExecutionLocation, PrivacyTier, Responsibility, TrustTier
from .routing import RoutingRequest

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class GovernedModelWorkRequest:
    request_id: str
    task_correlation_id: str
    evidence_correlation_id: str
    capability: Capability
    responsibility: Responsibility
    privacy_requirement: PrivacyTier
    evidence_context: tuple[str, ...]
    expected_output_contract: str
    timeout_ms: int = 30_000
    preferred_model_family: str | None = "gemma"
    fallback_allowed: bool = True
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if self.paper_only is not True or self.broker_submission is not False:
            raise ValueError("governed model work cannot receive execution authority")
        if not self.expected_output_contract.startswith("sigil.ai.output."):
            raise ValueError("expected output contract must be a governed Sigil contract")
        if any(_SHA256.fullmatch(item) is None for item in self.evidence_context):
            raise ValueError("Hermes evidence context must contain digest references only")

    def routing_request(self) -> RoutingRequest:
        return RoutingRequest(
            request_id=self.request_id,
            task_correlation_id=self.task_correlation_id,
            evidence_correlation_id=self.evidence_correlation_id,
            responsibility=self.responsibility,
            required_capabilities=frozenset({self.capability}),
            preferred_model_family=self.preferred_model_family,
            privacy_requirement=self.privacy_requirement,
            maximum_cost_class=CostClass.STANDARD,
            execution_location_preference=(
                ExecutionLocation.LOCAL,
                ExecutionLocation.FLEET,
                ExecutionLocation.EXTERNAL,
            ),
            minimum_trust_tier=TrustTier.RESTRICTED,
            timeout_ms=self.timeout_ms,
            fallback_allowed=self.fallback_allowed,
        )


@dataclass(frozen=True, slots=True)
class GovernedSentimentWorkRequest:
    """Hermes handoff containing references only; source text stays backend-local."""

    request_id: str
    task_correlation_id: str
    source_identity: str
    source_digest: str
    source_type: str
    privacy_requirement: PrivacyTier
    evidence_references: tuple[str, ...]
    responsibility: Responsibility
    expected_output_contract: str = "sigil.ai.output.financial-sentiment.v1"
    timeout_ms: int = 15_000
    fallback_allowed: bool = False
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if self.paper_only is not True or self.broker_submission is not False:
            raise ValueError("governed sentiment handoff cannot receive execution authority")
        if _SHA256.fullmatch(self.source_digest) is None or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_references
        ):
            raise ValueError("sentiment handoff requires digest references")
        if self.expected_output_contract != "sigil.ai.output.financial-sentiment.v1":
            raise ValueError("sentiment handoff output contract is invalid")
