#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { discoverPackagedPython } from './packaged-python.mjs'

const appDirectory = path.resolve(import.meta.dirname, '..')
const stagingRoot = path.join(appDirectory, 'packaged-backend/staged')
const python = discoverPackagedPython()

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: appDirectory,
    encoding: 'utf8',
    ...options
  })
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(' ')} failed (${result.status}):\n` +
      `${result.stdout}${result.stderr}`
    )
  }
  return result.stdout
}

run(process.execPath, ['scripts/prepare-packaged-backend.mjs'])

const pythonFiles = []
function collectPythonFiles(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      collectPythonFiles(entryPath)
    } else if (entry.isFile() && entry.name.endsWith('.py')) {
      pythonFiles.push(entryPath)
    }
  }
}
collectPythonFiles(stagingRoot)

const cacheDirectory = fs.mkdtempSync(
  path.join(os.tmpdir(), 'sigil-packaged-pycache.')
)
const stateDirectory = fs.mkdtempSync(
  path.join(os.tmpdir(), 'sigil-packaged-state.')
)
const environment = {
  ...process.env,
  PYTHONPATH: stagingRoot,
  PYTHONPYCACHEPREFIX: cacheDirectory,
  SIGIL_DESKTOP_STATE_DIR: stateDirectory
}

try {
  run(python, ['-m', 'py_compile', ...pythonFiles], { env: environment })
  process.stdout.write(`PASS: py_compile ${pythonFiles.length} packaged Python files\n`)

  run(python, [
    '-c',
    'import sigil; assert sigil.__file__ is not None; print(sigil.__file__)'
  ], { env: environment })
  process.stdout.write('PASS: imported packaged sigil module\n')

  function bridgeRequest(request, environmentOverrides = {}) {
    const output = run(
      python,
      ['-m', 'sigil.desktop_bridge.runner'],
      {
        env: { ...environment, ...environmentOverrides },
        input: `${JSON.stringify(request)}\n`
      }
    )
    const response = JSON.parse(output)

    if (response.ok !== true) {
      throw new Error(
        `${request.command} did not return ok=true: ${output}`
      )
    }

    return response.result
  }

  const initialSnapshot = bridgeRequest({ command: 'runtime_snapshot' })
  process.stdout.write(
    'PASS: runtime_snapshot bridge returned valid JSON with ok=true\n'
  )

  const aiStatus = bridgeRequest({ command: 'ai_status' })
  if (
    aiStatus.paper_only !== true ||
    aiStatus.execution_authorized !== false ||
    aiStatus.broker_submission !== false ||
    aiStatus.secrets_exposed !== false
  ) {
    throw new Error(`ai_status violated its inspection boundary: ${JSON.stringify(aiStatus)}`)
  }
  process.stdout.write('PASS: ai_status preserved advisory-only authority\n')

  const integrationRegistry = aiStatus.integration_registry
  if (
    integrationRegistry?.enabled !== false ||
    integrationRegistry?.state !== 'disabled' ||
    integrationRegistry?.store_health !== 'empty' ||
    integrationRegistry?.entry_count !== 0 ||
    integrationRegistry?.unpinned_count !== 0 ||
    integrationRegistry?.paper_only !== true ||
    integrationRegistry?.broker_submission !== false ||
    integrationRegistry?.activation_authorized !== false ||
    integrationRegistry?.installation_authorized !== false ||
    integrationRegistry?.approval_authority !== false
  ) {
    throw new Error(`Integration registry did not start empty and denied: ${JSON.stringify(integrationRegistry)}`)
  }
  process.stdout.write('PASS: packaged integration registry starts disabled, empty, and non-authoritative\n')

  const disabledOrchestration = aiStatus.orchestration
  if (
    disabledOrchestration?.enabled !== false ||
    disabledOrchestration?.health !== 'disabled' ||
    disabledOrchestration?.buzz !== 'unavailable' ||
    disabledOrchestration?.atlas !== 'unavailable' ||
    disabledOrchestration?.openworker !== 'unavailable' ||
    disabledOrchestration?.pending_human_interactions !== 0
  ) {
    throw new Error(`Hermes orchestration did not start safely disabled: ${JSON.stringify(disabledOrchestration)}`)
  }
  process.stdout.write('PASS: packaged optional orchestration surfaces start safely disabled\n')

  const disabledFleet = aiStatus.fleet
  if (
    disabledFleet?.enabled !== false ||
    disabledFleet?.health !== 'disabled' ||
    disabledFleet?.registered_node_count !== 0 ||
    disabledFleet?.healthy_node_count !== 0 ||
    disabledFleet?.active_tasks !== 0 ||
    disabledFleet?.completion_unknown_tasks !== 0 ||
    disabledFleet?.broker_submission !== false
  ) {
    throw new Error(`Governed fleet did not start safely disabled: ${JSON.stringify(disabledFleet)}`)
  }
  process.stdout.write('PASS: packaged governed fleet starts disabled and empty\n')

  run(
    python,
    [
      '-c',
      [
        'from dataclasses import replace',
        'from sigil.ai import *',
        "NOW='2026-08-01T18:00:00+00:00'; D='sha256:' + ('a' * 64)",
        "def node(role, cpu=CPUClass.STANDARD, memory=MemoryClass.MEDIUM):",
        " identity=FleetNodeIdentity('node-'+role.value, role.value, role, DeviceClass.SERVER, 'linux', 'arm64', 'governed-os', TrustTier.TRUSTED, PrivacyTier.LOCAL_ONLY, ExecutionLocation.FLEET, 'tailnet:'+role.value, 'identity-ref:'+role.value, NOW, NOW, True, True)",
        " model=FleetModelInventory(role.value+'-provider', role.value+'-model', role.value+'-tokenizer', frozenset((Capability.REASONING,)))",
        " return FleetNodeRegistration(identity, (model,), frozenset((WorkerTaskType.RESEARCH_PREPARATION,)), memory, cpu, None, 2, 10000, 2000, 2000, resource_enforcement_verified=True, enabled=True, health=ProviderHealth.HEALTHY)",
        "def health(n, state=FleetNodeState.HEALTHY): return FleetNodeHealth(n.identity.node_id, n.identity.authenticated_identity_ref, NOW, NOW, state, n.capabilities, tuple(m.model_id for m in n.models), 10, 0, 0, 'normal', 'normal', 'normal', ProviderHealth.HEALTHY, False, False)",
        "def request(**kw):",
        " values=dict(fleet_request_id='fleet-packaged', orchestration_id='orchestration-packaged', step_id='orchestration-step-'+('c'*64), task_correlation_id='packaged-fleet-task', required_capability=Capability.REASONING, responsibility=Responsibility.RESEARCH_ANALYSIS, required_provider_id=None, required_model_id=None, required_tokenizer_id=None, required_vector_dimension=None, required_corpus_revision=None, privacy_requirement=PrivacyTier.LOCAL_ONLY, minimum_trust_tier=TrustTier.TRUSTED, preferred_node_roles=(FleetNodeRole.TITAN,FleetNodeRole.MAC,FleetNodeRole.PRIME), excluded_node_ids=(), maximum_latency_ms=5000, maximum_duration_ms=5000, maximum_memory_class=MemoryClass.MEDIUM, minimum_cpu_class=CPUClass.STANDARD, maximum_cost_class=CostClass.FREE, fallback_permission=False, escalation_permission=True, cancellation_policy='query_before_retry', maximum_retries=1, maximum_remote_steps=1, input_digests=(D,), evidence_context_digests=(D,), requested_at=NOW); values.update(kw); return FleetRoutingRequest(**values)",
        "titan=node(FleetNodeRole.TITAN); mac=node(FleetNodeRole.MAC, CPUClass.HIGH, MemoryClass.LARGE); prime=node(FleetNodeRole.PRIME, CPUClass.HIGH, MemoryClass.LARGE)",
        "registry=FleetRegistry((titan,mac,prime)); router=GovernedFleetRouter(registry); healthy={n.identity.node_id:health(n) for n in (titan,mac,prime)}",
        "assert router.route(request(), healthy, decided_at=NOW).selected_node_id == 'node-titan'",
        "heavy=request(maximum_memory_class=MemoryClass.LARGE, minimum_cpu_class=CPUClass.HIGH); assert router.route(heavy, healthy, decided_at=NOW).selected_node_id == 'node-mac'",
        "healthy['node-mac']=health(mac,FleetNodeState.UNAVAILABLE); assert router.route(heavy, healthy, decided_at=NOW).selected_node_id == 'node-prime'",
        "assert all(not getattr(x, 'broker_submission', True) for x in (request(), router.route(request(), healthy, decided_at=NOW)))"
      ].join('\n')
    ],
    { env: environment }
  )
  process.stdout.write('PASS: packaged Titan, Mac escalation, and Prime fallback routing stayed governed\n')

  run(
    python,
    [
      '-c',
      [
        'from pathlib import Path',
        'from sigil.ai import DurableFleetStore, fleet_evidence',
        "root=Path(__import__('os').environ['SIGIL_DESKTOP_STATE_DIR']).resolve(); store=DurableFleetStore(root)",
        "store.append(fleet_evidence('failover', 'fleet-packaged-failover', node_id='node-titan', input_value='fleet-packaged', output_value='node-mac', state='retry_next_eligible_node', created_at='2026-08-01T18:00:00+00:00', failure='provider_unavailable'))",
        "store.append(fleet_evidence('completion_ambiguous', 'remote-task-packaged-unknown', node_id='node-mac', input_value='remote-task-packaged-unknown', output_value=None, state='completion_unknown', created_at='2026-08-01T18:00:01+00:00', failure='transport_ambiguous'))"
      ].join('\n')
    ],
    { env: environment }
  )
  const inspectedFleet = bridgeRequest(
    { command: 'ai_status' },
    { SIGIL_AI_FLEET_ENABLED: 'true' }
  ).fleet
  if (
    inspectedFleet?.completion_unknown_tasks !== 1 ||
    inspectedFleet?.latest_failover?.node_id !== 'node-titan' ||
    inspectedFleet?.execution_authorized !== false ||
    inspectedFleet?.broker_submission !== false
  ) {
    throw new Error(`Packaged fleet failover/ambiguity inspection was unsafe: ${JSON.stringify(inspectedFleet)}`)
  }
  process.stdout.write('PASS: packaged fleet failover and completion_unknown stayed visible and non-authoritative\n')

  const recentArtifacts = bridgeRequest({
    command: 'ai_recent_artifacts',
    payload: { limit: 1 }
  })
  if (!Array.isArray(recentArtifacts.artifacts) || recentArtifacts.artifacts.length !== 0) {
    throw new Error(`ai_recent_artifacts was not safely empty: ${JSON.stringify(recentArtifacts)}`)
  }
  process.stdout.write('PASS: ai_recent_artifacts returned a bounded safe result\n')

  const artifactResult = bridgeRequest({
    command: 'ai_artifact_get',
    payload: { artifact_id: `analysis-artifact-${'0'.repeat(64)}` }
  })
  if (artifactResult.found !== false || artifactResult.artifact !== null) {
    throw new Error(`ai_artifact_get did not fail closed: ${JSON.stringify(artifactResult)}`)
  }
  process.stdout.write('PASS: ai_artifact_get returned deterministic not-found\n')

  const disabledFinBERT = aiStatus.finbert
  if (disabledFinBERT?.enabled !== false || disabledFinBERT?.health !== 'disabled') {
    throw new Error(`FinBERT was not safely disabled: ${JSON.stringify(disabledFinBERT)}`)
  }
  process.stdout.write('PASS: packaged FinBERT status is disabled by default\n')

  const unavailableFinBERT = bridgeRequest(
    { command: 'ai_status' },
    {
      SIGIL_AI_FINBERT_ENABLED: 'true',
      SIGIL_AI_FINBERT_MODEL: 'prosusai.finbert',
      SIGIL_AI_FINBERT_MODEL_VERSION: 'packaged-unverified'
    }
  ).finbert
  if (
    unavailableFinBERT?.enabled !== true ||
    !['configured_unverified', 'unavailable'].includes(unavailableFinBERT?.health) ||
    unavailableFinBERT?.sentiment_artifact_count !== 0
  ) {
    throw new Error(`FinBERT unavailable status was unsafe: ${JSON.stringify(unavailableFinBERT)}`)
  }
  process.stdout.write('PASS: packaged FinBERT optional runtime status is bounded\n')

  run(
    python,
    [
      '-c',
      [
        'from sigil.ai import Capability, FinBERTConfig, LocalFinBERTProvider, ProviderInvocation',
        'class Runtime:',
        " def predict(self, *, text): return {'positive': 0.7, 'neutral': 0.2, 'negative': 0.1}",
        "provider = LocalFinBERTProvider(FinBERTConfig(enabled=True, model_version='packaged-test'), Runtime())",
        "result = provider.invoke(ProviderInvocation('packaged-request', 'packaged-task', provider.model_id, 'sha256:' + 'a' * 64, Capability.FINANCIAL_SENTIMENT, {'source_text': 'Revenue improved.', 'source_identity': 'packaged-source', 'source_digest': 'sha256:' + 'b' * 64}, 1000, '2026-08-01T18:00:00Z', '2026-08-01T18:00:01Z'))",
        "assert result.succeeded and result.output['label'] == 'positive'",
        "assert result.broker_submission is False and result.paper_only is True"
      ].join('\n')
    ],
    { env: environment }
  )
  process.stdout.write('PASS: packaged deterministic FinBERT invocation stayed advisory-only\n')

  const disabledEmbeddingGemma = aiStatus.embeddinggemma
  if (
    disabledEmbeddingGemma?.enabled !== false ||
    disabledEmbeddingGemma?.health !== 'disabled' ||
    disabledEmbeddingGemma?.vector_store_health !== 'empty'
  ) {
    throw new Error(`EmbeddingGemma was not safely disabled: ${JSON.stringify(disabledEmbeddingGemma)}`)
  }
  process.stdout.write('PASS: packaged EmbeddingGemma and empty vector store start safely\n')

  const unavailableEmbeddingGemma = bridgeRequest(
    { command: 'ai_status' },
    {
      SIGIL_AI_EMBEDDING_GEMMA_ENABLED: 'true',
      SIGIL_AI_EMBEDDING_GEMMA_MODEL_VERSION: 'packaged-unverified',
      SIGIL_AI_EMBEDDING_GEMMA_VECTOR_DIMENSION: '3'
    }
  ).embeddinggemma
  if (
    unavailableEmbeddingGemma?.enabled !== true ||
    !['configured_unverified', 'unavailable'].includes(unavailableEmbeddingGemma?.health) ||
    unavailableEmbeddingGemma?.embedding_count !== 0
  ) {
    throw new Error(`EmbeddingGemma unavailable status was unsafe: ${JSON.stringify(unavailableEmbeddingGemma)}`)
  }
  process.stdout.write('PASS: packaged EmbeddingGemma optional runtime status is bounded\n')

  run(
    python,
    [
      '-c',
      [
        'from pathlib import Path',
        'from sigil.ai import *',
        'from sigil.ai.registry import canonical_digest',
        'class Runtime:',
        " def embed(self, *, texts): return [[1.0, 0.0, 0.0] for _ in texts]",
        "root = Path(__import__('os').environ['SIGIL_DESKTOP_STATE_DIR']).resolve()",
        "provider = LocalEmbeddingGemmaProvider(EmbeddingGemmaConfig(enabled=True, model_version='packaged-test', vector_dimension=3), Runtime())",
        'registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))',
        "service = GovernedAnalysisService(registry=registry, providers={provider.identity.provider_id: provider}, evidence_ledger=DurableAIEvidenceLedger(root), artifact_store=DurableAnalysisArtifactStore(root), retrieval_store=DurableRetrievalStore(root), enabled=True)",
        "source = create_retrieval_source(content='Revenue growth improved.', source_type=RetrievalSourceType.RESEARCH_ARTIFACT, source_identity='packaged-source', source_version='v1', corpus_id='packaged-corpus', created_at='2026-08-01T18:00:00Z', observed_at='2026-08-01T18:00:00Z', stale_after=None, privacy_classification=PrivacyTier.LOCAL_ONLY, trust_classification=TrustTier.TRUSTED, language='en', supersedes_source_id=None)",
        "indexed = service.index_retrieval_source(GovernedIndexingRequest('packaged-index', 'packaged-index-task', source, 1000, '2026-08-01T18:00:00Z'), completed_at='2026-08-01T18:00:01Z')",
        "query = 'growth evidence'",
        "retrieved = service.retrieve(GovernedRetrievalRequest('packaged-retrieval', 'packaged-retrieval-task', Responsibility.RESEARCH_RETRIEVAL, 'sha256:' + canonical_digest(query), query, ('packaged-corpus',), (), PrivacyTier.LOCAL_ONLY, TrustTier.TRUSTED, FreshnessRequirement.ANY, 3, 0.0, False, '2026-08-01T18:01:00Z', ('sha256:' + 'a' * 64,)), completed_at='2026-08-01T18:01:01Z')",
        'assert indexed.succeeded and retrieved.succeeded and len(retrieved.artifact.results) == 1',
        'assert retrieved.broker_submission is False and retrieved.execution_authorized is False',
        "assert 'vector' not in str(retrieved.artifact).lower()"
      ].join('\n')
    ],
    { env: environment }
  )
  process.stdout.write('PASS: packaged deterministic indexing and retrieval stayed advisory-only\n')

  const disabledKronos = aiStatus.kronos
  if (
    disabledKronos?.enabled !== false ||
    disabledKronos?.health !== 'disabled' ||
    disabledKronos?.forecast_artifact_count !== 0 ||
    disabledKronos?.evaluation_artifact_count !== 0
  ) {
    throw new Error(`Kronos was not safely disabled: ${JSON.stringify(disabledKronos)}`)
  }
  process.stdout.write('PASS: packaged Kronos and empty forecast stores start safely\n')

  const unavailableKronos = bridgeRequest(
    { command: 'ai_status' },
    {
      SIGIL_AI_KRONOS_ENABLED: 'true',
      SIGIL_AI_KRONOS_MODEL_VERSION: 'packaged-unverified',
      SIGIL_AI_KRONOS_TOKENIZER_VERSION: 'packaged-unverified',
      SIGIL_AI_KRONOS_ALLOWED_INTERVALS: '1d'
    }
  ).kronos
  if (
    unavailableKronos?.enabled !== true ||
    !['configured_unverified', 'unavailable'].includes(unavailableKronos?.health) ||
    unavailableKronos?.forecast_artifact_count !== 0
  ) {
    throw new Error(`Kronos unavailable status was unsafe: ${JSON.stringify(unavailableKronos)}`)
  }
  process.stdout.write('PASS: packaged Kronos optional runtime status is bounded\n')

  run(
    python,
    [
      '-c',
      [
        'from datetime import datetime, timedelta, timezone',
        'from pathlib import Path',
        'from sigil.ai import *',
        'class Runtime:',
        " def forecast(self, *, bars, horizon, uncertainty_mode):",
        "  end = datetime.fromisoformat(bars[-1]['timestamp'])",
        "  return [{'horizon_index': index, 'timestamp': (end + timedelta(days=index)).isoformat().replace('+00:00', 'Z'), 'open': 131.5 + index, 'high': 133.0 + index, 'low': 131.0 + index, 'close': 132.0 + index, 'volume': 1100.0, 'lower_close': None, 'upper_close': None} for index in range(1, horizon + 1)]",
        "root = Path(__import__('os').environ['SIGIL_DESKTOP_STATE_DIR']).resolve()",
        "bars = tuple(GovernedMarketBar((datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat().replace('+00:00', 'Z'), 100.0 + index, 102.0 + index, 99.0 + index, 101.0 + index, 1000.0 + index) for index in range(32))",
        "series = GovernedMarketSeries('packaged-aapl-v1', 'governed-market:AAPL', market_series_digest(bars), 'AAPL', 'equity', 'NASDAQ', '1d', 'UTC', bars[0].timestamp, bars[-1].timestamp, bars[-1].timestamp, '2026-03-01T00:00:00Z', bars)",
        "provider = LocalKronosProvider(KronosConfig(enabled=True, model_version='packaged-test', tokenizer_version='packaged-test', min_sequence_length=16, max_sequence_length=64, max_horizon=8, allowed_intervals=('1d',)), Runtime())",
        'registry = GovernedModelRegistry((provider.identity,), (provider.registration(),))',
        "service = GovernedAnalysisService(registry=registry, providers={provider.identity.provider_id: provider}, evidence_ledger=DurableAIEvidenceLedger(root), artifact_store=DurableAnalysisArtifactStore(root), enabled=True)",
        "request = GovernedForecastRequest('packaged-forecast', 'packaged-forecast-task', Responsibility.MARKET_FORECASTING, series.series_id, series.source_digest, series.symbol, series.interval, 2, UncertaintyMode.NONE, (), PrivacyTier.LOCAL_ONLY, TrustTier.TRUSTED, False, 1000, '2026-02-02T00:00:00Z', ('sha256:' + 'a' * 64,))",
        "forecast = service.forecast(request, series=series, completed_at='2026-02-02T00:00:01Z')",
        "observed_bars = tuple(GovernedMarketBar((datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)).isoformat().replace('+00:00', 'Z'), 100.0 + index, 102.0 + index, 99.0 + index, 101.0 + index, 1000.0 + index) for index in range(34))",
        "observed = GovernedMarketSeries('packaged-aapl-observed', 'governed-market:AAPL', market_series_digest(observed_bars), 'AAPL', 'equity', 'NASDAQ', '1d', 'UTC', observed_bars[0].timestamp, observed_bars[-1].timestamp, observed_bars[-1].timestamp, '2026-03-01T00:00:00Z', observed_bars)",
        "evaluation = service.evaluate_forecast(forecast.artifact, observed, request_id='packaged-evaluation', task_correlation_id='packaged-evaluation-task', evaluated_at='2026-02-10T00:00:00Z')",
        'assert forecast.succeeded and evaluation.sample_count == 2',
        'assert forecast.execution_authorized is False and forecast.broker_submission is False',
        'assert evaluation.execution_authorized is False and evaluation.broker_submission is False',
        "assert 'bars' not in str(forecast.artifact).lower() and 'tensor' not in str(forecast.artifact).lower()"
      ].join('\n')
    ],
    { env: environment }
  )
  process.stdout.write('PASS: packaged deterministic Kronos forecast and evaluation stayed advisory-only\n')

  run(
    python,
    [
      '-c',
      [
        'from dataclasses import replace',
        'from pathlib import Path',
        'from sigil.ai import *',
        'class Specialists:',
        ' def execute(self, step, request, *, attempt, completed_at):',
        "  evidence = 'sha256:' + ('c' * 64)",
        "  artifact = 'analysis-artifact-' + ('d' * 64)",
        "  return GovernedStepResult('packaged-step-result-' + str(step.ordinal), step.step_id, OrchestrationStepStatus.SUCCEEDED, artifact, (evidence,), ('sanitized finding',), ('operator review required',), (), (), ('Advisory only.',), 0.5, 'current', None, False, False, attempt, completed_at)",
        "root = Path(__import__('os').environ['SIGIL_DESKTOP_STATE_DIR']).resolve()",
        "request = GovernedOrchestrationRequest('orchestration-packaged-complete', 'packaged-orchestration-task', ORCHESTRATION_WORKFLOW, 'Synthesize governed research evidence for operator review.', frozenset((Capability.SEMANTIC_RETRIEVAL, Capability.FINANCIAL_SENTIMENT, Capability.TIME_SERIES_FORECASTING, Capability.REASONING)), frozenset((Responsibility.RESEARCH_RETRIEVAL, Responsibility.FINANCIAL_SENTIMENT_ANALYSIS, Responsibility.MARKET_FORECASTING, Responsibility.RESEARCH_ANALYSIS)), ('sha256:' + ('a' * 64),), PrivacyTier.LOCAL_ONLY, TrustTier.TRUSTED, CostClass.FREE, 5000, 4, 2, True, False, '2026-08-01T18:10:00+00:00')",
        "service = GovernedOrchestrationService(store=DurableOrchestrationStore(root), artifact_store=DurableAnalysisArtifactStore(root), specialist_executor=Specialists(), registry_revision='sha256:' + ('b' * 64), enabled=True)",
        "result = service.run(request, completed_at='2026-08-01T18:10:05+00:00')",
        'assert result.terminal_status == OrchestrationState.COMPLETED and result.artifact_id',
        'assert result.paper_only is True and result.broker_submission is False',
        "paused = service.run(replace(request, orchestration_id='orchestration-packaged-paused', task_correlation_id='packaged-human-task', human_approval_requirement=True, requested_at='2026-08-01T18:11:00+00:00'), completed_at='2026-08-01T18:11:05+00:00')",
        'assert paused.terminal_status == OrchestrationState.PAUSED',
        'records = DurableOrchestrationStore(root).read_records()',
        'assert records[-1].interactions and records[-1].interactions[0].response is None',
        'assert all(record.request.paper_only and not record.request.broker_submission for record in records)'
      ].join('\n')
    ],
    { env: environment }
  )
  const packagedOrchestration = bridgeRequest(
    { command: 'ai_status' },
    {
      SIGIL_AI_ORCHESTRATION_ENABLED: 'true',
      SIGIL_AI_ATLAS_ENABLED: 'true'
    }
  ).orchestration
  if (
    packagedOrchestration?.completed_count !== 1 ||
    packagedOrchestration?.paused_count !== 1 ||
    packagedOrchestration?.pending_human_interactions !== 1 ||
    packagedOrchestration?.atlas !== 'available' ||
    packagedOrchestration?.buzz !== 'unavailable' ||
    packagedOrchestration?.openworker !== 'unavailable'
  ) {
    throw new Error(`Packaged orchestration inspection was unsafe: ${JSON.stringify(packagedOrchestration)}`)
  }
  process.stdout.write('PASS: packaged deterministic orchestration and human interaction stayed governed\n')

  const stoppedSnapshot = bridgeRequest({
    command: 'control_paper_cycle',
    payload: { action: 'stop' }
  })
  if (stoppedSnapshot.automation?.state !== 'stopped') {
    throw new Error(
      `control_paper_cycle did not stop automation: ${JSON.stringify(stoppedSnapshot)}`
    )
  }
  process.stdout.write(
    'PASS: control_paper_cycle stopped the local paper runtime\n'
  )

  const revokedSnapshot = bridgeRequest({
    command: 'control_paper_authorization',
    payload: { action: 'revoke' }
  })
  if (revokedSnapshot.paper_authorization?.status !== 'revoked') {
    throw new Error(
      `control_paper_authorization did not revoke authorization: ${JSON.stringify(revokedSnapshot)}`
    )
  }
  process.stdout.write(
    'PASS: control_paper_authorization revoked local paper authorization\n'
  )

  const resetSnapshot = bridgeRequest({
    command: 'reset_paper_runtime',
    payload: { confirmation: 'RESET LOCAL PAPER PORTFOLIO' }
  })
  if (
    resetSnapshot.positions?.length !== 0 ||
    resetSnapshot.proposals?.length !== 0 ||
    resetSnapshot.executions?.length !== 0
  ) {
    throw new Error(
      `reset_paper_runtime did not clear local ledger state: ${JSON.stringify(resetSnapshot)}`
    )
  }
  process.stdout.write(
    'PASS: reset_paper_runtime cleared only the local paper ledger\n'
  )

  const finalSnapshot = bridgeRequest({ command: 'runtime_snapshot' })
  const serializedFinalSnapshot = JSON.stringify(finalSnapshot)

  if (
    serializedFinalSnapshot.includes('"broker_submission":true') ||
    serializedFinalSnapshot.includes('"broker_submission_available":true')
  ) {
    throw new Error(
      `broker submission became available unexpectedly: ${serializedFinalSnapshot}`
    )
  }

  if (
    initialSnapshot.environment !== 'paper' ||
    finalSnapshot.environment !== 'paper'
  ) {
    throw new Error(
      `packaged bridge left paper mode: ${serializedFinalSnapshot}`
    )
  }

  process.stdout.write(
    'PASS: packaged bridge workflow preserved paper-only broker restrictions\n'
  )
} finally {
  fs.rmSync(cacheDirectory, { recursive: true, force: true })
  fs.rmSync(stateDirectory, { recursive: true, force: true })
}
