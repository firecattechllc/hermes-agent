import { fireEvent, render, screen, waitFor } from '@testing-library/react'

import { INITIAL_SIGIL_SNAPSHOT, MockSigilOperatorAdapter, SIGIL_FIRST_LAUNCH_LIMIT } from './mock-adapter'

import { SigilOperatorView } from './index'

describe('standalone Sigil Mission Control', () => {
  it('launches directly with Sigil-only product navigation and branding', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)

    expect(await screen.findByTestId('sigil-operator')).toBeTruthy()
    expect(screen.getByText('Mission control')).toBeTruthy()

    for (const label of ['Overview', 'Portfolio', 'Proposals', 'Launch', 'Executions', 'Reconciliation', 'Audit', 'Settings']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }

    for (const hermesArea of ['Chat', 'Projects', 'Messaging', 'Artifacts']) {
      expect(screen.queryByText(hermesArea)).toBeNull()
    }
  })

  it('preserves paper safety, masked identity, dynamic sizing, and reconciliation evidence', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter('disconnected')} />)

    expect(await screen.findByText('•••• 23F4')).toBeTruthy()
    expect(screen.getAllByText(SIGIL_FIRST_LAUNCH_LIMIT).length).toBeGreaterThan(0)
    expect(screen.getAllByText('DISCONNECTED').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Reconciliation' }))
    expect(screen.getByText('Required')).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
  })

  it('shows monthly paper authorization and never exposes submission capability', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    expect(screen.getByText('Paper month 2026-07 · active')).toBeTruthy()
    expect(screen.getByText(/calendar month started automatically authorized/)).toBeTruthy()
    expect(screen.queryByText(/submit order/i)).toBeNull()
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
            universe: {
              scope: '12 explicitly defined U.S.-listed demonstration equities',
              total: 12,
              available: 1,
              unavailable: 11,
              catalog_source: 'Sigil bounded demonstration universe',
              catalog_freshness: 'Static local definition; provider catalog unverified',
              iex_status: 'real-time',
              broader_us_status: '15-minute delayed historical data available; catalog unverified',
              criteria: 'Latest IEX quote availability',
              whole_market_coverage: false,
              catalog_access: 'unavailable_current_credentials',
              coverage_limitation: 'Full U.S. listing enumeration is unavailable with current credentials.',
              refresh_policy: 'One batched read-only request every 30 seconds.'
            },
            symbols: [
              {
                symbol: 'MSFT',
                name: 'Microsoft Corporation',
                sector: 'Information Technology',
                price: '452.80',
                observed_at: '2026-07-26T15:59:00Z',
                daily_change_percent: '1.20',
                screen_status: 'available',
                source: 'Alpaca IEX snapshot'
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
      expect(screen.getByText('U.S.-listed paper screening universe')).toBeTruthy()
      expect(screen.getByText(/Alpaca IEX · real-time/)).toBeTruthy()
      expect(screen.getByText(/Broader U.S. data · 15-minute delayed/)).toBeTruthy()
      expect(screen.getByText(/Coverage 1\/12 symbols/)).toBeTruthy()
      expect(screen.getByText(/Every active U.S. stock is not claimed as watched/)).toBeTruthy()
      expect(screen.getByText(/Full U.S. listing enumeration is unavailable/)).toBeTruthy()
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
    const adapter = new MockSigilOperatorAdapter()

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(
      screen.getByRole('button', { name: 'Start' }),
    )

    expect(
      screen.queryByText('Confirm paper automation start'),
    ).toBeNull()

    await waitFor(() => {
      expect(screen.getByText(/paper automation running/i)).toBeTruthy()
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Pause' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/paper automation paused/i)).toBeTruthy()
    })

    fireEvent.click(
      screen.getByRole('button', { name: 'Stop' }),
    )

    await waitFor(() => {
      expect(screen.getByText(/paper automation stopped/i)).toBeTruthy()
    })
  })
  it('refreshes visible portfolio values when backend state changes', async () => {
    const adapter = new MockSigilOperatorAdapter()
    const initialRead = adapter.readSnapshot.bind(adapter)
    let reads = 0

    adapter.readSnapshot = async () => {
      const snapshot = await initialRead()
      reads += 1

      return reads === 1
        ? snapshot
        : {
            ...snapshot,
            cash: '$9,500.00',
            totalAccountValue: '$10,900.00',
            lastUpdated: '2026-07-26T20:00:05Z'
          }
    }

    render(<SigilOperatorView adapter={adapter} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(screen.getByRole('button', { name: 'Portfolio' }))
    expect(screen.getAllByText('$10,000.00').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Refresh runtime' }))

    expect(await screen.findByText('$9,500.00')).toBeTruthy()
    expect(screen.getByText('$10,900.00')).toBeTruthy()
    expect(screen.getAllByText(/2026-07-26T20:00:05Z/).length).toBeGreaterThan(0)
  })

  it('shows paper holdings, buy and sell activity, and evidence links', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')

    fireEvent.click(screen.getByRole('button', { name: 'Portfolio' }))

    expect(screen.getByText('Current simulated holdings')).toBeTruthy()
    expect(screen.getAllByText('MSFT').length).toBeGreaterThan(0)
    expect(screen.getByText('$834.16')).toBeTruthy()
    expect(screen.getByText('REC-20260725-018')).toBeTruthy()
    expect(screen.getAllByText('BUY').length).toBeGreaterThan(0)
    expect(screen.getAllByText('SELL').length).toBeGreaterThan(0)
    expect(screen.getAllByText('PAPER').length).toBeGreaterThan(0)
    expect(screen.getAllByText('SIMULATED').length).toBeGreaterThan(0)
  })

  it('confirmation-gates the local-only paper reset', async () => {
    render(<SigilOperatorView adapter={new MockSigilOperatorAdapter()} />)
    await screen.findByTestId('sigil-operator')
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset local paper portfolio' }))

    expect(screen.getByText('Confirm local paper portfolio reset')).toBeTruthy()
    expect(screen.getByText(/settings, local provider credentials, source files/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reset empty paper ledger' }))

    expect(await screen.findByText('No paper holdings')).toBeTruthy()
  })
})
