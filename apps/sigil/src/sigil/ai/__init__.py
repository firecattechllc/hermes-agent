"""Governed, model-neutral AI provider foundation for Sigil."""

from .evidence import InvocationEvidence, build_invocation_evidence
from .gemma import (
    GemmaConfigurationError,
    GemmaHealth,
    GemmaTransportError,
    GemmaTransportFailure,
    LocalGemmaConfig,
    LocalGemmaProvider,
)
from .handoff import GovernedModelWorkRequest
from .ledger import (
    AIEvidenceConflictError,
    AIEvidenceCorruptionError,
    AIEvidenceLedgerError,
    AIEvidenceRecordType,
    DurableAIEvidenceLedger,
    GovernedAIEvidenceRecord,
    append_routing_decision,
)
from .models import (
    Capability,
    CostClass,
    ExecutionLocation,
    InputType,
    ModelRegistration,
    PrivacyTier,
    ProviderHealth,
    ProviderIdentity,
    Responsibility,
    TrustTier,
)
from .provider import (
    DeterministicProvider,
    DeterministicProviderMode,
    ProviderFailure,
    ProviderFailureClass,
    ProviderInvocation,
    ProviderResult,
)
from .registry import GovernedModelRegistry, RegistryValidationError
from .routing import (
    CandidateEvaluation,
    GovernedModelRouter,
    RoutingDecision,
    RoutingFailureClass,
    RoutingRequest,
)

__all__ = [
    "AIEvidenceConflictError",
    "AIEvidenceCorruptionError",
    "AIEvidenceLedgerError",
    "AIEvidenceRecordType",
    "CandidateEvaluation",
    "Capability",
    "CostClass",
    "DeterministicProvider",
    "DeterministicProviderMode",
    "DurableAIEvidenceLedger",
    "ExecutionLocation",
    "GemmaConfigurationError",
    "GemmaHealth",
    "GemmaTransportError",
    "GemmaTransportFailure",
    "GovernedAIEvidenceRecord",
    "GovernedModelRegistry",
    "GovernedModelRouter",
    "GovernedModelWorkRequest",
    "InputType",
    "InvocationEvidence",
    "LocalGemmaConfig",
    "LocalGemmaProvider",
    "ModelRegistration",
    "PrivacyTier",
    "ProviderFailure",
    "ProviderFailureClass",
    "ProviderHealth",
    "ProviderIdentity",
    "ProviderInvocation",
    "ProviderResult",
    "RegistryValidationError",
    "Responsibility",
    "RoutingDecision",
    "RoutingFailureClass",
    "RoutingRequest",
    "TrustTier",
    "append_routing_decision",
    "build_invocation_evidence",
]
