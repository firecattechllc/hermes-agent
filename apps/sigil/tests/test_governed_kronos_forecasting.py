from __future__ import annotations

import math
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sigil.ai import (
    Capability,
    CostClass,
    DeterministicProvider,
    DurableAIEvidenceLedger,
    DurableAnalysisArtifactStore,
    GovernedAnalysisService,
    GovernedForecastArtifact,
    GovernedForecastRequest,
    GovernedForecastWorkRequest,
    GovernedMarketBar,
    GovernedMarketSeries,
    GovernedModelRegistry,
    InputType,
    KronosConfig,
    KronosValidationError,
    LocalKronosProvider,
    ModelRegistration,
    PrivacyTier,
    ProviderInvocation,
    Responsibility,
    TrustTier,
    UncertaintyMode,
    compare_forecast_evaluations,
    evaluate_forecast,
    market_series_digest,
    validate_kronos_output,
)
from sigil.ai.inspection import ai_artifact_get, ai_status

DIGEST = "sha256:" + "a" * 64


def iso(day: int) -> str:
    return datetime(2026, 1, day, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def bars(count: int = 32, *, start: datetime | None = None) -> tuple[GovernedMarketBar, ...]:
    origin = start or datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        GovernedMarketBar(
            timestamp=(origin + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
            open=100.0 + index,
            high=102.0 + index,
            low=99.0 + index,
            close=101.0 + index,
            volume=1_000.0 + index,
        )
        for index in range(count)
    )


def series(**values) -> GovernedMarketSeries:
    values_bars = values.pop("bars", bars())
    return GovernedMarketSeries(
        **{
            "series_id": "aapl-daily-v1",
            "source_identity": "governed-market-evidence:AAPL",
            "source_digest": market_series_digest(values_bars),
            "symbol": "AAPL",
            "asset_class": "equity",
            "venue": "NASDAQ",
            "interval": "1d",
            "timezone": "UTC",
            "start_at": values_bars[0].timestamp,
            "end_at": values_bars[-1].timestamp,
            "observed_at": values_bars[-1].timestamp,
            "stale_after": "2026-03-01T00:00:00Z",
            "bars": values_bars,
            **values,
        }
    )


def request(**values) -> GovernedForecastRequest:
    item = values.pop("series", series())
    return GovernedForecastRequest(
        **{
            "request_id": "forecast-request",
            "task_correlation_id": "forecast-task",
            "responsibility": Responsibility.MARKET_FORECASTING,
            "series_id": item.series_id,
            "series_digest": item.source_digest,
            "symbol": item.symbol,
            "interval": item.interval,
            "forecast_horizon": 2,
            "uncertainty_mode": UncertaintyMode.NONE,
            "requested_quantiles": (),
            "privacy_requirement": PrivacyTier.LOCAL_ONLY,
            "minimum_trust_tier": TrustTier.TRUSTED,
            "fallback_permission": False,
            "timeout_ms": 1_000,
            "requested_at": "2026-02-02T00:00:00Z",
            "evidence_context_digests": (DIGEST,),
            **values,
        }
    )


class FakeKronosRuntime:
    def __init__(self, *, delay: float = 0, malformed: bool = False) -> None:
        self.delay = delay
        self.malformed = malformed
        self.calls = 0

    def forecast(self, *, bars, horizon, uncertainty_mode):
        self.calls += 1
        time.sleep(self.delay)
        last = datetime.fromisoformat(bars[-1]["timestamp"])
        points = []
        for index in range(1, horizon + 1):
            close = 132.0 + index
            point = {
                "horizon_index": index,
                "timestamp": (last + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1_100.0,
                "lower_close": None,
                "upper_close": None,
            }
            if uncertainty_mode == "quantiles":
                point["lower_close"] = close - 2
                point["upper_close"] = close + 2
            points.append(point)
        if self.malformed:
            points[0]["high"] = 1.0
        return points


def config(**values) -> KronosConfig:
    return KronosConfig(
        **{
            "enabled": True,
            "model_version": "test-v1",
            "tokenizer_version": "test-v1",
            "timeout_ms": 1_000,
            "max_sequence_length": 64,
            "min_sequence_length": 16,
            "max_horizon": 8,
            "allowed_intervals": ("1d",),
            **values,
        }
    )


def service(tmp_path: Path, runtime=None, *, enabled: bool = True):
    provider = LocalKronosProvider(config(), runtime or FakeKronosRuntime())
    registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    artifacts = DurableAnalysisArtifactStore(tmp_path.resolve())
    governed = GovernedAnalysisService(
        registry=registry,
        providers={provider.identity.provider_id: provider},
        evidence_ledger=ledger,
        artifact_store=artifacts,
        enabled=enabled,
    )
    return governed, provider, ledger, artifacts


def forecast(tmp_path: Path, *, runtime=None, series_value=None, request_value=None):
    governed, provider, ledger, artifacts = service(tmp_path, runtime)
    item = series_value or series()
    result = governed.forecast(
        request_value or request(series=item), series=item, completed_at="2026-02-02T00:00:01Z"
    )
    return result, governed, provider, ledger, artifacts


def test_config_is_disabled_cpu_local_only_and_bounded_by_default() -> None:
    value = KronosConfig.from_environment({})
    assert value.enabled is False
    assert value.device == "cpu"
    assert value.local_files_only is True
    assert value.max_horizon <= 256
    with pytest.raises(KronosValidationError):
        KronosConfig.from_environment({"SIGIL_AI_KRONOS_LOCAL_FILES_ONLY": "false"})


def test_registration_is_specialized_and_prohibitions_are_explicit() -> None:
    registration = LocalKronosProvider(config(), FakeKronosRuntime()).registration()
    assert registration.family == "kronos"
    assert registration.capabilities == frozenset({Capability.TIME_SERIES_FORECASTING})
    assert Responsibility.MARKET_FORECASTING in registration.allowed_responsibilities
    for responsibility in (
        Responsibility.CAPITAL_AUTHORIZATION,
        Responsibility.PROPOSAL_APPROVAL,
        Responsibility.BROKER_SUBMISSION,
        Responsibility.ORDER_EXECUTION,
        Responsibility.PORTFOLIO_MUTATION,
        Responsibility.POLICY_CHANGE,
        Responsibility.CREDENTIAL_ACCESS,
        Responsibility.UNRESTRICTED_SHELL_EXECUTION,
        Responsibility.AUTOMATIC_STRATEGY_PROMOTION,
        Responsibility.AUTOMATIC_FORECAST_DRIVEN_TRADING,
    ):
        assert responsibility in registration.prohibited_responsibilities


def test_disabled_and_missing_dependencies_are_unavailable_and_lazy() -> None:
    runtime = FakeKronosRuntime()
    provider = LocalKronosProvider(KronosConfig(), runtime)
    assert provider.identity.enabled is False
    assert provider.identity.health.value == "unavailable"
    assert runtime.calls == 0


def test_model_and_tokenizer_paths_are_hashed_not_exposed() -> None:
    value = config(model="/private/kronos/model", tokenizer="/private/kronos/tokenizer")
    assert "/private" not in value.model_id
    assert "/private" not in value.tokenizer_id


@pytest.mark.parametrize(
    "change",
    (
        {"timestamp": "bad"},
        {"open": math.nan},
        {"high": 1.0},
        {"low": 200.0},
        {"volume": -1.0},
    ),
)
def test_market_bar_validation(change) -> None:
    values = asdict(bars(1)[0])
    values.update(change)
    with pytest.raises(KronosValidationError):
        GovernedMarketBar(**values)


def test_series_rejects_order_duplicates_digest_and_oversize() -> None:
    values = bars()
    with pytest.raises(KronosValidationError):
        series(bars=(values[1], values[0], *values[2:]))
    with pytest.raises(KronosValidationError):
        series(bars=(values[0], values[0], *values[2:]))
    with pytest.raises(KronosValidationError):
        replace(series(), source_digest=DIGEST)
    with pytest.raises(KronosValidationError):
        series(bars=bars(2_049))


def test_request_rejects_prohibited_responsibility_horizon_interval_and_quantiles() -> None:
    with pytest.raises(KronosValidationError):
        replace(request(), responsibility=Responsibility.ORDER_EXECUTION)
    with pytest.raises(KronosValidationError):
        replace(request(), forecast_horizon=257)
    with pytest.raises(KronosValidationError):
        replace(request(), interval="30s")
    with pytest.raises(KronosValidationError):
        replace(
            request(), uncertainty_mode=UncertaintyMode.QUANTILES, requested_quantiles=(0.2, 0.8)
        )


def test_healthy_provider_success_is_structured_and_evidenced(tmp_path: Path) -> None:
    result, _, provider, ledger, artifacts = forecast(tmp_path)
    assert result.succeeded
    assert isinstance(result.artifact, GovernedForecastArtifact)
    assert result.artifact.forecast_horizon == 2
    assert provider._runtime.calls == 1
    assert len(ledger.read_records()) == 3
    assert artifacts.read_artifacts() == (result.artifact,)
    assert result.artifact.paper_only is True
    assert result.artifact.broker_submission is False


def test_timeout_fails_without_artifact(tmp_path: Path) -> None:
    governed, _, ledger, artifacts = service(tmp_path, FakeKronosRuntime(delay=0.2))
    value = governed.forecast(
        replace(request(), timeout_ms=100),
        series=series(),
        completed_at="2026-02-02T00:00:01Z",
    )
    assert not value.succeeded
    assert value.failure_classification == "timeout"
    assert len(ledger.read_records()) == 3
    assert artifacts.read_artifacts() == ()


def test_model_tokenizer_capability_interval_horizon_and_sequence_fail_closed() -> None:
    provider = LocalKronosProvider(config(), FakeKronosRuntime())
    base = ProviderInvocation(
        "invoke",
        "task",
        provider.model_id,
        DIGEST,
        Capability.TIME_SERIES_FORECASTING,
        {
            "model_id": provider.model_id,
            "tokenizer_id": provider.tokenizer_id,
            "tokenizer_version": provider.tokenizer_version,
            "interval": "1d",
            "forecast_horizon": 2,
            "uncertainty_mode": "none",
            "series_id": "series",
            "series_digest": DIGEST,
            "symbol": "AAPL",
            "freshness_state": "current",
            "bars": [asdict(item) for item in bars()],
        },
        1_000,
        iso(1),
        iso(2),
    )
    assert (
        provider.invoke(replace(base, model_id="wrong")).failure.classification.value
        == "model_identity_mismatch"
    )
    assert (
        provider.invoke(
            replace(base, input_payload={**base.input_payload, "tokenizer_id": "wrong"})
        ).failure.classification.value
        == "model_identity_mismatch"
    )
    assert (
        provider.invoke(replace(base, capability=Capability.REASONING)).failure.classification.value
        == "capability_mismatch"
    )
    assert (
        provider.invoke(
            replace(base, input_payload={**base.input_payload, "interval": "1h"})
        ).failure
        is not None
    )
    assert (
        provider.invoke(
            replace(base, input_payload={**base.input_payload, "forecast_horizon": 9})
        ).failure
        is not None
    )
    assert (
        provider.invoke(replace(base, input_payload={**base.input_payload, "bars": []})).failure
        is not None
    )


def test_malformed_forecast_persists_rejection_evidence_only(tmp_path: Path) -> None:
    result, _, _, ledger, artifacts = forecast(tmp_path, runtime=FakeKronosRuntime(malformed=True))
    assert not result.succeeded
    assert result.failure_classification == "output_validation_failed"
    assert len(ledger.read_records()) == 4
    assert artifacts.read_artifacts() == ()


def test_output_validation_rejects_horizon_continuity_uncertainty_and_authority(
    tmp_path: Path,
) -> None:
    result, _, provider, _, _ = forecast(tmp_path)
    payload = asdict(result.artifact.structured_payload)
    payload["uncertainty_mode"] = "none"
    common = {
        "request": request(),
        "series": series(),
        "provider_id": provider.identity.provider_id,
        "model_id": provider.model_id,
        "model_version": provider.model_version,
        "tokenizer_id": provider.tokenizer_id,
        "tokenizer_version": provider.tokenizer_version,
    }
    for mutation in (
        lambda value: value["forecast_points"].pop(),
        lambda value: value["forecast_points"][0].update(timestamp="2026-04-01T00:00:00Z"),
        lambda value: value["forecast_points"][0].update(close=math.inf),
        lambda value: value["forecast_points"][0].update(lower_close=200.0, upper_close=100.0),
        lambda value: value.update(model_id="wrong"),
        lambda value: value.update(execution_authorized=True),
    ):
        changed = {
            **payload,
            "forecast_points": [dict(item) for item in payload["forecast_points"]],
        }
        mutation(changed)
        with pytest.raises(KronosValidationError):
            validate_kronos_output(changed, **common)


def test_quantile_forecast_validates_real_bands(tmp_path: Path) -> None:
    item = series()
    result, *_ = forecast(
        tmp_path,
        series_value=item,
        request_value=replace(
            request(series=item),
            uncertainty_mode=UncertaintyMode.QUANTILES,
            requested_quantiles=(0.1, 0.5, 0.9),
        ),
    )
    assert result.succeeded
    assert result.artifact.structured_payload.calibration == "runtime-reported"
    assert result.artifact.structured_payload.forecast_points[0].lower_close is not None


def test_quantile_forecast_rejects_missing_runtime_bands(tmp_path: Path) -> None:
    item = series()
    result, *_ = forecast(
        tmp_path,
        runtime=FakeKronosRuntime(),
        series_value=item,
        request_value=replace(
            request(series=item),
            uncertainty_mode=UncertaintyMode.QUANTILES,
            requested_quantiles=(0.1, 0.5, 0.9),
        ),
    )
    assert result.succeeded
    payload = asdict(result.artifact.structured_payload)
    payload["uncertainty_mode"] = "quantiles"
    payload["forecast_points"][0]["lower_close"] = None
    payload["forecast_points"][0]["upper_close"] = None
    with pytest.raises(KronosValidationError):
        validate_kronos_output(
            payload,
            request=replace(
                request(series=item),
                uncertainty_mode=UncertaintyMode.QUANTILES,
                requested_quantiles=(0.1, 0.5, 0.9),
            ),
            series=item,
            provider_id=result.artifact.provider_id,
            model_id=result.artifact.model_id,
            model_version=result.artifact.model_version,
            tokenizer_id=result.artifact.tokenizer_id,
            tokenizer_version=result.artifact.tokenizer_version,
        )


def test_series_privacy_trust_staleness_and_bounds_fail_before_inference(tmp_path: Path) -> None:
    for item, requested in (
        (series(privacy_classification=PrivacyTier.EXTERNAL_APPROVED), request()),
        (series(source_trust=TrustTier.RESTRICTED), request()),
        (series(stale_after="2026-02-01T00:00:00Z"), request()),
        (series(bars=bars(8)), request(series=series(bars=bars(8)))),
    ):
        governed, provider, _, _ = service(
            tmp_path / item.series_id.replace(".", "-") if False else tmp_path
        )
        response = governed.forecast(requested, series=item, completed_at="2026-02-02T00:00:01Z")
        assert not response.succeeded
        assert provider._runtime.calls == 0


def test_evaluation_metrics_restart_and_no_promotion(tmp_path: Path) -> None:
    result, governed, _, _, artifacts = forecast(tmp_path)
    observed = series(series_id="aapl-observed-v2", bars=bars(34))
    evaluation = governed.evaluate_forecast(
        result.artifact,
        observed,
        request_id="evaluate-request",
        task_correlation_id="evaluate-task",
        evaluated_at="2026-02-10T00:00:00Z",
    )
    assert evaluation.sample_count == 2
    assert evaluation.mae == pytest.approx(0.0)
    assert evaluation.rmse == pytest.approx(0.0)
    assert evaluation.directional_accuracy == 1.0
    assert "promote" in " ".join(evaluation.limitations).lower()
    assert DurableAnalysisArtifactStore(tmp_path.resolve()).read_artifacts()[-1] == evaluation
    assert artifacts.read_artifacts()[0] == result.artifact


def test_evaluation_comparison_is_bounded_observational_and_has_no_winner(tmp_path: Path) -> None:
    result, governed, _, _, _ = forecast(tmp_path)
    observed = series(series_id="aapl-observed-v2", bars=bars(34))
    first = governed.evaluate_forecast(
        result.artifact,
        observed,
        request_id="evaluate-first",
        task_correlation_id="evaluate-task",
        evaluated_at="2026-02-10T00:00:00Z",
    )
    second = replace(
        first, artifact_id="evaluation-artifact-" + "b" * 64, request_id="evaluate-second"
    )
    comparison = compare_forecast_evaluations((second, first), compared_at="2026-02-11T00:00:00Z")
    assert comparison.evaluation_artifact_ids == tuple(
        sorted((first.artifact_id, second.artifact_id))
    )
    assert comparison.execution_authorized is False
    assert "winner" not in str(comparison).lower()
    with pytest.raises(KronosValidationError):
        compare_forecast_evaluations((first,), compared_at="2026-02-11T00:00:00Z")


def test_evaluation_rejects_insufficient_observations_and_identity_mismatch(tmp_path: Path) -> None:
    result, *_ = forecast(tmp_path)
    with pytest.raises(KronosValidationError):
        evaluate_forecast(
            result.artifact,
            series(bars=bars(32)),
            request_id="evaluate",
            task_correlation_id="task",
            evaluated_at=iso(10),
        )
    with pytest.raises(KronosValidationError):
        evaluate_forecast(
            result.artifact,
            series(symbol="MSFT", series_id="msft-daily-v1", bars=bars(34)),
            request_id="evaluate",
            task_correlation_id="task",
            evaluated_at=iso(10),
        )


def test_hermes_handoff_success_and_digest_failure(tmp_path: Path) -> None:
    governed, _, _, _ = service(tmp_path)
    item = series()
    work = GovernedForecastWorkRequest(
        "hermes-forecast",
        "hermes-task",
        item.series_id,
        item.source_digest,
        item.symbol,
        item.interval,
        2,
        "none",
        (),
        PrivacyTier.LOCAL_ONLY,
        TrustTier.TRUSTED,
        (DIGEST,),
        Responsibility.MARKET_FORECASTING,
    )
    result = governed.forecast_hermes(
        work, series=item, requested_at="2026-02-02T00:00:00Z", completed_at="2026-02-02T00:00:01Z"
    )
    assert result.succeeded
    with pytest.raises(ValueError):
        replace(work, series_digest="bad")


def test_inspection_is_sanitized_and_startup_independent(tmp_path: Path) -> None:
    result, governed, _, _, _ = forecast(tmp_path)
    observed = series(series_id="aapl-observed-v2", bars=bars(34))
    governed.evaluate_forecast(
        result.artifact,
        observed,
        request_id="evaluate-request",
        task_correlation_id="evaluate-task",
        evaluated_at="2026-02-10T00:00:00Z",
    )
    environment = {
        "SIGIL_DESKTOP_STATE_DIR": str(tmp_path.resolve()),
        "SIGIL_AI_KRONOS_ENABLED": "true",
        "SIGIL_AI_KRONOS_MODEL": "/private/models/kronos",
        "SIGIL_AI_KRONOS_TOKENIZER": "/private/tokenizers/kronos",
        "SIGIL_AI_KRONOS_MODEL_VERSION": "test-v1",
        "SIGIL_AI_KRONOS_TOKENIZER_VERSION": "test-v1",
        "SIGIL_AI_KRONOS_ALLOWED_INTERVALS": "1d",
    }
    status = ai_status(environment)
    assert status["kronos"]["forecast_artifact_count"] == 1
    assert status["kronos"]["evaluation_artifact_count"] == 1
    assert status["kronos"]["last_successful_forecast"]["symbol"] == "AAPL"
    serialized = str(status).lower()
    assert "/private" not in serialized and "bars" not in serialized
    exact = ai_artifact_get({"artifact_id": result.artifact.artifact_id}, environment)
    assert exact["found"] is True
    assert "hidden" not in str(exact).lower()
    disabled = ai_status({})
    assert disabled["kronos"]["health"] == "disabled"
    assert disabled["paper_only"] is True and disabled["broker_submission"] is False


def test_service_disabled_and_no_route_fail_closed(tmp_path: Path) -> None:
    governed, _, _, _ = service(tmp_path, enabled=False)
    assert (
        governed.forecast(request(), series=series(), completed_at=iso(2)).failure_classification
        == "service_disabled"
    )
    empty = GovernedAnalysisService(
        registry=GovernedModelRegistry((), ()),
        providers={},
        evidence_ledger=DurableAIEvidenceLedger(tmp_path.resolve()),
        artifact_store=DurableAnalysisArtifactStore(tmp_path.resolve()),
        enabled=True,
    )
    response = empty.forecast(request(), series=series(), completed_at="2026-02-02T00:00:01Z")
    assert not response.succeeded
    assert response.artifact is None


def test_only_explicit_forecasting_fallback_is_routable(tmp_path: Path) -> None:
    provider = DeterministicProvider(
        provider_id="approved-forecast-fallback",
        model_id="approved-forecast-model",
        model_family="gemma",
        capabilities=frozenset({Capability.TIME_SERIES_FORECASTING}),
    )
    registration = ModelRegistration(
        model_id=provider.model_id,
        provider_id=provider.identity.provider_id,
        family="gemma",
        version=provider.model_version,
        capabilities=provider.capabilities,
        execution_location=provider.identity.execution_location,
        context_limit=64,
        supported_input_types=frozenset({InputType.TIME_SERIES}),
        structured_output=True,
        cost_class=CostClass.FREE,
        trust_tier=TrustTier.TRUSTED,
        privacy_tier=PrivacyTier.LOCAL_ONLY,
        health=provider.identity.health,
        enabled=True,
        allowed_responsibilities=frozenset({Responsibility.MARKET_FORECASTING}),
    )
    ledger = DurableAIEvidenceLedger(tmp_path.resolve())
    governed = GovernedAnalysisService(
        registry=GovernedModelRegistry((provider.identity,), (registration,)),
        providers={provider.identity.provider_id: provider},
        evidence_ledger=ledger,
        artifact_store=DurableAnalysisArtifactStore(tmp_path.resolve()),
        enabled=True,
    )
    response = governed.forecast(
        replace(request(), fallback_permission=True),
        series=series(),
        completed_at="2026-02-02T00:00:01Z",
    )
    assert response.failure_classification == "output_validation_failed"
    assert ledger.read_records()[0].fallback is True

    no_capability = DeterministicProvider(provider_id="generic-gemma", model_id="generic-gemma")
    generic_registration = replace(
        registration,
        model_id=no_capability.model_id,
        provider_id=no_capability.identity.provider_id,
        capabilities=no_capability.capabilities,
        supported_input_types=frozenset({InputType.TEXT}),
    )
    generic_root = tmp_path / "generic"
    generic_root.mkdir()
    generic_service = GovernedAnalysisService(
        registry=GovernedModelRegistry((no_capability.identity,), (generic_registration,)),
        providers={no_capability.identity.provider_id: no_capability},
        evidence_ledger=DurableAIEvidenceLedger(generic_root.resolve()),
        artifact_store=DurableAnalysisArtifactStore(generic_root.resolve()),
        enabled=True,
    )
    generic = generic_service.forecast(
        replace(request(), fallback_permission=True),
        series=series(),
        completed_at="2026-02-02T00:00:01Z",
    )
    assert not generic.succeeded
    assert generic.routing_summary == "forecast routing rejected"
