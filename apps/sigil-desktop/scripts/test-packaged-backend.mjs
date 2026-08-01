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
