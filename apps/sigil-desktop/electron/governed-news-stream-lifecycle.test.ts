import type { ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'
import path from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import { GOVERNED_NEWS_STREAM_MODULE, GovernedNewsStreamLifecycle } from './governed-news-stream-lifecycle'

class FakeStream extends EventEmitter {
  setEncoding(): void {}
}

class FakeChild extends EventEmitter {
  pid = 2468
  stdout = new FakeStream()
  stderr = new FakeStream()
  signals: NodeJS.Signals[] = []

  kill(signal: NodeJS.Signals = 'SIGTERM'): boolean {
    this.signals.push(signal)

    return true
  }
}

function lifecycle(
  child: FakeChild,
  changes: {
    enabled?: boolean
    shutdownTimeoutMs?: number
  } = {}
) {
  const spawnWorker = vi.fn(
    () => child as unknown as ChildProcess
  )

  const controller = new GovernedNewsStreamLifecycle({
    python: '/sigil/python',
    sourceRoot: '/sigil/source',
    workingDirectory: '/sigil',
    stateDirectory: '/sigil/state',
    environment: {
      SIGIL_ALPACA_NEWS_STREAM_ENABLED: changes.enabled === false ? 'false' : 'true',
      APCA_API_KEY_ID: 'key',
      APCA_API_SECRET_KEY: 'secret'
    },
    spawnWorker,
    shutdownTimeoutMs: changes.shutdownTimeoutMs ?? 50
  })

  return { controller, spawnWorker }
}

describe('governed news stream lifecycle', () => {
  it('starts one supervised worker with bounded environment propagation', () => {
    const child = new FakeChild()
    const { controller, spawnWorker } = lifecycle(child)

    const first = controller.start()
    const second = controller.start()

    expect(first.lifecycle_state).toBe('running')
    expect(second.process_pid).toBe(2468)
    expect(spawnWorker).toHaveBeenCalledOnce()
    expect(spawnWorker).toHaveBeenCalledWith(
      '/sigil/python',
      ['-m', GOVERNED_NEWS_STREAM_MODULE],
      expect.objectContaining({
        cwd: '/sigil',
        env: expect.objectContaining({
          PYTHONPATH: '/sigil/source',
          SIGIL_DESKTOP_STATE_DIR: '/sigil/state',
          APCA_API_KEY_ID: 'key',
          APCA_API_SECRET_KEY: 'secret'
        }),
        stdio: ['ignore', 'pipe', 'pipe']
      })
    )
  })

  it('does not start unless explicitly enabled', () => {
    const child = new FakeChild()

    const { controller, spawnWorker } = lifecycle(child, {
      enabled: false
    })

    expect(controller.start()).toMatchObject({
      enabled: false,
      process_running: false,
      lifecycle_state: 'disabled',
      execution_authority: false,
      broker_submission_attempted: false
    })
    expect(spawnWorker).not.toHaveBeenCalled()
  })

  it('stops gracefully with SIGTERM', async () => {
    const child = new FakeChild()
    const { controller } = lifecycle(child)

    controller.start()
    const stopping = controller.stop()

    expect(child.signals).toEqual(['SIGTERM'])

    child.emit('close', 0)
    await stopping

    expect(controller.snapshotSync()).toMatchObject({
      process_running: false,
      lifecycle_state: 'stopped'
    })
  })

  it('escalates to SIGKILL after the shutdown timeout', async () => {
    vi.useFakeTimers()

    try {
      const child = new FakeChild()

      const { controller } = lifecycle(child, {
        shutdownTimeoutMs: 25
      })

      controller.start()
      const stopping = controller.stop()

      await vi.advanceTimersByTimeAsync(25)
      await stopping

      expect(child.signals).toEqual(['SIGTERM', 'SIGKILL'])
      expect(controller.snapshotSync().process_running).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('uses the governed stream state file inside the paper runtime', () => {
    const child = new FakeChild()
    const { controller } = lifecycle(child)

    expect(controller.statePath).toBe(path.join('/sigil/state', 'governed-news-stream-state.json'))
  })
})
