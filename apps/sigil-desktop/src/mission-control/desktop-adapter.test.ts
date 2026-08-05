import { describe, expect, it } from 'vitest'

import { runtimeDataState, runtimeHealthLabel } from './desktop-adapter'

describe('runtimeDataState', () => {
  it('does not report data ready when market-data authentication fails', () => {
    expect(
      runtimeDataState({ status: 'connected', market_data_status: 'authentication_failed' })
    ).toBe('stale')
  })

  it('reports ready only after the evidence provider is ready', () => {
    expect(runtimeDataState({ status: 'connected', market_data_status: 'ready' })).toBe('ready')
  })
})

describe('runtimeHealthLabel', () => {
  it('reports healthy only for a connected healthy runtime', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' },
        runtime_health: 'healthy'
      })
    ).toBe('Runtime healthy')
  })

  it('reports degraded runtime health', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' },
        runtime_health: 'degraded'
      })
    ).toBe('Runtime degraded')
  })

  it('reports when recovery is required', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' },
        runtime_health: 'recovery_required'
      })
    ).toBe('Recovery required')
  })

  it('reports detected runtime corruption', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' },
        runtime_health: 'corrupt'
      })
    ).toBe('Runtime corruption detected')
  })

  it('reports a locked runtime as unavailable', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' },
        runtime_health: 'locked'
      })
    ).toBe('Runtime unavailable')
  })

  it('fails closed when runtime health is missing', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'connected' }
      })
    ).toBe('Runtime health unknown')
  })

  it('prioritizes a disconnected transport over runtime health', () => {
    expect(
      runtimeHealthLabel({
        connection: { status: 'disconnected' },
        runtime_health: 'healthy'
      })
    ).toBe('Runtime disconnected')
  })
})
