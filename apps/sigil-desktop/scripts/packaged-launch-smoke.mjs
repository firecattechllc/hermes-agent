#!/usr/bin/env node

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'

const appDirectory = path.resolve(import.meta.dirname, '..')
const executable = path.join(
  appDirectory,
  'release/mac-arm64/Sigil.app/Contents/MacOS/Sigil'
)
const minimumRuntimeMs = 5_000
const shutdownTimeoutMs = 5_000

if (!fs.existsSync(executable)) {
  throw new Error(`Packaged Sigil executable not found: ${executable}`)
}

const userDataDirectory = fs.mkdtempSync(
  path.join(os.tmpdir(), 'sigil-packaged-smoke.')
)
let stderr = ''
const child = spawn(executable, [`--user-data-dir=${userDataDirectory}`], {
  cwd: appDirectory,
  env: {
    ...process.env,
    SIGIL_DESKTOP_STATE_DIR: path.join(userDataDirectory, 'paper-runtime')
  },
  stdio: ['ignore', 'ignore', 'pipe']
})

child.stderr.setEncoding('utf8')
child.stderr.on('data', chunk => {
  stderr += chunk
})

const exit = new Promise(resolve => child.once('exit', (code, signal) => {
  resolve({ code, signal })
}))
const earlyExit = await Promise.race([
  exit,
  new Promise(resolve => setTimeout(() => resolve(null), minimumRuntimeMs))
])

if (earlyExit) {
  throw new Error(
    `Sigil exited before initialization completed (${JSON.stringify(earlyExit)}).\n${stderr}`
  )
}

child.kill('SIGTERM')
const cleanExit = await Promise.race([
  exit,
  new Promise(resolve => setTimeout(() => resolve(null), shutdownTimeoutMs))
])

if (!cleanExit) {
  child.kill('SIGKILL')
  throw new Error('Sigil did not exit cleanly after SIGTERM.')
}

fs.rmSync(userDataDirectory, { recursive: true, force: true })
process.stdout.write(
  `PASS: ${executable} remained running for ${minimumRuntimeMs}ms and exited after SIGTERM.\n`
)
