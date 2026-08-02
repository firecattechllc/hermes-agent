from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import sleep

import pytest

from sigil.ai import (
    ORCHESTRATION_WORKFLOW,
    Capability,
    CostClass,
    DurableAnalysisArtifactStore,
    DurableOrchestrationStore,
    GovernedAtlasProjection,
    GovernedBuzzGateway,
    GovernedOpenWorker,
    GovernedOrchestrationArtifact,
    GovernedOrchestrationRequest,
    GovernedOrchestrationService,
    GovernedOrchestrationWorkRequest,
    GovernedStepResult,
    GovernedWorkerRequest,
    OrchestrationState,
    OrchestrationStepStatus,
    OrchestrationStoreConflictError,
    OrchestrationStoreError,
    OrchestrationValidationError,
    PrivacyTier,
    ProviderHealth,
    Responsibility,
    TrustTier,
    WorkerRegistration,
    WorkerTaskType,
    build_orchestration_plan,
    orchestration_execution_batches,
)
from sigil.ai.inspection import ai_artifact_get, ai_status

DIGEST = "sha256:" + "a" * 64
REGISTRY = "sha256:" + "b" * 64


def request(**values) -> GovernedOrchestrationRequest:
    return GovernedOrchestrationRequest(
        **{
            "orchestration_id": "orchestration-research-001",
            "task_correlation_id": "research-task-001",
            "workflow_type": ORCHESTRATION_WORKFLOW,
            "objective": "Synthesize governed research evidence for operator review.",
            "allowed_capabilities": frozenset(
                {
                    Capability.SEMANTIC_RETRIEVAL,
                    Capability.FINANCIAL_SENTIMENT,
                    Capability.TIME_SERIES_FORECASTING,
                    Capability.REASONING,
                }
            ),
            "allowed_responsibilities": frozenset(
                {
                    Responsibility.RESEARCH_RETRIEVAL,
                    Responsibility.FINANCIAL_SENTIMENT_ANALYSIS,
                    Responsibility.MARKET_FORECASTING,
                    Responsibility.RESEARCH_ANALYSIS,
                }
            ),
            "required_evidence_digests": (DIGEST,),
            "privacy_requirement": PrivacyTier.LOCAL_ONLY,
            "trust_requirement": TrustTier.TRUSTED,
            "cost_ceiling": CostClass.FREE,
            "timeout_ms": 5_000,
            "maximum_steps": 4,
            "maximum_parallelism": 1,
            "fallback_permission": False,
            "human_approval_requirement": False,
            "requested_at": "2026-08-01T18:00:00+00:00",
            **values,
        }
    )


class FakeSpecialists:
    def __init__(self, failures=None) -> None:
        self.failures = dict(failures or {})
        self.calls: list[tuple[Capability, int]] = []

    def execute(self, step, _request, *, attempt, completed_at):
        self.calls.append((step.capability, attempt))
        failure = self.failures.get(step.capability)
        if isinstance(failure, list):
            failure = failure[min(attempt - 1, len(failure) - 1)]
        if failure:
            return GovernedStepResult(
                result_id=f"step-result-{step.ordinal}-{attempt}",
                step_id=step.step_id,
                status=OrchestrationStepStatus.FAILED,
                artifact_id=None,
                evidence_identities=(f"sha256:{step.ordinal:064x}",),
                findings=(),
                risks=(),
                disagreements=(),
                missing_evidence=(step.capability.value,),
                limitations=("Capability failed safely.",),
                confidence=None,
                freshness="unknown",
                failure_classification=failure,
                retryable=failure in {"timeout", "provider_unavailable"},
                fallback_used=False,
                attempts=attempt,
                completed_at=completed_at,
            )
        return GovernedStepResult(
            result_id=f"step-result-{step.ordinal}-{attempt}",
            step_id=step.step_id,
            status=OrchestrationStepStatus.SUCCEEDED,
            artifact_id=f"analysis-artifact-{step.ordinal:064x}",
            evidence_identities=(f"sha256:{step.ordinal:064x}",),
            findings=(f"finding-{step.ordinal}",),
            risks=(f"risk-{step.ordinal}",),
            disagreements=("sentiment and forecast disagree",)
            if step.capability == Capability.REASONING
            else (),
            missing_evidence=(),
            limitations=("Advisory specialist output only.",),
            confidence=0.8,
            freshness="current",
            failure_classification=None,
            retryable=False,
            fallback_used=False,
            attempts=attempt,
            completed_at=completed_at,
        )


def service(tmp_path: Path, executor=None, *, enabled=True):
    store = DurableOrchestrationStore(tmp_path.resolve())
    artifacts = DurableAnalysisArtifactStore(tmp_path.resolve())
    specialists = executor or FakeSpecialists()
    governed = GovernedOrchestrationService(
        store=store,
        artifact_store=artifacts,
        specialist_executor=specialists,
        registry_revision=REGISTRY,
        enabled=enabled,
    )
    return governed, specialists, store, artifacts


def test_valid_request_and_deterministic_dependency_plan() -> None:
    value = request(maximum_parallelism=2)
    first = build_orchestration_plan(
        value, registry_revision=REGISTRY, created_at=value.requested_at
    )
    second = build_orchestration_plan(
        value, registry_revision=REGISTRY, created_at=value.requested_at
    )
    assert first == second
    assert first.plan_id == second.plan_id
    assert tuple(item.ordinal for item in first.steps) == (1, 2, 3, 4)
    assert first.steps[-1].dependencies == tuple(item.step_id for item in first.steps[:3])
    assert first.maximum_parallelism == 2


def test_execution_batches_are_deterministic_dependency_safe_and_bounded() -> None:
    plan = build_orchestration_plan(
        request(), registry_revision=REGISTRY, created_at="2026-08-01T18:00:00+00:00"
    )
    batches = orchestration_execution_batches(plan)
    assert batches == orchestration_execution_batches(plan)
    assert all(len(batch) <= plan.maximum_parallelism for batch in batches)
    completed: set[str] = set()
    for batch in batches:
        assert all(set(step.dependencies) <= completed for step in batch)
        completed.update(step.step_id for step in batch)
    assert completed == {step.step_id for step in plan.steps}


@pytest.mark.parametrize(
    "change",
    (
        {"objective": "Submit broker orders automatically."},
        {"allowed_responsibilities": frozenset({Responsibility.ORDER_EXECUTION})},
        {"maximum_steps": 9},
        {"maximum_parallelism": 3},
        {"timeout_ms": 99},
        {"workflow_type": "generic-autonomous-loop"},
    ),
)
def test_request_rejects_authority_and_unbounded_values(change) -> None:
    with pytest.raises(OrchestrationValidationError):
        request(**change)


def test_full_retrieval_sentiment_forecast_synthesis_workflow(tmp_path: Path) -> None:
    governed, specialists, store, artifacts = service(tmp_path)
    response = governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    assert response.terminal_status == OrchestrationState.COMPLETED
    assert [item[0] for item in specialists.calls] == [
        Capability.SEMANTIC_RETRIEVAL,
        Capability.FINANCIAL_SENTIMENT,
        Capability.TIME_SERIES_FORECASTING,
        Capability.REASONING,
    ]
    artifact = artifacts.read_artifacts()[-1]
    assert isinstance(artifact, GovernedOrchestrationArtifact)
    assert len(artifact.retrieval_artifact_ids) == 1
    assert len(artifact.sentiment_artifact_ids) == 1
    assert len(artifact.forecast_artifact_ids) == 1
    assert artifact.synthesis_artifact_id is not None
    assert artifact.disagreements == ("sentiment and forecast disagree",)
    assert store.latest(response.orchestration_id).state == OrchestrationState.COMPLETED


@pytest.mark.parametrize(
    "capability",
    (
        Capability.SEMANTIC_RETRIEVAL,
        Capability.FINANCIAL_SENTIMENT,
        Capability.TIME_SERIES_FORECASTING,
        Capability.REASONING,
    ),
)
def test_missing_specialist_fallback_partial_or_fail_closed(tmp_path: Path, capability) -> None:
    root = tmp_path / capability.name.lower()
    root.mkdir()
    failed, _, _, artifacts = service(root, FakeSpecialists({capability: "provider_unavailable"}))
    response = failed.run(
        request(orchestration_id=f"orchestration-fail-{capability.name.lower()}"),
        completed_at="2026-08-01T18:00:05+00:00",
    )
    assert response.terminal_status == OrchestrationState.FAILED
    assert artifacts.read_artifacts() == ()

    fallback_root = root / "fallback"
    fallback_root.mkdir()
    partial, _, partial_store, partial_artifacts = service(
        fallback_root, FakeSpecialists({capability: "provider_unavailable"})
    )
    response = partial.run(
        request(
            orchestration_id=f"orchestration-partial-{capability.name.lower()}",
            fallback_permission=True,
        ),
        completed_at="2026-08-01T18:00:05+00:00",
    )
    assert response.terminal_status == OrchestrationState.PARTIAL
    assert capability.value in response.missing_capabilities
    assert len(partial_artifacts.read_artifacts()) == 1
    assert "fallback_applied" in {
        item.event_type for item in partial_store.latest(response.orchestration_id).evidence
    }


def test_retry_is_bounded_to_transient_failure(tmp_path: Path) -> None:
    specialists = FakeSpecialists({Capability.SEMANTIC_RETRIEVAL: ["timeout", None]})
    governed, _, store, _ = service(tmp_path, specialists)
    response = governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    assert response.terminal_status == OrchestrationState.COMPLETED
    assert specialists.calls[:2] == [
        (Capability.SEMANTIC_RETRIEVAL, 1),
        (Capability.SEMANTIC_RETRIEVAL, 2),
    ]
    assert "step_retried" in {
        item.event_type for item in store.latest(response.orchestration_id).evidence
    }


def test_nonretryable_unsafe_failure_stops_without_second_attempt(tmp_path: Path) -> None:
    specialists = FakeSpecialists({Capability.FINANCIAL_SENTIMENT: "unsafe_output"})
    governed, _, _, artifacts = service(tmp_path, specialists)
    response = governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    assert response.terminal_status == OrchestrationState.FAILED
    assert specialists.calls.count((Capability.FINANCIAL_SENTIMENT, 1)) == 1
    assert (Capability.FINANCIAL_SENTIMENT, 2) not in specialists.calls
    assert artifacts.read_artifacts() == ()


def test_duplicate_terminal_immutability_restart_and_truncated_recovery(tmp_path: Path) -> None:
    governed, _, store, _ = service(tmp_path)
    governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    restarted = DurableOrchestrationStore(tmp_path.resolve())
    assert restarted.latest(request().orchestration_id).state == OrchestrationState.COMPLETED
    with pytest.raises(OrchestrationStoreConflictError):
        governed.run(request(), completed_at="2026-08-01T18:00:06+00:00")
    with pytest.raises(OrchestrationStoreConflictError):
        restarted.append(replace(restarted.latest(request().orchestration_id), revision=4))
    with store.path.open("ab") as stream:
        stream.write(b'{"truncated":')
    with pytest.raises(OrchestrationStoreError):
        store.read_records(recover_truncated_tail=False)
    assert store.read_records(recover_truncated_tail=True)[-1].state == OrchestrationState.COMPLETED


def test_corrupt_and_unsupported_state_fail_closed(tmp_path: Path) -> None:
    governed, _, store, _ = service(tmp_path)
    governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    data = store.path.read_text()
    store.path.write_text(data.replace('"store_version":1', '"store_version":2', 1))
    with pytest.raises(OrchestrationStoreError):
        store.read_records(recover_truncated_tail=False)


def test_human_interaction_pauses_survives_restart_and_exact_response(tmp_path: Path) -> None:
    governed, _, _store, _ = service(tmp_path)
    response = governed.run(
        request(human_approval_requirement=True), completed_at="2026-08-01T18:00:05+00:00"
    )
    assert response.terminal_status == OrchestrationState.PAUSED
    restarted = DurableOrchestrationStore(tmp_path.resolve())
    interaction = restarted.latest(request().orchestration_id).interactions[0]
    assert "human_interaction_requested" in {
        item.event_type for item in restarted.latest(request().orchestration_id).evidence
    }
    updated = governed.respond_to_interaction(
        request().orchestration_id,
        interaction.interaction_id,
        "proceed",
        responded_at="2026-08-01T18:00:06+00:00",
    )
    assert updated.interactions[0].response == "proceed"
    with pytest.raises(OrchestrationValidationError):
        governed.respond_to_interaction(
            request().orchestration_id,
            interaction.interaction_id,
            "proceed",
            responded_at="2026-08-01T18:00:07+00:00",
        )


def test_expired_interaction_and_cancellation_are_safe(tmp_path: Path) -> None:
    governed, _, _, _ = service(tmp_path)
    governed.run(request(human_approval_requirement=True), completed_at="2026-08-01T18:00:05+00:00")
    interaction = governed.store.latest(request().orchestration_id).interactions[0]
    with pytest.raises(OrchestrationValidationError):
        governed.respond_to_interaction(
            request().orchestration_id,
            interaction.interaction_id,
            "skip",
            responded_at="2026-08-01T20:00:00+00:00",
        )
    cancelled = governed.cancel(
        request().orchestration_id, cancelled_at="2026-08-01T18:10:00+00:00"
    )
    assert cancelled.state == OrchestrationState.CANCELLED
    assert cancelled.paper_only is True and cancelled.broker_submission is False


def test_buzz_is_optional_sanitized_and_not_a_command_gateway() -> None:
    offline = GovernedBuzzGateway()
    identity = offline.deliver(
        orchestration_id="orchestration-research-001",
        message_type="status",
        content="Research is paused.",
    )
    assert identity.startswith("buzz-message-") and offline.messages() == ()
    online = GovernedBuzzGateway(available=True)
    online.deliver(
        orchestration_id="orchestration-research-001",
        message_type="result_summary",
        content="Advisory result ready.",
    )
    assert len(online.messages()) == 1
    with pytest.raises(OrchestrationValidationError):
        online.deliver(
            orchestration_id="orchestration-research-001",
            message_type="command",
            content="run shell",
        )
    with pytest.raises(OrchestrationValidationError):
        online.deliver(
            orchestration_id="orchestration-research-001",
            message_type="status",
            content="api_key=secret",
        )


def test_atlas_is_bounded_read_only_and_sanitized(tmp_path: Path) -> None:
    governed, _, store, _ = service(tmp_path)
    governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    atlas = GovernedAtlasProjection(store, available=True)
    projection = atlas.exact(request().orchestration_id)
    assert projection["state"] == "completed"
    assert projection["paper_only"] is True
    assert "objective" not in projection and "prompt" not in str(projection).lower()
    assert not hasattr(atlas, "write") and not hasattr(atlas, "update")
    with pytest.raises(OrchestrationValidationError):
        atlas.recent(51)


def worker_registration(**values) -> WorkerRegistration:
    return WorkerRegistration(
        **{
            "worker_id": "local-openworker",
            "worker_type": "deterministic-transformer",
            "supported_task_types": frozenset({WorkerTaskType.DOCUMENT_NORMALIZATION}),
            "execution_location": __import__(
                "sigil.ai", fromlist=["ExecutionLocation"]
            ).ExecutionLocation.LOCAL,
            "trust_tier": TrustTier.TRUSTED,
            "privacy_tier": PrivacyTier.LOCAL_ONLY,
            "timeout_ms": 1_000,
            "maximum_memory_mb": 64,
            "maximum_output_chars": 1_000,
            "enabled": True,
            "health": ProviderHealth.HEALTHY,
            **values,
        }
    )


def worker_request(**values) -> GovernedWorkerRequest:
    plan = build_orchestration_plan(
        request(), registry_revision=REGISTRY, created_at=request().requested_at
    )
    return GovernedWorkerRequest(
        **{
            "request_id": "worker-request-001",
            "orchestration_id": request().orchestration_id,
            "step_id": plan.steps[0].step_id,
            "task_type": WorkerTaskType.DOCUMENT_NORMALIZATION,
            "input_digests": (DIGEST,),
            "expected_output_schema": "sigil.worker.output.document-normalization.v1",
            "timeout_ms": 500,
            "maximum_memory_mb": 32,
            "maximum_output_chars": 500,
            "privacy_requirement": PrivacyTier.LOCAL_ONLY,
            "trust_requirement": TrustTier.TRUSTED,
            "requested_at": "2026-08-01T18:00:00+00:00",
            **values,
        }
    )


def test_openworker_allowlisted_success_and_no_authority() -> None:
    worker = GovernedOpenWorker(
        worker_registration(),
        {WorkerTaskType.DOCUMENT_NORMALIZATION: lambda digests: {"normalized_digest": digests[0]}},
    )
    result = worker.execute(worker_request(), completed_at="2026-08-01T18:00:01+00:00")
    assert result.succeeded
    assert result.execution_authorized is False and result.broker_submission is False
    assert result.output_digest is not None


def test_openworker_rejects_unknown_disabled_policy_timeout_and_recursion() -> None:
    with pytest.raises(OrchestrationValidationError):
        worker_registration(network_allowed=True)
    with pytest.raises(OrchestrationValidationError):
        worker_registration(shell_allowed=True)
    with pytest.raises(OrchestrationValidationError):
        worker_registration(filesystem_allowed=True)
    with pytest.raises(OrchestrationValidationError):
        worker_request(recursive=True)
    worker = GovernedOpenWorker(replace(worker_registration(), enabled=False), {})
    assert (
        worker.execute(
            worker_request(), completed_at="2026-08-01T18:00:01+00:00"
        ).failure_classification
        == "worker_unavailable"
    )
    unsupported = GovernedOpenWorker(worker_registration(), {})
    assert (
        unsupported.execute(
            worker_request(), completed_at="2026-08-01T18:00:01+00:00"
        ).failure_classification
        == "worker_task_unsupported"
    )
    bounded = GovernedOpenWorker(
        worker_registration(), {WorkerTaskType.DOCUMENT_NORMALIZATION: lambda _: {"value": "ok"}}
    )
    assert (
        bounded.execute(
            replace(worker_request(), timeout_ms=2_000), completed_at="2026-08-01T18:00:01+00:00"
        ).failure_classification
        == "worker_resource_limit"
    )
    assert (
        bounded.execute(
            replace(worker_request(), maximum_memory_mb=128),
            completed_at="2026-08-01T18:00:01+00:00",
        ).failure_classification
        == "worker_resource_limit"
    )


def test_openworker_rejects_oversized_and_credential_output() -> None:
    oversized = GovernedOpenWorker(
        worker_registration(),
        {WorkerTaskType.DOCUMENT_NORMALIZATION: lambda _: {"value": "x" * 600}},
    )
    assert (
        oversized.execute(
            worker_request(), completed_at="2026-08-01T18:00:01+00:00"
        ).failure_classification
        == "worker_output_oversized"
    )
    unsafe = GovernedOpenWorker(
        worker_registration(),
        {WorkerTaskType.DOCUMENT_NORMALIZATION: lambda _: {"api_key": "secret"}},
    )
    assert (
        unsafe.execute(
            worker_request(), completed_at="2026-08-01T18:00:01+00:00"
        ).failure_classification
        == "worker_output_unsafe"
    )


def test_openworker_classifies_actual_timeout_without_expanding_authority() -> None:
    worker = GovernedOpenWorker(
        worker_registration(timeout_ms=500),
        {WorkerTaskType.DOCUMENT_NORMALIZATION: lambda _: sleep(0.15) or {"value": "too late"}},
    )
    result = worker.execute(
        replace(worker_request(), timeout_ms=100),
        completed_at="2026-08-01T18:00:01+00:00",
    )
    assert result.failure_classification == "worker_timeout"
    assert result.structured_payload == ()
    assert result.broker_submission is False and result.portfolio_mutation is False


def test_hermes_handoff_success_failure_and_no_autonomous_expansion(tmp_path: Path) -> None:
    governed, _, _, _ = service(tmp_path)
    value = request()
    work = GovernedOrchestrationWorkRequest(
        value.orchestration_id,
        value.task_correlation_id,
        value.workflow_type,
        value.objective,
        value.required_evidence_digests,
        value.privacy_requirement,
        value.trust_requirement,
        tuple(value.allowed_capabilities),
        tuple(value.allowed_responsibilities),
        value.timeout_ms,
        value.maximum_steps,
        value.maximum_parallelism,
        value.fallback_permission,
        value.human_approval_requirement,
    )
    response = governed.run_hermes(
        work, requested_at=value.requested_at, completed_at="2026-08-01T18:00:05+00:00"
    )
    assert response.terminal_status == OrchestrationState.COMPLETED
    assert len(response.step_summaries) <= value.maximum_steps
    with pytest.raises(ValueError):
        replace(work, evidence_context_digests=("bad",))


def test_disabled_orchestration_is_startup_independent(tmp_path: Path) -> None:
    governed, specialists, store, artifacts = service(tmp_path, enabled=False)
    response = governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    assert response.failure_classification == "service_disabled"
    assert (
        specialists.calls == [] and store.read_records() == () and artifacts.read_artifacts() == ()
    )
    assert response.paper_only is True and response.broker_submission is False


def test_inspection_is_bounded_read_only_and_counts_latest_state(tmp_path: Path) -> None:
    governed, _, _, _ = service(tmp_path)
    completed = governed.run(request(), completed_at="2026-08-01T18:00:05+00:00")
    paused_request = request(
        orchestration_id="orchestration-research-paused",
        task_correlation_id="research-task-paused",
        human_approval_requirement=True,
        requested_at="2026-08-01T18:01:00+00:00",
    )
    governed.run(paused_request, completed_at="2026-08-01T18:01:05+00:00")
    environment = {
        "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
        "SIGIL_AI_ORCHESTRATION_ENABLED": "true",
        "SIGIL_AI_ATLAS_ENABLED": "true",
        "SECRET_TOKEN": "must-not-appear",
    }
    status = ai_status(environment)
    orchestration = status["orchestration"]
    assert orchestration["completed_count"] == 1
    assert orchestration["paused_count"] == 1
    assert orchestration["active_count"] == 0
    assert orchestration["pending_human_interactions"] == 1
    assert orchestration["atlas"] == "available"
    assert orchestration["buzz"] == orchestration["openworker"] == "unavailable"
    assert orchestration["paper_only"] is True
    assert orchestration["execution_authorized"] is False
    assert orchestration["broker_submission"] is False
    assert "must-not-appear" not in str(status)
    artifact = ai_artifact_get({"artifact_id": completed.artifact_id}, environment)
    assert artifact["found"] is True
    assert artifact["artifact"]["orchestration_id"] == request().orchestration_id
    assert "findings" not in artifact["artifact"]
