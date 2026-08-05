import { expect, it, vi } from 'vitest'

import {
  enableGovernedPaperExecutionByDefault,
  type PaperExecutionStartupStatus
} from './paper-execution-startup'

function status(
  changes: Partial<PaperExecutionStartupStatus> = {}
): PaperExecutionStartupStatus {
  return {
    environment: 'paper',
    live_execution: false,
    broker: 'alpaca_paper',
    broker_base_url: 'https://paper-api.alpaca.markets',
    broker_submission: false,
    activated: false,
    paused: false,
    kill_switch: true,
    ...changes
  }
}

it('activates governed paper execution automatically when startup finds it disabled', async () => {
  const runner = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, result: status() })
    .mockResolvedValueOnce({
      ok: true,
      result: status({
        broker_submission: true,
        activated: true,
        kill_switch: false
      })
    })

  const result = await enableGovernedPaperExecutionByDefault(runner)

  expect(result.ok).toBe(true)
  expect(runner.mock.calls).toEqual([
    [{ command: 'paper_execution_status' }],
    [{ command: 'paper_execution_activate' }]
  ])
})

it('revalidates and reconciles an already active runtime on startup', async () => {
  const active = status({
    broker_submission: true,
    activated: true,
    kill_switch: false
  })

  const runner = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, result: active })
    .mockResolvedValueOnce({ ok: true, result: active })

  const result = await enableGovernedPaperExecutionByDefault(runner)

  expect(result.ok).toBe(true)
  expect(runner.mock.calls).toEqual([
    [{ command: 'paper_execution_status' }],
    [{ command: 'paper_execution_activate' }]
  ])
})

it('fails closed without activation when the execution identity is not paper-only', async () => {
  const runner = vi.fn().mockResolvedValue({
    ok: true,
    result: status({
      live_execution: true,
      broker_base_url: 'https://api.alpaca.markets'
    })
  })

  const result = await enableGovernedPaperExecutionByDefault(runner)

  expect(result).toEqual({
    ok: false,
    error: 'unsafe_paper_execution_identity',
    message:
      'Governed paper execution did not report the required paper-only identity.'
  })
  expect(runner).toHaveBeenCalledOnce()
})

it('leaves execution disabled when governed activation is rejected', async () => {
  const runner = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, result: status() })
    .mockResolvedValueOnce({
      ok: false,
      error: 'backend_error',
      message: 'paper execution activation requires completed shadow promotion readiness'
    })

  const result = await enableGovernedPaperExecutionByDefault(runner)

  expect(result).toEqual({
    ok: false,
    error: 'backend_error',
    message: 'paper execution activation requires completed shadow promotion readiness'
  })
})

it('restores the kill switch when persisted authority fails startup revalidation', async () => {
  const runner = vi
    .fn()
    .mockResolvedValueOnce({
      ok: true,
      result: status({
        broker_submission: true,
        activated: true,
        kill_switch: false
      })
    })
    .mockResolvedValueOnce({
      ok: false,
      error: 'backend_error',
      message: 'paper account authentication failed'
    })
    .mockResolvedValueOnce({ ok: true, result: status() })

  const result = await enableGovernedPaperExecutionByDefault(runner)

  expect(result.ok).toBe(false)
  expect(runner.mock.calls).toEqual([
    [{ command: 'paper_execution_status' }],
    [{ command: 'paper_execution_activate' }],
    [{ command: 'paper_execution_deactivate' }]
  ])
})
