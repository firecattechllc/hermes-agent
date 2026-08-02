from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigil.ai import (
    AIEvidenceRecordType,
    Capability,
    DurableAIEvidenceLedger,
    DurableAnalysisArtifactStore,
    ExecutionLocation,
    GenericAnalysisPayload,
    GovernedAIEvidenceRecord,
    Responsibility,
    build_analysis_artifact,
)
from sigil.ai.inspection import (
    AIInspectionValidationError,
    ai_artifact_get,
    ai_recent_artifacts,
    ai_status,
)
from sigil.desktop_bridge.runner import SUPPORTED_COMMANDS, handle_request

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def environment(tmp_path: Path, *, enabled: bool = False) -> dict[str, str]:
    return {
        "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
        "SIGIL_AI_SERVICE_ENABLED": "true" if enabled else "false",
    }


def artifact():
    payload = GenericAnalysisPayload(
        summary="Sanitized analysis",
        findings=("Finding",),
        risks=("Risk",),
        evidence_references=(DIGEST_B,),
        limitations=("Advisory only",),
        confidence=0.8,
    )
    return build_analysis_artifact(
        request_id="inspection-request",
        task_correlation_id="inspection-task",
        provider_id="local-runtime",
        model_id="gemma-analysis",
        model_version="1.0.0",
        capability=Capability.REASONING,
        responsibility=Responsibility.RESEARCH_ANALYSIS,
        created_at="2026-08-01T17:00:00Z",
        routing_evidence_id=DIGEST_A,
        invocation_evidence_id=DIGEST_B,
        input_digest=DIGEST_A,
        output_digest=DIGEST_B,
        structured_payload=payload,
        citations=(DIGEST_B,),
        confidence=0.8,
        limitations=("Advisory only",),
        stale_after=None,
    )


def evidence(*, failed: bool = False) -> GovernedAIEvidenceRecord:
    return GovernedAIEvidenceRecord(
        evidence_identity="ai-evidence-" + ("f" if failed else "e") * 64,
        record_type=(
            AIEvidenceRecordType.PROVIDER_RESULT_FAILED
            if failed
            else AIEvidenceRecordType.PROVIDER_RESULT_SUCCEEDED
        ),
        request_id="inspection-request",
        task_correlation_id="inspection-task",
        provider_id="local-runtime",
        model_id="gemma-analysis",
        model_version="1.0.0",
        registry_revision=DIGEST_A,
        capability=Capability.REASONING,
        execution_location=ExecutionLocation.LOCAL,
        routing_status="selected",
        fallback=False,
        started_at="2026-08-01T17:00:00Z",
        ended_at="2026-08-01T17:00:01Z",
        succeeded=not failed,
        failure_classification="timeout" if failed else None,
        input_digest=DIGEST_A,
        output_digest=None if failed else DIGEST_B,
    )


def test_status_disabled_empty_and_authority_free(tmp_path: Path) -> None:
    status = ai_status(environment(tmp_path))
    assert status["enabled"] is False
    assert status["evidence_ledger_health"] == "empty"
    assert status["artifact_store_health"] == "empty"
    assert status["broker_submission"] is False
    assert status["execution_authorized"] is False
    assert status["portfolio_mutation"] is False
    assert status["approval_authority"] is False
    assert status["secrets_exposed"] is False


def test_status_enabled_with_valid_evidence_and_artifact(tmp_path: Path) -> None:
    DurableAIEvidenceLedger(tmp_path.resolve()).append(evidence(failed=True))
    DurableAnalysisArtifactStore(tmp_path.resolve()).append(artifact())
    status = ai_status(environment(tmp_path, enabled=True))
    assert status["enabled"] is True
    assert status["evidence_record_count"] == 1
    assert status["artifact_count"] == 1
    assert status["last_failure_classification"] == "timeout"
    assert status["last_successful_analysis_at"] == "2026-08-01T17:00:00Z"
    assert status["latest_analysis_summary"] == "Sanitized analysis"


def test_artifact_summary_exact_lookup_not_found_and_malformed(tmp_path: Path) -> None:
    stored = artifact()
    DurableAnalysisArtifactStore(tmp_path.resolve()).append(stored)
    env = environment(tmp_path)
    recent = ai_recent_artifacts({"limit": 1}, env)
    assert recent["artifacts"][0]["artifact_id"] == stored.artifact_id
    exact = ai_artifact_get({"artifact_id": stored.artifact_id}, env)
    assert exact["found"] is True
    assert exact["artifact"]["structured_payload"]["summary"] == "Sanitized analysis"
    missing = ai_artifact_get({"artifact_id": "analysis-artifact-" + "0" * 64}, env)
    assert missing["found"] is False
    with pytest.raises(AIInspectionValidationError):
        ai_artifact_get({"artifact_id": "bad"}, env)


def test_exact_artifact_lookup_revalidates_corrupt_storage(tmp_path: Path) -> None:
    store = DurableAnalysisArtifactStore(tmp_path.resolve())
    store.path.write_text('{"artifact":', encoding="utf-8")
    result = ai_artifact_get(
        {"artifact_id": "analysis-artifact-" + "0" * 64}, environment(tmp_path)
    )
    assert result == {
        "schema_version": 1,
        "found": False,
        "artifact": None,
        "health": "recoverable_tail",
    }


@pytest.mark.parametrize("limit", [0, 51, True, "10"])
def test_inspection_limits_are_bounded(tmp_path: Path, limit) -> None:
    with pytest.raises(AIInspectionValidationError):
        ai_recent_artifacts({"limit": limit}, environment(tmp_path))


def test_bridge_commands_are_allowlisted_read_only_and_sanitized(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path.resolve()))
    monkeypatch.setenv("SIGIL_AI_GEMMA_ENDPOINT", "http://user:secret@127.0.0.1:11434")
    for command in (
        "ai_status",
        "ai_registry_status",
        "ai_evidence_status",
        "ai_artifact_status",
        "ai_recent_artifacts",
        "ai_recent_failures",
    ):
        assert command in SUPPORTED_COMMANDS
        response = handle_request({"command": command, "payload": {"limit": 5}})
        assert response["ok"] is True
        serialized = json.dumps(response).lower()
        assert "user:secret" not in serialized
        assert "raw prompt" not in serialized
        assert "raw_output" not in serialized
        assert '"broker_submission": true' not in serialized

    unknown = handle_request({"command": "ai_invoke", "payload": {"prompt": "trade"}})
    assert unknown["ok"] is False


def test_artifact_get_bridge_rejects_arbitrary_input_and_never_invokes_model(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SIGIL_DESKTOP_STATE_DIR", str(tmp_path.resolve()))
    monkeypatch.setattr(
        "sigil.ai.gemma.LocalGemmaProvider.invoke",
        lambda *_args, **_kwargs: pytest.fail("inspection invoked a model"),
    )
    response = handle_request({"command": "ai_artifact_get", "payload": {"artifact_id": "bad"}})
    assert response["ok"] is False
    status = handle_request({"command": "ai_status"})
    assert status["ok"] is True
