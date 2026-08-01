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

  function bridgeRequest(request) {
    const output = run(
      python,
      ['-m', 'sigil.desktop_bridge.runner'],
      {
        env: environment,
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
