#!/usr/bin/env node

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'

const appDirectory = path.resolve(import.meta.dirname, '..')
const stagingRoot = path.join(appDirectory, 'packaged-backend/staged')
const python = '/opt/homebrew/opt/python@3.11/bin/python3.11'

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

if (!fs.existsSync(python)) {
  throw new Error(`Certified packaged Python runtime not found: ${python}`)
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

  const output = run(
    python,
    ['-m', 'sigil.desktop_bridge.runner'],
    {
      env: environment,
      input: '{"command":"runtime_snapshot"}\n'
    }
  )
  const response = JSON.parse(output)
  if (response.ok !== true) {
    throw new Error(`runtime_snapshot did not return ok=true: ${output}`)
  }
  process.stdout.write('PASS: runtime_snapshot bridge returned valid JSON with ok=true\n')
} finally {
  fs.rmSync(cacheDirectory, { recursive: true, force: true })
  fs.rmSync(stateDirectory, { recursive: true, force: true })
}
