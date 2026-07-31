import type { ChildProcess } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

export const GOVERNED_NEWS_STREAM_MODULE = 'sigil.desktop_bridge.governed_news_stream_runner'
export const GOVERNED_NEWS_STREAM_STATE_FILE = 'governed-news-stream-state.json'
export const GOVERNED_NEWS_STREAM_SHUTDOWN_TIMEOUT_MS = 5_000

type SpawnWorker = (
  executable: string,
  arguments_: readonly string[],
  options: {
    cwd: string
    env: NodeJS.ProcessEnv
    stdio: ['ignore', 'pipe', 'pipe']
  }
) => ChildProcess

export type GovernedNewsStreamLifecycleSnapshot = {
  enabled: boolean
  process_running: boolean
  process_pid: number | null
  lifecycle_state: 'disabled' | 'stopped' | 'starting' | 'running' | 'stopping' | 'failed'
  last_process_error: string | null
  state_file: string
  stream_state: Readonly<Record<string, unknown>> | null
  advisory_only: true
  execution_authority: false
  broker_submission_attempted: false
  paper_only: true
}

type GovernedNewsStreamLifecycleOptions = {
  python: string
  sourceRoot: string
  workingDirectory: string
  stateDirectory: string
  environment?: NodeJS.ProcessEnv
  spawnWorker: SpawnWorker
  shutdownTimeoutMs?: number
}

export class GovernedNewsStreamLifecycle {
  readonly #python: string
  readonly #sourceRoot: string
  readonly #workingDirectory: string
  readonly #stateDirectory: string
  readonly #environment: NodeJS.ProcessEnv
  readonly #spawnWorker: SpawnWorker
  readonly #shutdownTimeoutMs: number

  #child: ChildProcess | null = null
  #lifecycleState: 'disabled' | 'stopped' | 'starting' | 'running' | 'stopping' | 'failed' = 'stopped'
  #lastProcessError: string | null = null
  #stopPromise: Promise<void> | null = null

  constructor(options: GovernedNewsStreamLifecycleOptions) {
    this.#python = options.python
    this.#sourceRoot = options.sourceRoot
    this.#workingDirectory = options.workingDirectory
    this.#stateDirectory = options.stateDirectory
    this.#environment = options.environment ?? process.env
    this.#spawnWorker = options.spawnWorker
    this.#shutdownTimeoutMs = options.shutdownTimeoutMs ?? GOVERNED_NEWS_STREAM_SHUTDOWN_TIMEOUT_MS

    if (!this.enabled) {
      this.#lifecycleState = 'disabled'
    }
  }

  get enabled(): boolean {
    return this.#environment.SIGIL_ALPACA_NEWS_STREAM_ENABLED?.trim().toLowerCase() === 'true'
  }

  get running(): boolean {
    return this.#child !== null
  }

  get statePath(): string {
    return path.join(this.#stateDirectory, GOVERNED_NEWS_STREAM_STATE_FILE)
  }

  start(): GovernedNewsStreamLifecycleSnapshot {
    if (!this.enabled) {
      this.#lifecycleState = 'disabled'

      return this.snapshotSync()
    }

    if (this.#child) {
      return this.snapshotSync()
    }

    this.#lifecycleState = 'starting'
    this.#lastProcessError = null

    const child = this.#spawnWorker(this.#python, ['-m', GOVERNED_NEWS_STREAM_MODULE], {
      cwd: this.#workingDirectory,
      env: {
        ...this.#environment,
        PYTHONPATH: this.#sourceRoot,
        SIGIL_DESKTOP_STATE_DIR: this.#stateDirectory
      },
      stdio: ['ignore', 'pipe', 'pipe']
    })

    this.#child = child
    this.#lifecycleState = 'running'

    let stderr = ''

    child.stderr?.setEncoding('utf8')
    child.stderr?.on('data', chunk => {
      stderr += String(chunk)
    })

    child.once('error', error => {
      this.#lastProcessError = error.message
      this.#lifecycleState = 'failed'
    })

    child.once('close', code => {
      if (this.#child === child) {
        this.#child = null
      }

      if (this.#lifecycleState === 'stopping') {
        this.#lifecycleState = 'stopped'

        return
      }

      if (code === 0 || !this.enabled) {
        this.#lifecycleState = this.enabled ? 'stopped' : 'disabled'

        return
      }

      this.#lastProcessError = stderr.trim() || `Governed news stream exited unexpectedly with code ${String(code)}.`
      this.#lifecycleState = 'failed'
    })

    return this.snapshotSync()
  }

  stop(): Promise<void> {
    if (this.#stopPromise) {
      return this.#stopPromise
    }

    const child = this.#child

    if (!child) {
      this.#lifecycleState = this.enabled ? 'stopped' : 'disabled'

      return Promise.resolve()
    }

    this.#lifecycleState = 'stopping'

    this.#stopPromise = new Promise(resolve => {
      let settled = false

      const finish = (): void => {
        if (settled) {
          return
        }

        settled = true
        clearTimeout(forceTimer)

        if (this.#child === child) {
          this.#child = null
        }

        this.#lifecycleState = this.enabled ? 'stopped' : 'disabled'
        this.#stopPromise = null
        resolve()
      }

      child.once('close', finish)

      const forceTimer = setTimeout(() => {
        if (this.#child === child) {
          child.kill('SIGKILL')
        }

        finish()
      }, this.#shutdownTimeoutMs)

      const signalAccepted = child.kill('SIGTERM')

      if (!signalAccepted) {
        finish()
      }
    })

    return this.#stopPromise
  }

  snapshotSync(): GovernedNewsStreamLifecycleSnapshot {
    return {
      enabled: this.enabled,
      process_running: this.running,
      process_pid: this.#child?.pid ?? null,
      lifecycle_state: this.#lifecycleState,
      last_process_error: this.#lastProcessError,
      state_file: this.statePath,
      stream_state: null,
      advisory_only: true,
      execution_authority: false,
      broker_submission_attempted: false,
      paper_only: true
    }
  }

  async snapshot(): Promise<GovernedNewsStreamLifecycleSnapshot> {
    let streamState: Readonly<Record<string, unknown>> | null = null

    try {
      const payload: unknown = JSON.parse(await readFile(this.statePath, 'utf8'))

      if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
        streamState = payload as Readonly<Record<string, unknown>>
      }
    } catch {
      // A missing or partially written state file is represented as null.
    }

    return {
      ...this.snapshotSync(),
      stream_state: streamState
    }
  }
}
