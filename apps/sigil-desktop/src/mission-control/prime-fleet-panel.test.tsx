import { act, render, screen, waitFor } from '@testing-library/react'

import { PrimeFleetPanel } from './prime-fleet-panel'
import type { PrimeFleetStatus, PrimeSigilRouteResult } from './types'

function mockDesktop(overrides: Partial<Window['sigilDesktop']>) {
  window.sigilDesktop = {
    productName: 'Sigil',
    persistenceNamespace: 'com.firecattechnology.sigil',
    brokerSubmissionAvailable: false,
    getBackendStatus: async () => ({ ok: true, result: {} as never }),
    explainProposal: async () => ({ ok: true, result: {} as never }),
    ...overrides
  } as Window['sigilDesktop']
}

afterEach(() => {
  delete (window as { sigilDesktop?: unknown }).sigilDesktop
})

describe('Prime Fleet panel', () => {
  it('shows an honest not-configured empty state, never a fake healthy fleet', async () => {
    const status: PrimeFleetStatus = {
      configured: false,
      reachable: false,
      base_url: null,
      nodes: [],
      certification: { status: 'unknown', evidence_ref: null }
    }

    mockDesktop({ getPrimeFleetStatus: async () => ({ ok: true, result: status }) })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText('Prime not configured')).toBeTruthy()
    expect(screen.queryByText('connected')).toBeNull()
  })

  it('shows an unreachable error state when Prime is configured but not responding', async () => {
    const status: PrimeFleetStatus = {
      configured: true,
      reachable: false,
      base_url: 'http://100.119.205.44:8743',
      nodes: [],
      certification: { status: 'unknown', evidence_ref: null }
    }

    mockDesktop({ getPrimeFleetStatus: async () => ({ ok: true, result: status }) })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText(/did not respond/)).toBeTruthy()
  })

  it('renders real node identity, connection state, and certification status', async () => {
    const status: PrimeFleetStatus = {
      configured: true,
      reachable: true,
      base_url: 'http://100.119.205.44:8743',
      nodes: [
        {
          natural_key: 'titan',
          role: 'titan',
          connection_state: 'connected',
          last_seen_at: Math.floor(Date.now() / 1000),
          model_inventory: ['qwen3:0.6b']
        },
        {
          natural_key: 'mac',
          role: 'mac',
          connection_state: 'stale',
          last_seen_at: null,
          model_inventory: []
        }
      ],
      certification: { status: 'certified', evidence_ref: 'evidence://x' }
    }

    mockDesktop({ getPrimeFleetStatus: async () => ({ ok: true, result: status }) })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText('titan')).toBeTruthy()
    expect(screen.getByText('mac')).toBeTruthy()
    expect(screen.getByText('connected')).toBeTruthy()
    expect(screen.getByText('stale')).toBeTruthy()
    expect(screen.getByText('certified')).toBeTruthy()
    expect(screen.getByText(/none reported/)).toBeTruthy()
  })

  it('never renders a stale node as connected', async () => {
    const status: PrimeFleetStatus = {
      configured: true,
      reachable: true,
      base_url: 'http://100.119.205.44:8743',
      nodes: [
        { natural_key: 'titan', role: 'titan', connection_state: 'stale', last_seen_at: 1, model_inventory: [] }
      ],
      certification: { status: 'not_certified', evidence_ref: null }
    }

    mockDesktop({ getPrimeFleetStatus: async () => ({ ok: true, result: status }) })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText('stale')).toBeTruthy()
    expect(screen.queryByText('connected')).toBeNull()
  })

  it('sends a governed route test request and renders Prime\'s real accepted result', async () => {
    const status: PrimeFleetStatus = {
      configured: true,
      reachable: true,
      base_url: 'http://100.119.205.44:8743',
      nodes: [
        { natural_key: 'titan', role: 'titan', connection_state: 'connected', last_seen_at: 1, model_inventory: [] }
      ],
      certification: { status: 'certified', evidence_ref: 'evidence://x' }
    }

    const routeResult: PrimeSigilRouteResult = {
      ok: true,
      outcome: 'accepted',
      advisory_output: { routed_to: 'titan', model_alias: 'sentiment' }
    }

    mockDesktop({
      getPrimeFleetStatus: async () => ({ ok: true, result: status }),
      primeSigilRoute: async () => ({ ok: true, result: routeResult })
    })

    render(<PrimeFleetPanel />)
    await screen.findByText('Send harmless test request')

    const button = screen.getByRole('button', { name: 'Send harmless test request' })
    await act(async () => {
      button.click()
    })

    await waitFor(() => expect(screen.getByText(/Accepted/)).toBeTruthy())
    expect(screen.getAllByText('titan').length).toBeGreaterThan(0)
    expect(screen.getByText('sentiment')).toBeTruthy()
  })

  it('renders a real rejection reason rather than hiding the failure', async () => {
    const status: PrimeFleetStatus = {
      configured: true,
      reachable: true,
      base_url: 'http://100.119.205.44:8743',
      nodes: [],
      certification: { status: 'not_certified', evidence_ref: null }
    }

    const routeResult: PrimeSigilRouteResult = {
      ok: false,
      outcome: 'rejected',
      rejection_code: 'caller_not_admitted'
    }

    mockDesktop({
      getPrimeFleetStatus: async () => ({ ok: true, result: status }),
      primeSigilRoute: async () => ({ ok: true, result: routeResult })
    })

    render(<PrimeFleetPanel />)
    const button = await screen.findByRole('button', { name: 'Send harmless test request' })
    await act(async () => {
      button.click()
    })

    await waitFor(() => expect(screen.getByText(/Rejected — caller_not_admitted/)).toBeTruthy())
  })

  it('shows a bounded error banner when the bridge call itself fails', async () => {
    mockDesktop({ getPrimeFleetStatus: async () => ({ ok: false, error: 'bridge_error', message: 'boom' }) })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText('boom')).toBeTruthy()
  })

  it('degrades gracefully when the bridge API is entirely unavailable', async () => {
    mockDesktop({ getPrimeFleetStatus: undefined })

    render(<PrimeFleetPanel />)

    expect(await screen.findByText(/unavailable in this build/)).toBeTruthy()
  })
})
