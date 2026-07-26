import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { INITIAL_SIGIL_SNAPSHOT, MockSigilOperatorAdapter, SIGIL_FIRST_LAUNCH_LIMIT } from './mock-adapter'

import { SigilOperatorView } from './index'

describe('standalone Sigil Mission Control', () => {
  it('launches directly with Sigil-only product navigation and branding', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    expect(await screen.findByTestId('sigil-operator')).toBeTruthy()
    expect(screen.getByText('Mission control')).toBeTruthy()

    for (const label of ['Overview', 'Proposals', 'Launch', 'Executions', 'Reconciliation', 'Audit', 'Settings']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }

    for (const hermesArea of ['Chat', 'Projects', 'Messaging', 'Artifacts']) {
      expect(screen.queryByText(hermesArea)).toBeNull()
    }
  })

  it('preserves paper safety, masked identity, the fixed cap, and reconciliation evidence', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter('disconnected')} />)

    expect(await screen.findByText('•••• 23F4')).toBeTruthy()
    expect(screen.getAllByText(SIGIL_FIRST_LAUNCH_LIMIT).length).toBeGreaterThan(0)
    expect(screen.getAllByText('DISCONNECTED').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliation' }))
    expect(screen.getByText('Required')).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('confirmation-gates simulated approval and never exposes submission capability', async () => {
    const adapter = new MockSigilOperatorAdapter()
    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Proposals' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enable simulated operator actions' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]!)
    expect(screen.getByText('Confirm simulated approval')).toBeTruthy()
    expect((await adapter.readSnapshot()).proposals[0]?.status).toBe('pending')

    fireEvent.click(screen.getByRole('button', { name: 'Confirm approval' }))
    await waitFor(async () => expect((await adapter.readSnapshot()).proposals[0]?.status).toBe('approved'))
    expect((await adapter.readSnapshot()).auditEvents[0]?.details.broker_submission_attempted).toBe(false)
  })

  it('renders disconnected Hermes intelligence and explains the selected proposal locally', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))

    expect(screen.getByText('Hermes Intelligence')).toBeTruthy()
    expect(screen.getByText('local-disconnected')).toBeTruthy()
    expect(screen.getByText('Analysis only')).toBeTruthy()
    expect(screen.getByText('Never')).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Explain selected proposal' }))

    const analysis = await screen.findByTestId('hermes-analysis')

    expect(analysis.textContent).toContain('Proposal PRP-20260725-0042 is governed and simulated.')
    expect(analysis.textContent).toContain('it cannot be submitted to a broker')
    expect(analysis.textContent).toContain('Execution authorized: no')
    expect(analysis.textContent).toContain('Broker submission available: no')
  })

  it('contains no broker submission method in the adapter contract', () => {
    expect('submitOrder' in new MockSigilOperatorAdapter()).toBe(false)
    expect(INITIAL_SIGIL_SNAPSHOT.environment).toBe('paper')
    expect(INITIAL_SIGIL_SNAPSHOT.simulation).toBe(true)
  })

  it('renders refreshable read-only provider data without credential fields', async () => {
    const original = window.sigilDesktop
    window.sigilDesktop = {
      productName: 'Sigil',
      persistenceNamespace: 'test',
      brokerSubmissionAvailable: false,
      getBackendStatus: async () => ({
        ok: false,
        error: 'unused',
        message: 'unused'
      }),
      explainProposal: async () => ({
        ok: false,
        error: 'unused',
        message: 'unused'
      }),
      getProviderSnapshot: async () => ({
        ok: true,
        result: {
          checked_at: '2026-07-26T16:00:00Z',
          broker_submission_available: false,
          credentials_exposed: false,
          alpaca: {
            status: 'connected',
            message: 'Read-only market data is current.',
            symbols: [
              {
                symbol: 'MSFT',
                price: '452.80',
                observed_at: '2026-07-26T15:59:00Z',
                source: 'Alpaca IEX latest bar'
              }
            ]
          },
          public: {
            status: 'connected',
            message: 'Read-only account access is current.',
            accounts: [
              {
                masked_account_id: '•••• 1234',
                cash: '1250.00',
                portfolio_value: '1500.00',
                positions: [{ symbol: 'AAPL', quantity: '2' }]
              }
            ]
          }
        }
      })
    }

    try {
      render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

      expect(await screen.findByText('Read-only provider health')).toBeTruthy()
      expect(screen.getAllByText('MSFT').length).toBeGreaterThan(0)
      expect(screen.getByText('•••• 1234')).toBeTruthy()
      expect(screen.getByText(/secrets exposed: no/i)).toBeTruthy()
      expect(screen.queryByText(/alpaca-secret|public-secret/i)).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: /Refresh providers/i }))
      await waitFor(() => expect(screen.getByText('Read-only market data is current.')).toBeTruthy())
    } finally {
      window.sigilDesktop = original
    }
  })

  it('confirmation-gates local paper automation controls', async () => {
    const adapter = new MockSigilOperatorAdapter() as MockSigilOperatorAdapter & {
      controlPaperCycle: (action: 'start' | 'pause' | 'stop') => Promise<typeof INITIAL_SIGIL_SNAPSHOT>
    }

    adapter.controlPaperCycle = async action => ({
      ...INITIAL_SIGIL_SNAPSHOT,
      automationState: action === 'start' ? 'running' : action === 'pause' ? 'paused' : 'stopped',
      automationCycleCount: 1
    })

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(screen.getByRole('button', { name: 'Start' }))

    expect(screen.getByText('Confirm paper automation start')).toBeTruthy()
    expect(screen.getByText(/cannot submit to a broker/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Start paper automation' }))
    expect(await screen.findByText('Paper automation running')).toBeTruthy()
  })
})
