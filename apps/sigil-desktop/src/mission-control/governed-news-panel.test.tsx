import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GovernedNewsPanel } from './governed-news-panel'

type JsonObject = Record<string, unknown>

type NewsDesktopApi = {
  getGovernedNewsStatus: ReturnType<typeof vi.fn<() => Promise<JsonObject>>>
  getGovernedNewsTimeline: ReturnType<
    typeof vi.fn<(symbol: string) => Promise<JsonObject>>
  >
  getGovernedNewsAdvisorySummary: ReturnType<
    typeof vi.fn<() => Promise<JsonObject>>
  >
  collectGovernedAlpacaNews: ReturnType<
    typeof vi.fn<(symbols: string[]) => Promise<JsonObject>>
  >
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void

  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })

  return { promise, reject, resolve }
}

function response(result: JsonObject = {}): JsonObject {
  return { ok: true, result }
}

function bridge(): NewsDesktopApi {
  return {
    getGovernedNewsStatus: vi.fn(async () =>
      response({ headline_count: 0 })
    ),
    getGovernedNewsTimeline: vi.fn(async () =>
      response({ headlines: [] })
    ),
    getGovernedNewsAdvisorySummary: vi.fn(async () => response({})),
    collectGovernedAlpacaNews: vi.fn(async () =>
      response({
        status: 'finished',
        stored_count: 0,
        duplicate_count: 0,
        rejected_count: 0
      })
    )
  }
}

function installBridge(api: NewsDesktopApi): void {
  Object.defineProperty(window, 'sigilDesktop', {
    configurable: true,
    value: api
  })
}

async function renderPanel(api: NewsDesktopApi): Promise<void> {
  installBridge(api)
  render(<GovernedNewsPanel />)

  await screen.findByText('Governed evidence refreshed.')
}

afterEach(() => {
  cleanup()
  Reflect.deleteProperty(window, 'sigilDesktop')
})

describe('GovernedNewsPanel single-flight actions', () => {
  it('admits only one collection from same-render duplicate clicks', async () => {
    const api = bridge()
    await renderPanel(api)

    const collection = deferred<JsonObject>()
    api.collectGovernedAlpacaNews.mockReturnValueOnce(collection.promise)

    const collect = screen.getByRole('button', {
      name: 'Scan governed universe'
    })

    act(() => {
      collect.click()
      collect.click()
    })

    expect(api.collectGovernedAlpacaNews).toHaveBeenCalledOnce()

    collection.resolve(
      response({
        status: 'finished',
        stored_count: 1
      })
    )

    await waitFor(() => {
      expect((collect as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('admits only one refresh from same-render duplicate clicks', async () => {
    const api = bridge()
    await renderPanel(api)

    const status = deferred<JsonObject>()
    const advisory = deferred<JsonObject>()
    const statusCalls = api.getGovernedNewsStatus.mock.calls.length
    const advisoryCalls =
      api.getGovernedNewsAdvisorySummary.mock.calls.length

    api.getGovernedNewsStatus.mockReturnValueOnce(status.promise)
    api.getGovernedNewsAdvisorySummary.mockReturnValueOnce(
      advisory.promise
    )

    const refresh = screen.getByRole('button', {
      name: 'Refresh evidence'
    })

    act(() => {
      refresh.click()
      refresh.click()
    })

    expect(api.getGovernedNewsStatus).toHaveBeenCalledTimes(
      statusCalls + 1
    )
    expect(api.getGovernedNewsAdvisorySummary).toHaveBeenCalledTimes(
      advisoryCalls + 1
    )

    status.resolve(response({ headline_count: 1 }))
    advisory.resolve(response({}))

    await waitFor(() => {
      expect((refresh as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('blocks refresh while collection owns the action lock', async () => {
    const api = bridge()
    await renderPanel(api)

    const collection = deferred<JsonObject>()
    const statusCalls = api.getGovernedNewsStatus.mock.calls.length

    api.collectGovernedAlpacaNews.mockReturnValueOnce(collection.promise)

    const collect = screen.getByRole('button', {
      name: 'Scan governed universe'
    })
    const refresh = screen.getByRole('button', {
      name: 'Refresh evidence'
    })

    act(() => {
      collect.click()
      refresh.click()
    })

    expect(api.collectGovernedAlpacaNews).toHaveBeenCalledOnce()
    expect(api.getGovernedNewsStatus).toHaveBeenCalledTimes(statusCalls)

    collection.resolve(
      response({
        status: 'finished',
        stored_count: 0
      })
    )

    await waitFor(() => {
      expect(api.getGovernedNewsStatus).toHaveBeenCalledTimes(
        statusCalls + 1
      )
    })
  })

  it('releases the action lock after failure so collection can retry', async () => {
    const api = bridge()
    await renderPanel(api)

    api.collectGovernedAlpacaNews.mockRejectedValueOnce(
      new Error('collection failed')
    )

    const collect = screen.getByRole('button', {
      name: 'Scan governed universe'
    })

    fireEvent.click(collect)
    await screen.findByText('collection failed')

    fireEvent.click(collect)

    await waitFor(() => {
      expect(api.collectGovernedAlpacaNews).toHaveBeenCalledTimes(2)
    })
  })
})
