"""Versioned contracts and sanitization for governed AI analysis."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum

from .models import (
    PROHIBITED_RESPONSIBILITIES,
    Capability,
    CostClass,
    ExecutionLocation,
    PrivacyTier,
    Responsibility,
    TrustTier,
    validate_identifier,
)
from .registry import canonical_digest

ANALYSIS_SCHEMA_VERSION = 1
MAX_ANALYSIS_OUTPUT_BYTES = 32_768
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE = (
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "cookie:",
    "password",
    "private_key",
    "private key",
    "secret=",
    "token=",
)
_EXECUTION_DIRECTIVES = (
    "submit order",
    "execute trade",
    "buy shares",
    "sell shares",
    "authorize capital",
    "approve proposal",
    "change policy",
    "mutate portfolio",
    "run shell",
)
ADVISORY_RESPONSIBILITIES = frozenset(
    {
        Responsibility.RESEARCH_ANALYSIS,
        Responsibility.PROPOSAL_SUPPORT,
        Responsibility.EVIDENCE_SUMMARIZATION,
        Responsibility.RISK_ANALYSIS,
        Responsibility.MARKET_CONTEXT,
        Responsibility.ORCHESTRATION_SUPPORT,
    }
)


class AnalysisValidationError(ValueError):
    """Analysis input or output violated the governed contract."""


class GovernedOutputSchema(str, Enum):
    GENERIC_ANALYSIS_V1 = "sigil.ai.output.generic-analysis.v1"


@dataclass(frozen=True, slots=True)
class GovernedAnalysisRequest:
    request_id: str
    task_correlation_id: str
    requested_capability: Capability
    responsibility: Responsibility
    privacy_requirement: PrivacyTier
    maximum_cost_class: CostClass
    minimum_trust_tier: TrustTier
    execution_location_preference: tuple[ExecutionLocation, ...]
    fallback_permission: bool
    timeout_ms: int
    input_digest: str
    evidence_context_digests: tuple[str, ...]
    expected_output_schema: GovernedOutputSchema
    requested_at: str
    allowed_provider_ids: frozenset[str] | None = None
    schema_version: int = ANALYSIS_SCHEMA_VERSION
    paper_only: bool = True
    broker_submission: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != ANALYSIS_SCHEMA_VERSION:
            raise AnalysisValidationError("unsupported analysis request schema")
        validate_identifier(self.request_id, "request_id")
        validate_identifier(self.task_correlation_id, "task_correlation_id")
        if self.responsibility in PROHIBITED_RESPONSIBILITIES:
            raise AnalysisValidationError("analysis responsibility is prohibited")
        if self.responsibility not in ADVISORY_RESPONSIBILITIES:
            raise AnalysisValidationError("analysis responsibility must be advisory-only")
        if _SHA256.fullmatch(self.input_digest) is None:
            raise AnalysisValidationError("analysis input must be a SHA-256 digest")
        if not self.evidence_context_digests or any(
            _SHA256.fullmatch(item) is None for item in self.evidence_context_digests
        ):
            raise AnalysisValidationError("analysis evidence context must contain trusted digests")
        if len(self.evidence_context_digests) > 64:
            raise AnalysisValidationError("analysis evidence context exceeds its bound")
        if not self.execution_location_preference:
            raise AnalysisValidationError("analysis location preference cannot be empty")
        if not 100 <= self.timeout_ms <= 300_000:
            raise AnalysisValidationError("analysis timeout is outside its governed bound")
        if not self.requested_at:
            raise AnalysisValidationError("analysis request timestamp cannot be blank")
        if self.allowed_provider_ids is not None:
            if not self.allowed_provider_ids:
                raise AnalysisValidationError("allowed_provider_ids cannot be empty")
            try:
                for provider_id in sorted(self.allowed_provider_ids):
                    validate_identifier(provider_id, "allowed_provider_ids")
            except ValueError as error:
                raise AnalysisValidationError(str(error)) from error
        if self.paper_only is not True or self.broker_submission is not False:
            raise AnalysisValidationError("analysis request cannot receive execution authority")


@dataclass(frozen=True, slots=True)
class GenericAnalysisPayload:
    summary: str
    findings: tuple[str, ...]
    risks: tuple[str, ...]
    evidence_references: tuple[str, ...]
    limitations: tuple[str, ...]
    confidence: float | None


@dataclass(frozen=True, slots=True)
class GovernedAnalysisArtifact:
    artifact_id: str
    request_id: str
    task_correlation_id: str
    provider_id: str
    model_id: str
    model_version: str
    capability: Capability
    responsibility: Responsibility
    created_at: str
    routing_evidence_id: str
    invocation_evidence_id: str
    input_digest: str
    output_digest: str
    structured_payload: GenericAnalysisPayload
    citations: tuple[str, ...]
    confidence: float | None
    limitations: tuple[str, ...]
    stale_after: str | None = None
    schema_version: int = ANALYSIS_SCHEMA_VERSION
    paper_only: bool = True
    execution_authorized: bool = False
    broker_submission: bool = False
    portfolio_mutation: bool = False
    approval_authority: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != ANALYSIS_SCHEMA_VERSION:
            raise AnalysisValidationError("unsupported analysis artifact schema")
        if re.fullmatch(r"analysis-artifact-[0-9a-f]{64}", self.artifact_id) is None:
            raise AnalysisValidationError("analysis artifact identity is invalid")
        for value in (
            self.routing_evidence_id,
            self.invocation_evidence_id,
            self.input_digest,
            self.output_digest,
            *self.citations,
        ):
            if _SHA256.fullmatch(value) is None:
                raise AnalysisValidationError(
                    "artifact evidence references must be SHA-256 digests"
                )
        if any(
            (
                self.paper_only is not True,
                self.execution_authorized is not False,
                self.broker_submission is not False,
                self.portfolio_mutation is not False,
                self.approval_authority is not False,
            )
        ):
            raise AnalysisValidationError("analysis artifact cannot carry execution authority")
        validated = validate_generic_analysis(
            {
                "summary": self.structured_payload.summary,
                "findings": list(self.structured_payload.findings),
                "risks": list(self.structured_payload.risks),
                "evidence_references": list(self.structured_payload.evidence_references),
                "limitations": list(self.structured_payload.limitations),
                "confidence": self.structured_payload.confidence,
            },
            trusted_evidence=self.citations,
        )
        if (
            validated != self.structured_payload
            or self.citations != self.structured_payload.evidence_references
            or self.confidence != self.structured_payload.confidence
            or self.limitations != self.structured_payload.limitations
        ):
            raise AnalysisValidationError("artifact fields contradict structured payload")


def validate_generic_analysis(
    output: object, *, trusted_evidence: tuple[str, ...]
) -> GenericAnalysisPayload:
    if not isinstance(output, dict):
        raise AnalysisValidationError("provider output must be a structured object")
    encoded = json.dumps(output, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > MAX_ANALYSIS_OUTPUT_BYTES:
        raise AnalysisValidationError("provider output exceeds the governed size bound")
    required = {"summary", "findings", "risks", "evidence_references", "limitations", "confidence"}
    if set(output) != required:
        raise AnalysisValidationError("provider output does not match the generic analysis schema")
    summary = output["summary"]
    findings = output["findings"]
    risks = output["risks"]
    references = output["evidence_references"]
    limitations = output["limitations"]
    confidence = output["confidence"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 4_096:
        raise AnalysisValidationError("analysis summary is invalid")
    for name, value in (("findings", findings), ("risks", risks), ("limitations", limitations)):
        if (
            not isinstance(value, list)
            or len(value) > 32
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 2_048 for item in value
            )
        ):
            raise AnalysisValidationError(f"analysis {name} are invalid")
    if (
        not isinstance(references, list)
        or not references
        or any(not isinstance(item, str) or item not in trusted_evidence for item in references)
    ):
        raise AnalysisValidationError("analysis contains untrusted evidence references")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise AnalysisValidationError("analysis confidence is invalid")
    lowered = encoded.lower()
    if any(marker in lowered for marker in _SENSITIVE):
        raise AnalysisValidationError("analysis output contains credential material")
    if any(marker in lowered for marker in _EXECUTION_DIRECTIVES):
        raise AnalysisValidationError("analysis output contains an execution instruction")
    return GenericAnalysisPayload(
        summary=summary.strip(),
        findings=tuple(item.strip() for item in findings),
        risks=tuple(item.strip() for item in risks),
        evidence_references=tuple(references),
        limitations=tuple(item.strip() for item in limitations),
        confidence=None if confidence is None else float(confidence),
    )


def build_analysis_artifact(**values: object) -> GovernedAnalysisArtifact:
    unsigned = {**values, "artifact_id": "pending", "schema_version": ANALYSIS_SCHEMA_VERSION}
    identity_payload = {
        **unsigned,
        "structured_payload": asdict(unsigned["structured_payload"]),
    }
    artifact_id = f"analysis-artifact-{canonical_digest(identity_payload)}"
    return GovernedAnalysisArtifact(**{**unsigned, "artifact_id": artifact_id})
